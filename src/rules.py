#!/usr/bin/env python3
"""
rules.py — S1-3 规则引擎

输入：对齐结果 JSON（s1-2_alignment.json）+ 词级转写 JSON（s1-1_transcript_base.json）
输出：规则决策结果 JSON（每个区间的 keep/reason/decided_by）

规则（执行顺序）：
  R1. 首尾空镜剔除：第一个词前、最后一个词后的所有时间留 300ms 呼吸垫，其余剔除。
      特别注意：154.6s 处已知的 8.2s 关机空档必须被剪掉。
  R2. 重复区间：直接采用对齐结果中的 keep 标记（保留最后一次）。
  R3. 停顿剪除：词间隔 > PAUSE_THRESHOLD_S 的部分剪掉，保留两侧各 PAD_IN_MS/PAD_OUT_MS 呼吸垫。
  R4. 语气词剔除：白名单词汇，仅当该词独立成段（前后都有停顿 > FILLER_PAUSE_S）时剔除。
  R5. 脱稿区间：默认保留，仅对其做 R3/R4 清理。

用法：
  python3 src/rules.py \\
      --alignment eval/s1-2_alignment.json \\
      --transcript eval/s1-1_transcript_base.json \\
      --out output/s1_rules.json
"""

import argparse
import json
import os
import sys
from typing import List, Tuple, Dict, Any

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG — 所有可调参数集中在此
# ═══════════════════════════════════════════════════════════════════════════════
CONFIG = {
    # ── R1 首尾空镜 ──────────────────────────────────────────────────────────
    # 第一个词前保留的呼吸垫（秒）
    "HEAD_PAD_S": 0.3,
    # 最后一个词后保留的呼吸垫（秒）
    "TAIL_PAD_S": 0.3,

    # ── R3 停顿剪除 ──────────────────────────────────────────────────────────
    # 词间隔超过此值（秒）视为需要剪除的停顿
    "PAUSE_THRESHOLD_S": 0.8,
    # 停顿切点两侧保留的呼吸垫（秒）
    "PAD_IN_MS": 0.150,   # 停顿前（该词结束后）保留
    "PAD_OUT_MS": 0.150,  # 停顿后（下词开始前）保留

    # ── R4 语气词 ────────────────────────────────────────────────────────────
    # 语气词白名单（归一化后匹配）
    "FILLER_WORDS": ["啊", "嗯", "呃", "这个", "那个", "就是说", "就是", "然后", "对"],
    # 语气词前后停顿阈值（秒）：前后都大于此值才算独立成段可剔除
    "FILLER_PAUSE_S": 0.3,

    # ── 其他 ─────────────────────────────────────────────────────────────────
    # 最小区间时长（秒）：短于此值的区间直接跳过（避免零长度片段）
    "MIN_SEGMENT_S": 0.05,
}
# ═══════════════════════════════════════════════════════════════════════════════


def fmt_tc(s: float) -> str:
    """秒 → HH:MM:SS.mmm"""
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:06.3f}"


def load_words(transcript_path: str) -> List[Dict]:
    with open(transcript_path, encoding="utf-8") as f:
        data = json.load(f)
    return data["words"]


def load_alignment(alignment_path: str) -> Dict:
    with open(alignment_path, encoding="utf-8") as f:
        return json.load(f)


def word_text_normalized(w: Dict) -> str:
    """提取词文本并去除空格、标点，仅保留汉字/字母/数字"""
    import re
    text = w["word"].strip()
    text = re.sub(r"[^\w一-鿿]", "", text, flags=re.UNICODE)
    return text


def is_filler(word_text: str, fillers: List[str]) -> bool:
    return word_text in fillers


def get_gap_before(words: List[Dict], idx: int) -> float:
    """第 idx 个词与前一个词之间的间隔（秒）；第 0 个词返回 inf"""
    if idx == 0:
        return float("inf")
    return words[idx]["start"] - words[idx - 1]["end"]


def get_gap_after(words: List[Dict], idx: int) -> float:
    """第 idx 个词与后一个词之间的间隔（秒）；最后一个词返回 inf"""
    if idx >= len(words) - 1:
        return float("inf")
    return words[idx + 1]["start"] - words[idx]["end"]


# ─── 核心：把一组词（连续区间）转换为保留子区间列表 ───────────────────────────

def apply_rules_to_word_range(
    words: List[Dict],
    w_start: int,
    w_end: int,  # inclusive
    region_start_s: float,
    region_end_s: float,
    cfg: Dict,
    region_label: str = "",
) -> List[Dict]:
    """
    对 words[w_start..w_end] 应用 R3（停顿剪除）和 R4（语气词剔除），
    返回保留的子区间列表，每项格式：
      {start_s, end_s, reason}

    region_start_s / region_end_s：该区间在原视频中的时间边界
    （用于 clamp，确保不超出对齐区间的边界）。
    """
    if w_start > w_end:
        return []

    pause_th = cfg["PAUSE_THRESHOLD_S"]
    pad_in = cfg["PAD_IN_MS"]
    pad_out = cfg["PAD_OUT_MS"]
    filler_pause = cfg["FILLER_PAUSE_S"]
    fillers = cfg["FILLER_WORDS"]
    min_seg = cfg["MIN_SEGMENT_S"]

    # 先标记每个词：keep / filler_remove / pause_boundary
    # 构建"切割点"列表：每个切割点是词间隔 > pause_th 的位置
    # 再对每个连续保留段的边界做 pad 处理

    # Step 1: 找所有停顿切割位置（词索引对，表示在 idx 和 idx+1 之间有停顿）
    pause_cuts: List[Tuple[int, int]] = []  # (left_word_idx, right_word_idx)
    for i in range(w_start, w_end):
        gap = words[i + 1]["start"] - words[i]["end"]
        if gap > pause_th:
            pause_cuts.append((i, i + 1))

    # Step 2: 把区间切成若干子段（由 pause_cuts 分隔）
    # 每个子段：[seg_w_start, seg_w_end]（词索引，inclusive）
    # 每个子段的时间边界：
    #   start_s = words[seg_w_start]["start"] - pad_out（前呼吸垫，但不能超出上段的 end+pad_in）
    #   end_s   = words[seg_w_end]["end"] + pad_in（后呼吸垫）
    boundaries = [w_start] + [r for _, r in pause_cuts] + [w_end + 1]
    # boundaries[i]..boundaries[i+1]-1 是第 i 个子段的词范围

    sub_segs = []
    for k in range(len(boundaries) - 1):
        seg_ws = boundaries[k]
        seg_we = boundaries[k + 1] - 1

        if seg_ws > seg_we:
            continue

        # 计算时间边界（加呼吸垫）
        seg_start = words[seg_ws]["start"] - pad_out
        seg_end = words[seg_we]["end"] + pad_in

        # 对第一个子段：start 不能小于 region_start_s
        if k == 0:
            seg_start = max(seg_start, region_start_s)
        # 对最后一个子段：end 不能大于 region_end_s
        if k == len(boundaries) - 2:
            seg_end = min(seg_end, region_end_s)

        # 确保不越界
        seg_start = max(seg_start, region_start_s)
        seg_end = min(seg_end, region_end_s)

        sub_segs.append({
            "start_s": seg_start,
            "end_s": seg_end,
            "w_start": seg_ws,
            "w_end": seg_we,
        })

    if not sub_segs:
        return []

    # Step 3: 对每个子段，做语气词剔除（R4）
    # 语气词：该词在子段首/尾且前后停顿 > filler_pause_s 时剔除
    result = []
    for seg in sub_segs:
        seg_ws = seg["w_start"]
        seg_we = seg["w_end"]
        seg_start = seg["start_s"]
        seg_end = seg["end_s"]

        # 从头剔除语气词
        while seg_ws <= seg_we:
            wtxt = word_text_normalized(words[seg_ws])
            if not is_filler(wtxt, fillers):
                break
            # 检查该词前后停顿（在全局词序中）
            gap_before = get_gap_before(words, seg_ws)
            gap_after = get_gap_after(words, seg_ws)
            if gap_before >= filler_pause and gap_after >= filler_pause:
                # 剔除该语气词：把 seg_start 推后
                new_start = words[seg_ws]["end"] + pad_out
                seg_start = min(new_start, seg_end)
                seg_ws += 1
            else:
                break

        # 从尾剔除语气词
        while seg_we >= seg_ws:
            wtxt = word_text_normalized(words[seg_we])
            if not is_filler(wtxt, fillers):
                break
            gap_before = get_gap_before(words, seg_we)
            gap_after = get_gap_after(words, seg_we)
            if gap_before >= filler_pause and gap_after >= filler_pause:
                new_end = words[seg_we]["start"] - pad_in
                seg_end = max(new_end, seg_start)
                seg_we -= 1
            else:
                break

        duration = seg_end - seg_start
        if duration < min_seg:
            continue

        result.append({
            "start_s": round(seg_start, 3),
            "end_s": round(seg_end, 3),
            "reason": f"rule_keep:{region_label}",
        })

    return result


# ─── 主逻辑 ──────────────────────────────────────────────────────────────────

def apply_rules(alignment: Dict, words: List[Dict], cfg: Dict) -> List[Dict]:
    """
    综合应用所有规则，返回 EDL segments 列表（每项含 keep/reason/decided_by 等字段）。

    Returns: sorted list of segment dicts
    """
    min_seg = cfg["MIN_SEGMENT_S"]

    # 所有词的全局首尾
    first_word_start = words[0]["start"] if words else 0.0
    last_word_end = words[-1]["end"] if words else 0.0

    # R1: 首尾空镜边界
    head_cut_end = max(0.0, first_word_start - cfg["HEAD_PAD_S"])
    tail_cut_start = last_word_end + cfg["TAIL_PAD_S"]
    video_start = 0.0
    video_end = last_word_end + cfg["TAIL_PAD_S"]  # 视频有效终点（片尾空档后不保留）

    print(f"[rules] 素材词级范围: {first_word_start:.3f}s ~ {last_word_end:.3f}s", file=sys.stderr)
    print(f"[rules] R1 首尾空镜: 剔除 [0, {head_cut_end:.3f}s] 和 [{tail_cut_start:.3f}s, end]", file=sys.stderr)

    # 收集所有"原始保留区间"（来自对齐结果）
    # 格式：{start_s, end_s, type, script_line, text, word_start_idx, word_end_idx, keep_from_align}
    raw_intervals = []

    # 1. 句子区间（matched）
    for sent in alignment["sentences"]:
        for iv in sent["intervals"]:
            keep_from_align = iv.get("keep", True)
            raw_intervals.append({
                "start_s": iv["start_s"],
                "end_s": iv["end_s"],
                "type": "sentence",
                "script_line": sent["id"],
                "text": iv.get("transcript_text", ""),
                "word_start_idx": iv["word_start_idx"],
                "word_end_idx": iv["word_end_idx"],
                "keep_from_align": keep_from_align,
                "align_reason": iv.get("reason", "matched"),
            })

    # 2. 脱稿区间（adlib）
    for adl in alignment.get("adlib_intervals", []):
        raw_intervals.append({
            "start_s": adl["start_s"],
            "end_s": adl["end_s"],
            "type": "adlib",
            "script_line": None,
            "text": adl.get("transcript_text", ""),
            "word_start_idx": adl["word_start_idx"],
            "word_end_idx": adl["word_end_idx"],
            "keep_from_align": True,  # 脱稿默认保留
            "align_reason": "adlib",
        })

    # 按时间排序
    raw_intervals.sort(key=lambda x: x["start_s"])

    # ── 预处理：解决重叠区间 ──────────────────────────────────────────────────
    # 若两个区间有重叠（end_s > next start_s），把前者的 end_s clamp 到 next start_s
    # 优先保留 keep_from_align=True 的区间
    merged_raw = []
    cursor_end = 0.0
    for raw in raw_intervals:
        if raw["start_s"] < cursor_end - 0.01:
            # 重叠：把该区间起点推后到 cursor_end
            overlap_push = cursor_end - raw["start_s"]
            if overlap_push < (raw["end_s"] - raw["start_s"]) - min_seg:
                raw = dict(raw)
                raw["start_s"] = cursor_end
            else:
                # 区间几乎完全被前一个覆盖，跳过
                print(f"[rules] 跳过被覆盖区间: {raw['start_s']:.2f}-{raw['end_s']:.2f}s",
                      file=sys.stderr)
                continue
        merged_raw.append(raw)
        cursor_end = max(cursor_end, raw["end_s"])

    raw_intervals = merged_raw

    # 生成最终 EDL segments
    seg_id = 0
    edl_segments = []

    # R1: 首部空镜段（keep=False）
    if head_cut_end > min_seg:
        edl_segments.append({
            "id": seg_id,
            "keep": False,
            "start_s": 0.0,
            "end_s": head_cut_end,
            "start": fmt_tc(0.0),
            "end": fmt_tc(head_cut_end),
            "text": "[首部空镜]",
            "script_line": None,
            "pad_in_ms": 0,
            "pad_out_ms": int(cfg["HEAD_PAD_S"] * 1000),
            "reason": "head_blank",
            "decided_by": "rule",
        })
        seg_id += 1

    prev_keep_end = head_cut_end  # 跟踪上一个保留区间的结束时间，用于检测区间间大停顿

    for raw in raw_intervals:
        ws = raw["word_start_idx"]
        we = raw["word_end_idx"]
        r_start = raw["start_s"]
        r_end = raw["end_s"]
        keep_from_align = raw["keep_from_align"]
        region_type = raw["type"]

        # ── 区间之间的大停顿检测（R3 跨区间版）──────────────────────────────
        # 若本区间与上一保留区间之间的间隔 > PAUSE_THRESHOLD_S，
        # 显式插入一个 DROP 片段（覆盖该停顿区域），然后把本区间起点推到停顿结束后
        gap_before_interval = r_start - prev_keep_end
        if gap_before_interval > cfg["PAUSE_THRESHOLD_S"] and keep_from_align:
            # 停顿段：从 prev_keep_end+pad_in 到 r_start-pad_out
            pause_drop_start = prev_keep_end + cfg["PAD_IN_MS"]
            pause_drop_end = r_start - cfg["PAD_OUT_MS"]
            if pause_drop_end - pause_drop_start > min_seg:
                print(f"[rules] 跨区间停顿 {gap_before_interval:.2f}s @ "
                      f"[{prev_keep_end:.2f}s→{r_start:.2f}s] → DROP",
                      file=sys.stderr)
                edl_segments.append({
                    "id": seg_id,
                    "keep": False,
                    "start_s": round(pause_drop_start, 3),
                    "end_s": round(pause_drop_end, 3),
                    "start": fmt_tc(pause_drop_start),
                    "end": fmt_tc(pause_drop_end),
                    "text": "[跨区间停顿]",
                    "script_line": None,
                    "pad_in_ms": int(cfg["PAD_IN_MS"] * 1000),
                    "pad_out_ms": int(cfg["PAD_OUT_MS"] * 1000),
                    "reason": f"inter_interval_pause ({gap_before_interval:.2f}s)",
                    "decided_by": "rule",
                })
                seg_id += 1
                # 把本区间起点推后（加上呼吸垫）
                r_start = r_start - cfg["PAD_OUT_MS"]
                r_start = max(r_start, prev_keep_end + cfg["PAD_IN_MS"])

        # R2: 重复区间——直接采用对齐结果的 keep 标记
        if not keep_from_align:
            edl_segments.append({
                "id": seg_id,
                "keep": False,
                "start_s": r_start,
                "end_s": r_end,
                "start": fmt_tc(r_start),
                "end": fmt_tc(r_end),
                "text": raw["text"],
                "script_line": raw["script_line"],
                "pad_in_ms": 0,
                "pad_out_ms": 0,
                "reason": f"repeat_discard (align: {raw['align_reason']})",
                "decided_by": "rule",
            })
            seg_id += 1
            # 重复丢弃区间不更新 prev_keep_end
            continue

        # R3/R4: 对保留区间做停顿/语气词清理，得到子区间
        sub_segs = apply_rules_to_word_range(
            words, ws, we, r_start, r_end, cfg,
            region_label=region_type,
        )

        if not sub_segs:
            # 整个区间被规则剔除（罕见）
            edl_segments.append({
                "id": seg_id,
                "keep": False,
                "start_s": r_start,
                "end_s": r_end,
                "start": fmt_tc(r_start),
                "end": fmt_tc(r_end),
                "text": raw["text"],
                "script_line": raw["script_line"],
                "pad_in_ms": 0,
                "pad_out_ms": 0,
                "reason": "all_filtered_by_rules",
                "decided_by": "rule",
            })
            seg_id += 1
            continue

        # 输出子区间
        for sub in sub_segs:
            # clamp：不能落在首尾空镜剔除区
            sub_s = max(sub["start_s"], head_cut_end)
            sub_e = min(sub["end_s"], last_word_end + cfg["TAIL_PAD_S"])
            if sub_e - sub_s < min_seg:
                continue

            edl_segments.append({
                "id": seg_id,
                "keep": True,
                "start_s": sub_s,
                "end_s": sub_e,
                "start": fmt_tc(sub_s),
                "end": fmt_tc(sub_e),
                "text": raw["text"],
                "script_line": raw["script_line"],
                "pad_in_ms": int(cfg["PAD_OUT_MS"] * 1000),
                "pad_out_ms": int(cfg["PAD_IN_MS"] * 1000),
                "reason": sub["reason"],
                "decided_by": "rule",
            })
            seg_id += 1
            prev_keep_end = max(prev_keep_end, sub_e)

    # 末尾空镜（片尾 last_word_end 之后的静默，含已知的关机空档）：
    # 因为所有保留区间的 end_s 都 clamp 到 last_word_end + TAIL_PAD_S，
    # 不会产生超出该边界的 keep=True 片段，所以自动剔除。
    # 若最后一个保留区间 end_s < video 实际结尾，也不需要显式插入 DROP。

    edl_segments.sort(key=lambda x: x["start_s"])

    print(f"[rules] 共生成 {len(edl_segments)} 个 EDL 片段，"
          f"其中 keep={sum(1 for s in edl_segments if s['keep'])} 个保留", file=sys.stderr)

    return edl_segments


def compute_metrics(edl_segments: List[Dict], words: List[Dict], cfg: Dict) -> Dict:
    """计算自动指标"""
    keep_segs = [s for s in edl_segments if s["keep"]]

    # 检查保留区间中是否有 >0.8s 的停顿残留
    # 方式：对相邻保留区间，检查词级时间戳中的间隔
    pause_th = cfg["PAUSE_THRESHOLD_S"]
    pause_residuals = 0

    for seg in keep_segs:
        s_start = seg["start_s"]
        s_end = seg["end_s"]
        # 找该区间内的词
        seg_words = [w for w in words if w["start"] >= s_start - 0.01 and w["end"] <= s_end + 0.01]
        for i in range(len(seg_words) - 1):
            gap = seg_words[i + 1]["start"] - seg_words[i]["end"]
            if gap > pause_th:
                pause_residuals += 1

    # 统计保留区间总时长
    total_keep_s = sum(s["end_s"] - s["start_s"] for s in keep_segs)

    # 粗估语气词残留（保留区间内还有多少语气词，作为参考，不精确计）
    fillers = cfg["FILLER_WORDS"]
    filler_residuals = 0
    for seg in keep_segs:
        seg_words = [w for w in words if w["start"] >= seg["start_s"] - 0.01
                     and w["end"] <= seg["end_s"] + 0.01]
        for w in seg_words:
            import re
            wtxt = re.sub(r"[^\w一-鿿]", "", w["word"], flags=re.UNICODE)
            if wtxt in fillers:
                filler_residuals += 1

    return {
        "total_keep_duration_s": round(total_keep_s, 2),
        "total_discard_duration_s": round(
            sum(s["end_s"] - s["start_s"] for s in edl_segments if not s["keep"]), 2),
        "pause_residuals_gt08s": pause_residuals,
        "filler_word_residuals_estimate": filler_residuals,
        "keep_segments_count": len(keep_segs),
        "discard_segments_count": len(edl_segments) - len(keep_segs),
    }


def main():
    parser = argparse.ArgumentParser(
        description="S1-3 规则引擎：对齐结果 → 规则决策 JSON",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--alignment", "-a", required=True,
                        help="对齐结果 JSON（s1-2_alignment.json）")
    parser.add_argument("--transcript", "-t", required=True,
                        help="词级转写 JSON（s1-1_transcript_base.json）")
    parser.add_argument("--out", "-o", required=True,
                        help="规则决策结果 JSON 输出路径")
    args = parser.parse_args()

    print(f"[rules] 加载对齐结果: {args.alignment}", file=sys.stderr)
    alignment = load_alignment(args.alignment)

    print(f"[rules] 加载转写: {args.transcript}", file=sys.stderr)
    words = load_words(args.transcript)

    print(f"[rules] 词数: {len(words)}, 时长: {alignment['meta']['transcript_duration_s']:.1f}s",
          file=sys.stderr)

    edl_segments = apply_rules(alignment, words, CONFIG)
    metrics = compute_metrics(edl_segments, words, CONFIG)

    output = {
        "meta": {
            "alignment_source": args.alignment,
            "transcript_source": args.transcript,
            "config": CONFIG,
            "metrics": metrics,
        },
        "segments": edl_segments,
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[rules] 保存 → {args.out}", file=sys.stderr)
    print(f"[rules] 指标: {json.dumps(metrics, ensure_ascii=False)}", file=sys.stderr)


if __name__ == "__main__":
    main()

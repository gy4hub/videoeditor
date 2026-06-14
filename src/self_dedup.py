#!/usr/bin/env python3
"""
self_dedup.py — 转写自重复检测（FR-3b：第二道防线）

不依赖定稿，在转写词流内滑动检测时间邻近（间隔 <MAX_GAP_S）的
n-gram 高相似区间对，标记疑似重复（含结巴重启型部分重叠）。

输出：一组 SelfDupPair，每对包含：
  - seg_a: 第一次出现区间（word_start/end, start_s/end_s, text）
  - seg_b: 第二次出现区间
  - similarity: n-gram F1 相似度
  - gap_s: 两次之间的间隔
  - dup_type: "full_repeat" | "stutter_restart"（部分重叠型）

用法（独立运行）：
  python3 src/self_dedup.py \\
      --transcript eval/s1-1_transcript_base.json \\
      --out eval/s1f_self_dedup.json
"""

import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field, asdict
from typing import List, Optional

# ═══════════════════════════════════════════════════════════════════════════
# 可配置参数
# ═══════════════════════════════════════════════════════════════════════════
SELF_DEDUP_CFG = {
    # 两次出现之间的最大允许间隔（秒）
    # 超过此值视为正常"再次提及"而非重复
    # 设为 15s：可覆盖"市场营销这套"（gap ~9s）
    "MAX_GAP_S": 15.0,

    # 滑动窗口：最小/最大窗口字符数
    # MIN_CHARS=4 可覆盖"它的长道修复"（norm后约5字）
    "WIN_MIN_CHARS": 4,
    "WIN_MAX_CHARS": 60,

    # n-gram 大小（用于相似度计算）
    # n=2 对短重复（4-8字）更宽容
    "NGRAM": 2,

    # 相似度阈值：F1 >= 此值才视为疑似重复
    # 0.55 在 n=2 时仍有足够区分度
    "SIM_THRESHOLD": 0.55,

    # 结巴重启判定：两段的时间重叠（秒）或间隔极短（秒）
    # seg_b.start - seg_a.end < STUTTER_MAX_GAP_S 且相似
    "STUTTER_MAX_GAP_S": 3.0,

    # 最小检测词数：区间词数 < 此值不参与检测（避免单字误报）
    "MIN_WORDS": 3,
}
# ═══════════════════════════════════════════════════════════════════════════


def normalize(text: str) -> str:
    """去除空格、标点，只保留汉字/字母/数字"""
    text = re.sub(r"[^\w一-鿿]", "", text, flags=re.UNICODE)
    return text


def ngram_f1(a: str, b: str, n: int) -> float:
    """计算两个字符串的 n-gram F1"""
    if len(a) < n or len(b) < n:
        return 0.0
    ca = Counter(a[i:i+n] for i in range(len(a) - n + 1))
    cb = Counter(b[i:i+n] for i in range(len(b) - n + 1))
    total_a = sum(ca.values())
    total_b = sum(cb.values())
    overlap = sum(min(ca[g], cb[g]) for g in ca)
    if total_a == 0 or total_b == 0:
        return 0.0
    prec = overlap / total_b
    rec = overlap / total_a
    return 2 * prec * rec / (prec + rec) if prec + rec > 1e-9 else 0.0


@dataclass
class DupSeg:
    word_start: int
    word_end: int
    start_s: float
    end_s: float
    text: str
    norm_text: str


@dataclass
class SelfDupPair:
    seg_a: dict          # 第一次出现（建议 drop）
    seg_b: dict          # 第二次出现（建议 keep，更流畅/更完整）
    similarity: float
    gap_s: float         # seg_b.start_s - seg_a.end_s
    dup_type: str        # "full_repeat" | "stutter_restart"
    suggested_action: str  # "drop_a_keep_b"
    decided_by: str = "self_dedup_rule"


def build_windows(words: List[dict]) -> List[DupSeg]:
    """
    用滑动窗口把转写词流切成候选检测段。

    策略（双轨）：
    轨道 A — 停顿边界窗口：以每个词为起点，向后延伸，仅在停顿处（间隔>0.3s）或
              达到 WIN_MAX_CHARS 时记录窗口。覆盖停顿分隔的自然句段。
    轨道 B — 固定步长窗口：以 3 词为步长，固定宽度 [MIN_WORDS, MIN_WORDS+4] 覆盖
              密集重启型（无停顿）的短重复段，如"它的长道修复/它的长道修复的"。
    """
    cfg = SELF_DEDUP_CFG
    n_words = len(words)
    windows: List[DupSeg] = []
    seen: set = set()  # (word_start, word_end) 去重

    def add_window(i, j):
        key = (i, j)
        if key in seen:
            return
        if j < i:
            return
        word_count = j - i + 1
        if word_count < cfg["MIN_WORDS"]:
            return
        norm = "".join(normalize(words[k]["word"]) for k in range(i, j + 1))
        if len(norm) < cfg["WIN_MIN_CHARS"] or len(norm) > cfg["WIN_MAX_CHARS"]:
            return
        seen.add(key)
        windows.append(DupSeg(
            word_start=i,
            word_end=j,
            start_s=words[i]["start"],
            end_s=words[j]["end"],
            text="".join(w["word"] for w in words[i:j+1]),
            norm_text=norm,
        ))

    # 轨道 A: 停顿边界窗口
    for i in range(n_words):
        acc = ""
        for j in range(i, n_words):
            acc += normalize(words[j]["word"])
            if len(acc) > cfg["WIN_MAX_CHARS"]:
                break
            is_pause = (j == n_words - 1) or (words[j+1]["start"] - words[j]["end"] > 0.25)
            if is_pause and len(acc) >= cfg["WIN_MIN_CHARS"]:
                add_window(i, j)

    # 轨道 B: 固定步长滑动窗口（覆盖无停顿的密集重启）
    min_w = cfg["MIN_WORDS"]
    for i in range(0, n_words, 1):  # 每个词都作起点
        for width in range(min_w, min_w + 8):  # 宽度 min_w ~ min_w+7
            j = i + width - 1
            if j >= n_words:
                break
            add_window(i, j)

    return windows


def detect_self_dups(words: List[dict]) -> List[SelfDupPair]:
    """
    主检测函数：在转写词流中找所有疑似重复对。
    算法：
      1. 对所有候选窗口两两比较（仅比较时间接近的对）
      2. n-gram F1 >= SIM_THRESHOLD 且 gap < MAX_GAP_S → 疑似重复
      3. gap < STUTTER_MAX_GAP_S → stutter_restart，否则 full_repeat
      4. 去重：一个区间只参与最高相似度的那对
    """
    cfg = SELF_DEDUP_CFG
    windows = build_windows(words)
    pairs: List[SelfDupPair] = []

    n = len(windows)
    # 按时间排序
    windows.sort(key=lambda w: w.start_s)

    # 标记已被某对消耗的窗口（只报最佳对）
    used_as_b: set = set()

    for i in range(n):
        wa = windows[i]
        best_sim = cfg["SIM_THRESHOLD"] - 0.001
        best_pair: Optional[SelfDupPair] = None

        for j in range(i + 1, n):
            wb = windows[j]
            # 间隔过大，停止搜索
            gap = wb.start_s - wa.end_s
            if gap > cfg["MAX_GAP_S"]:
                break
            # 区间重叠过多（同一段被分成两个窗口）也跳过
            if gap < -wa.end_s * 0.5:
                continue
            # 词索引不能重叠（不比较自身子集）
            if wb.word_start <= wa.word_end:
                continue

            sim = ngram_f1(wa.norm_text, wb.norm_text, cfg["NGRAM"])
            if sim >= cfg["SIM_THRESHOLD"] and sim > best_sim:
                dup_type = (
                    "stutter_restart"
                    if gap < cfg["STUTTER_MAX_GAP_S"]
                    else "full_repeat"
                )
                best_sim = sim
                best_pair = SelfDupPair(
                    seg_a=asdict(wa),
                    seg_b=asdict(wb),
                    similarity=round(sim, 4),
                    gap_s=round(gap, 3),
                    dup_type=dup_type,
                    suggested_action="drop_a_keep_b",
                )

        if best_pair is not None:
            b_key = (best_pair.seg_b["word_start"], best_pair.seg_b["word_end"])
            if b_key not in used_as_b:
                pairs.append(best_pair)
                used_as_b.add(b_key)

    # 去除嵌套对（如果 a-b 已配对，b-c 也配对，去掉 a-b 中 b 已被用作 a 的情况）
    # 简单策略：按 similarity 降序，贪心去重
    pairs.sort(key=lambda p: -p.similarity)
    final_pairs: List[SelfDupPair] = []
    used_a_ranges: List[tuple] = []

    for p in pairs:
        a_range = (p.seg_a["word_start"], p.seg_a["word_end"])
        overlap = any(
            not (a_range[1] < r[0] or a_range[0] > r[1])
            for r in used_a_ranges
        )
        if not overlap:
            final_pairs.append(p)
            used_a_ranges.append(a_range)

    # 按时间排序输出
    final_pairs.sort(key=lambda p: p.seg_a["start_s"])
    return final_pairs


def load_words(transcript_path: str) -> List[dict]:
    with open(transcript_path, encoding="utf-8") as f:
        data = json.load(f)
    return data["words"]


def run_self_dedup(transcript_path: str, out_path: str = None) -> List[SelfDupPair]:
    """主入口：加载转写，检测，可选保存结果"""
    words = load_words(transcript_path)
    pairs = detect_self_dups(words)

    result = {
        "transcript_source": transcript_path,
        "total_pairs": len(pairs),
        "config": SELF_DEDUP_CFG,
        "pairs": [
            {
                "pair_id": idx,
                "dup_type": p.dup_type,
                "similarity": p.similarity,
                "gap_s": p.gap_s,
                "seg_a": {
                    "word_start": p.seg_a["word_start"],
                    "word_end": p.seg_a["word_end"],
                    "start_s": p.seg_a["start_s"],
                    "end_s": p.seg_a["end_s"],
                    "text": p.seg_a["text"],
                },
                "seg_b": {
                    "word_start": p.seg_b["word_start"],
                    "word_end": p.seg_b["word_end"],
                    "start_s": p.seg_b["start_s"],
                    "end_s": p.seg_b["end_s"],
                    "text": p.seg_b["text"],
                },
                "suggested_action": p.suggested_action,
                "decided_by": p.decided_by,
            }
            for idx, p in enumerate(pairs)
        ],
    }

    if out_path:
        import os
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[self_dedup] 检测完成: {len(pairs)} 对疑似重复 → {out_path}", file=sys.stderr)

    return pairs


def main():
    import argparse
    parser = argparse.ArgumentParser(description="转写自重复检测（FR-3b）")
    parser.add_argument("--transcript", "-t", required=True, help="词级转写 JSON")
    parser.add_argument("--out", "-o", required=True, help="输出 JSON 路径")
    args = parser.parse_args()
    pairs = run_self_dedup(args.transcript, args.out)
    print(f"[self_dedup] 共检出 {len(pairs)} 对疑似重复")
    for p in pairs:
        print(f"  [{p.dup_type}] sim={p.similarity:.2f} gap={p.gap_s:.1f}s")
        print(f"    A ({p.seg_a['start_s']:.1f}s): {p.seg_a['text'][:40]}")
        print(f"    B ({p.seg_b['start_s']:.1f}s): {p.seg_b['text'][:40]}")


if __name__ == "__main__":
    main()

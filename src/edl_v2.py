#!/usr/bin/env python3
"""
edl_v2.py — S1-F EDL 生成（三道防线版本）

在 edl.py 基础上增加：
  1. 读入 LLM 语义决策（s1f_llm_decisions.json），把 LLM 的 drop 区间注入 EDL
  2. 读入自重复检测结果（s1f_self_dedup.json），把命中对注入 EDL
  3. 三道防线合并逻辑：
     - 防线1（对齐重复）：已由 rules.py 处理，直接来自 alignment
     - 防线2（自重复）：来自 self_dedup.json
     - 防线3（LLM）：来自 llm_decisions.json，优先级最高，可覆盖前两道

用法：
  python3 src/edl_v2.py generate \\
      --rules output/s1_rules.json \\
      --source reference/原素材.MP4 \\
      --llm-decisions output/s1f_llm_decisions.json \\
      --self-dedup eval/s1f_self_dedup.json \\
      --out output/s1f_edl.json

  python3 src/edl_v2.py render --precise \\
      --edl output/s1f_edl.json \\
      --source reference/原素材.MP4 \\
      --out output/s1_roughcut_v2.mp4
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
from typing import List, Dict, Any, Optional


# ─── 工具函数 ─────────────────────────────────────────────────────────────────

def fmt_tc(s: float) -> str:
    """秒 → HH:MM:SS.mmm"""
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:06.3f}"


def _probe_duration(path: str, timeout: int = 10) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=timeout,
        )
        return float(r.stdout.strip())
    except Exception:
        return -1.0


def probe_fps(video_path: str) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate",
             "-of", "csv=p=0", video_path],
            capture_output=True, text=True, timeout=10,
        )
        out = r.stdout.strip()
        if "/" in out:
            num, den = out.split("/")
            return round(int(num) / int(den), 3)
        return float(out)
    except Exception:
        return 30.0


# ─── LLM 决策注入 ─────────────────────────────────────────────────────────────

def load_llm_drop_intervals(decisions_path: str) -> List[Dict]:
    """
    从 llm_decisions.json 加载 LLM 决定删除的区间列表。
    返回：[{start_s, end_s, reason, decided_by, drop_id}, ...]
    """
    if not decisions_path or not os.path.isfile(decisions_path):
        return []

    with open(decisions_path, encoding="utf-8") as f:
        data = json.load(f)

    drops = []
    for d in data.get("edl_drop_intervals", []):
        drops.append({
            "start_s": float(d["start_s"]),
            "end_s": float(d["end_s"]),
            "reason": d.get("reason", "llm_drop"),
            "decided_by": "llm",
            "drop_id": d.get("drop_id", ""),
        })
    drops.sort(key=lambda x: x["start_s"])
    print(f"[edl_v2] LLM 决策 drop 区间: {len(drops)} 个", file=sys.stderr)
    for d in drops:
        print(f"  [{d['drop_id']}] {d['start_s']:.2f}s-{d['end_s']:.2f}s: {d['reason'][:50]}", file=sys.stderr)
    return drops


def load_self_dedup_drops(self_dedup_path: str, sim_threshold: float = 0.80) -> List[Dict]:
    """
    从 self_dedup.json 加载高置信度的自重复检测结果，
    把 seg_a（第一次）标记为 drop。
    只选 similarity >= sim_threshold 的对（避免低相似度误删）。
    """
    if not self_dedup_path or not os.path.isfile(self_dedup_path):
        return []

    with open(self_dedup_path, encoding="utf-8") as f:
        data = json.load(f)

    drops = []
    for pair in data.get("pairs", []):
        sim = pair.get("similarity", 0)
        if sim < sim_threshold:
            continue
        seg_a = pair["seg_a"]
        drops.append({
            "start_s": float(seg_a["start_s"]),
            "end_s": float(seg_a["end_s"]),
            "reason": f"self_dedup_drop_a (sim={sim:.2f}, {pair['dup_type']}): {seg_a['text'][:30]}",
            "decided_by": "self_dedup_rule",
            "drop_id": f"SD_{pair['pair_id']}",
        })
    drops.sort(key=lambda x: x["start_s"])
    print(f"[edl_v2] 自重复检测 drop 区间（sim>={sim_threshold}）: {len(drops)} 个", file=sys.stderr)
    return drops


# ─── 核心：把 drop 区间注入 EDL segment 列表 ──────────────────────────────────

def apply_llm_drops_to_segments(
    segments: List[Dict],
    llm_drops: List[Dict],
    tolerance: float = 0.3,
) -> List[Dict]:
    """
    把 LLM/自重复 drop 区间叠加到已有 EDL segment 列表上。

    策略：
      - 对每个 drop 区间 [ds, de]，找所有与之重叠的 keep=True segment
      - 把重叠部分切掉（可能把一个 segment 切成 before/after 两段）
      - 插入一个 keep=False 的新 segment 覆盖 [ds, de]
      - 若某 segment 完全被 drop 覆盖，直接标记 keep=False
    """
    if not llm_drops:
        return segments

    # 转为可修改列表
    result: List[Dict] = list(segments)
    next_id = max((s.get("id", 0) for s in result), default=0) + 1

    for drop in llm_drops:
        ds = drop["start_s"]
        de = drop["end_s"]
        drop_reason = drop["reason"]
        decided_by = drop["decided_by"]

        new_result: List[Dict] = []
        drop_inserted = False

        for seg in result:
            ss = seg.get("start_s", 0.0)
            se = seg.get("end_s", 0.0)

            # 无重叠：保留原样
            if se <= ds + tolerance or ss >= de - tolerance:
                # 若 drop 区间在这两个 seg 之间，在这里插入
                if not drop_inserted and ss >= de - tolerance:
                    new_result.append({
                        "id": next_id,
                        "keep": False,
                        "start_s": ds,
                        "end_s": de,
                        "start": fmt_tc(ds),
                        "end": fmt_tc(de),
                        "text": f"[LLM DROP: {drop_reason[:40]}]",
                        "script_line": None,
                        "pad_in_ms": 0,
                        "pad_out_ms": 0,
                        "reason": drop_reason,
                        "decided_by": decided_by,
                        "note": drop.get("drop_id", ""),
                    })
                    next_id += 1
                    drop_inserted = True
                new_result.append(seg)
                continue

            # drop 完全覆盖 segment
            if ds <= ss + tolerance and de >= se - tolerance:
                # 将原 segment 改为 keep=False
                killed = dict(seg)
                killed["keep"] = False
                killed["reason"] = f"killed_by_llm: {drop_reason[:40]}"
                killed["decided_by"] = decided_by
                new_result.append(killed)
                continue

            # drop 部分重叠：切割
            # 前段（ss ~ ds）
            if ss < ds - tolerance and seg.get("keep", False):
                before = dict(seg)
                before["end_s"] = ds
                before["end"] = fmt_tc(ds)
                before["id"] = next_id
                next_id += 1
                new_result.append(before)

            # drop 本身（仅插入一次）
            if not drop_inserted:
                new_result.append({
                    "id": next_id,
                    "keep": False,
                    "start_s": max(ds, ss),
                    "end_s": min(de, se),
                    "start": fmt_tc(max(ds, ss)),
                    "end": fmt_tc(min(de, se)),
                    "text": f"[LLM DROP: {drop_reason[:40]}]",
                    "script_line": None,
                    "pad_in_ms": 0,
                    "pad_out_ms": 0,
                    "reason": drop_reason,
                    "decided_by": decided_by,
                    "note": drop.get("drop_id", ""),
                })
                next_id += 1
                drop_inserted = True

            # 后段（de ~ se）
            if se > de + tolerance and seg.get("keep", False):
                after = dict(seg)
                after["start_s"] = de
                after["start"] = fmt_tc(de)
                after["id"] = next_id
                next_id += 1
                new_result.append(after)

        # 若 drop 在所有 segment 之后，追加
        if not drop_inserted:
            new_result.append({
                "id": next_id,
                "keep": False,
                "start_s": ds,
                "end_s": de,
                "start": fmt_tc(ds),
                "end": fmt_tc(de),
                "text": f"[LLM DROP: {drop_reason[:40]}]",
                "script_line": None,
                "pad_in_ms": 0,
                "pad_out_ms": 0,
                "reason": drop_reason,
                "decided_by": decided_by,
                "note": drop.get("drop_id", ""),
            })
            next_id += 1

        result = new_result

    # 重新按时间排序，重新分配 id
    result.sort(key=lambda s: s.get("start_s", 0.0))

    # 去除精确重复段（相同 start_s+end_s 的 keep=False 段只保留一个）
    seen_ranges: set = set()
    deduped: List[Dict] = []
    for s in result:
        key = (round(s.get("start_s", 0.0), 3), round(s.get("end_s", 0.0), 3), s.get("keep", True))
        if key in seen_ranges and not s.get("keep", True):
            continue
        seen_ranges.add(key)
        deduped.append(s)
    result = deduped

    for i, s in enumerate(result):
        s["id"] = i

    return result


# ─── EDL I/O (复用 edl.py 的逻辑) ────────────────────────────────────────────

def rules_to_edl_v2(
    rules_json: Dict,
    source_file: str,
    llm_drops: List[Dict],
    self_dedup_drops: List[Dict],
    fps: float = 30.0,
) -> Dict:
    """
    三道防线 EDL 生成：
      1. rules.py 输出（含对齐重复处理）→ 基础 segments
      2. 自重复检测 drops 叠加
      3. LLM drops 叠加（最高优先级）
    """
    segments_in = rules_json.get("segments", [])

    # 基础 EDL（来自 rules.py 和 align.py）
    edl_segments = []
    for seg in segments_in:
        decided_by = seg.get("decided_by", "rule")
        edl_seg = {
            "id": seg["id"],
            "keep": seg["keep"],
            "start": seg.get("start") or fmt_tc(seg["start_s"]),
            "end": seg.get("end") or fmt_tc(seg["end_s"]),
            "start_s": seg.get("start_s", 0.0),
            "end_s": seg.get("end_s", 0.0),
            "text": seg.get("text", ""),
            "script_line": seg.get("script_line"),
            "pad_in_ms": seg.get("pad_in_ms", 0),
            "pad_out_ms": seg.get("pad_out_ms", 0),
            "reason": seg.get("reason", ""),
            "decided_by": decided_by,
            "note": seg.get("note", ""),
        }
        edl_segments.append(edl_seg)

    # 防线2：叠加自重复检测 drops
    if self_dedup_drops:
        print(f"[edl_v2] 叠加防线2（自重复检测 drops）...", file=sys.stderr)
        edl_segments = apply_llm_drops_to_segments(edl_segments, self_dedup_drops)

    # 防线3：叠加 LLM drops（最高优先级）
    if llm_drops:
        print(f"[edl_v2] 叠加防线3（LLM 语义决策 drops）...", file=sys.stderr)
        edl_segments = apply_llm_drops_to_segments(edl_segments, llm_drops)

    source_basename = os.path.basename(source_file)
    keep_count = sum(1 for s in edl_segments if s.get("keep"))
    total_keep_s = sum(s.get("end_s", 0) - s.get("start_s", 0) for s in edl_segments if s.get("keep"))
    print(f"[edl_v2] EDL v2: {len(edl_segments)} 段, keep={keep_count}, 保留时长={total_keep_s:.1f}s ({total_keep_s/60:.2f}min)",
          file=sys.stderr)

    return {
        "source": source_basename,
        "fps": fps,
        "version": "v2_three_fence",
        "segments": edl_segments,
    }


def write_edl_json(edl: Dict, out_path: str):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(edl, f, ensure_ascii=False, indent=2)
    print(f"[edl_v2] EDL JSON → {out_path}", file=sys.stderr)


def write_edl_csv(edl: Dict, out_path: str):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    fieldnames = ["id", "keep", "start", "end", "start_s", "end_s",
                  "duration_s", "text", "script_line", "pad_in_ms",
                  "pad_out_ms", "reason", "decided_by", "note"]
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for seg in edl["segments"]:
            row = dict(seg)
            row["duration_s"] = round(seg.get("end_s", 0) - seg.get("start_s", 0), 3)
            filtered = {k: row.get(k, "") for k in fieldnames}
            writer.writerow(filtered)
    print(f"[edl_v2] EDL CSV → {out_path}", file=sys.stderr)


# ─── 渲染（复用 edl.py 的 _render_precise 逻辑）─────────────────────────────

def render_edl_v2(
    edl: Dict,
    source_path: str,
    out_path: str,
    crf: int = 20,
    preset: str = "veryfast",
    precise: bool = True,
    fps: float = 30.0,
) -> str:
    """按 EDL keep=True 区间渲染成片（精确重编码模式）"""
    keep_segs = [s for s in edl["segments"] if s.get("keep")]
    if not keep_segs:
        raise ValueError("EDL 中没有 keep=true 片段")

    print(f"[edl_v2] 渲染 {len(keep_segs)} 片段 [{'precise' if precise else 'streamcopy'}] → {out_path}", file=sys.stderr)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    if precise:
        return _render_precise(keep_segs, source_path, out_path, crf, preset, fps)
    else:
        return _render_streamcopy(keep_segs, source_path, out_path)


def _render_streamcopy(keep_segs, source_path, out_path):
    base, _ = os.path.splitext(out_path)
    tmp_parts = []
    for i, seg in enumerate(keep_segs):
        start_s = seg["start_s"]
        end_s = seg["end_s"]
        duration = round(end_s - start_s, 3)
        if duration <= 0:
            continue
        part_path = f"{base}_v2_part_{i:04d}.mp4"
        cmd = ["ffmpeg", "-y", "-ss", str(start_s), "-t", str(duration),
               "-i", source_path, "-c", "copy", "-avoid_negative_ts", "make_zero", part_path]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg 切割失败: {result.stderr[-500:]}")
        tmp_parts.append(part_path)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for p in tmp_parts:
            f.write(f"file '{os.path.abspath(p)}'\n")
        list_path = f.name

    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", out_path],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"concat 失败: {result.stderr[-500:]}")
    finally:
        os.unlink(list_path)
        for p in tmp_parts:
            try: os.unlink(p)
            except OSError: pass

    dur = _probe_duration(out_path)
    print(f"[edl_v2] 渲染完成 [streamcopy]: {dur:.3f}s", file=sys.stderr)
    return out_path


def _render_precise(keep_segs, source_path, out_path, crf, preset, fps):
    """精确重编码三步法（同 edl.py _render_precise）"""
    base, _ = os.path.splitext(out_path)
    tmp_parts = []

    # Step 1: 逐段精确重编码
    for i, seg in enumerate(keep_segs):
        start_s = seg["start_s"]
        end_s = seg["end_s"]
        duration = round(end_s - start_s, 6)
        if duration <= 0:
            continue
        n_frames = max(1, round(duration * fps))
        coarse = max(0.0, start_s - 5.0)
        fine = round(start_s - coarse, 6)
        part_path = f"{base}_v2_precise_part_{i:04d}.mp4"

        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{coarse:.6f}", "-i", source_path,
            "-ss", f"{fine:.6f}",
            "-frames:v", str(n_frames),
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
            "-c:a", "aac", "-b:a", "192k",
            "-avoid_negative_ts", "make_zero",
            part_path,
        ]
        print(f"[edl_v2]   精确编码 {i+1}/{len(keep_segs)}: [{start_s:.3f}s,{end_s:.3f}s] ({duration:.3f}s={n_frames}f)",
              file=sys.stderr)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg 精确编码失败 id={seg['id']}: {result.stderr[-500:]}")
        tmp_parts.append((part_path, n_frames))

    if not tmp_parts:
        raise RuntimeError("没有有效片段")

    # Step 2: 视频 concat
    video_out = f"{base}_v2_precise_video.mp4"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for idx, (p, n) in enumerate(tmp_parts):
            f.write(f"file '{os.path.abspath(p)}'\n")
            if idx < len(tmp_parts) - 1:
                f.write(f"duration {n/fps:.6f}\n")
        list_path = f.name

    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", list_path, "-map", "0:v", "-c:v", "copy", video_out],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"视频 concat 失败: {result.stderr[-500:]}")
    finally:
        os.unlink(list_path)

    # Step 3: 音频 atrim filtergraph
    audio_out = f"{base}_v2_precise_audio.aac"
    a_filters = []
    concat_inputs = []
    for idx, seg in enumerate(keep_segs):
        s, e = seg["start_s"], seg["end_s"]
        if e - s <= 0:
            continue
        a_filters.append(f"[0:a]atrim=start={s}:end={e},asetpts=PTS-STARTPTS[a{idx}]")
        concat_inputs.append(f"[a{idx}]")

    n_a = len(concat_inputs)
    filtergraph = ";".join(a_filters) + ";" + "".join(concat_inputs) + f"concat=n={n_a}:v=0:a=1[outa]"
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", source_path,
         "-filter_complex", filtergraph,
         "-map", "[outa]", "-c:a", "aac", "-b:a", "192k", audio_out],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"音频 filtergraph 失败: {result.stderr[-500:]}")

    # Final mux
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", video_out, "-i", audio_out,
         "-c:v", "copy", "-c:a", "copy", "-shortest", out_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"mux 失败: {result.stderr[-500:]}")

    # 清理
    for p, _ in tmp_parts:
        try: os.unlink(p)
        except OSError: pass
    for f in [video_out, audio_out]:
        try: os.unlink(f)
        except OSError: pass

    dur = _probe_duration(out_path)
    print(f"[edl_v2] 渲染完成 [precise]: {out_path} ({dur:.3f}s / {dur/60:.2f}min)", file=sys.stderr)
    return out_path


# ─── CLI ─────────────────────────────────────────────────────────────────────

def cmd_generate(args):
    print(f"[edl_v2] 加载规则结果: {args.rules}", file=sys.stderr)
    with open(args.rules, encoding="utf-8") as f:
        rules_json = json.load(f)

    fps = probe_fps(args.source) if args.source else 30.0
    print(f"[edl_v2] 视频 fps: {fps}", file=sys.stderr)

    # 加载 LLM 决策
    llm_drops = load_llm_drop_intervals(args.llm_decisions)

    # 加载自重复检测（仅高置信度）
    self_dedup_drops = load_self_dedup_drops(args.self_dedup, sim_threshold=0.90)

    edl = rules_to_edl_v2(
        rules_json=rules_json,
        source_file=args.source or "unknown.mp4",
        llm_drops=llm_drops,
        self_dedup_drops=self_dedup_drops,
        fps=fps,
    )

    write_edl_json(edl, args.out)
    csv_path = os.path.splitext(args.out)[0] + ".csv"
    write_edl_csv(edl, csv_path)

    keep_count = sum(1 for s in edl["segments"] if s["keep"])
    total_keep_s = sum(s.get("end_s", 0) - s.get("start_s", 0) for s in edl["segments"] if s["keep"])
    print(f"[edl_v2] 概览: {len(edl['segments'])} 总段, {keep_count} 保留, "
          f"保留时长 {total_keep_s:.1f}s ({total_keep_s/60:.2f}min)", file=sys.stderr)


def cmd_render(args):
    print(f"[edl_v2] 加载 EDL: {args.edl}", file=sys.stderr)
    with open(args.edl, encoding="utf-8") as f:
        edl = json.load(f)

    fps = edl.get("fps", 30.0)
    render_edl_v2(
        edl=edl,
        source_path=args.source,
        out_path=args.out,
        crf=args.crf,
        preset=args.preset,
        precise=args.precise,
        fps=fps,
    )


def main():
    parser = argparse.ArgumentParser(
        description="edl_v2.py — 三道防线 EDL 生成 + 渲染",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("generate", help="生成三道防线 EDL")
    p_gen.add_argument("--rules", "-r", required=True)
    p_gen.add_argument("--source", "-v")
    p_gen.add_argument("--out", "-o", required=True)
    p_gen.add_argument("--llm-decisions", default=None, help="LLM 语义决策 JSON")
    p_gen.add_argument("--self-dedup", default=None, help="自重复检测结果 JSON")

    p_rnd = sub.add_parser("render", help="按 EDL 渲染成片")
    p_rnd.add_argument("--edl", "-e", required=True)
    p_rnd.add_argument("--source", "-v", required=True)
    p_rnd.add_argument("--out", "-o", required=True)
    p_rnd.add_argument("--crf", type=int, default=20)
    p_rnd.add_argument("--preset", default="veryfast")
    p_rnd.add_argument("--precise", action="store_true")

    args = parser.parse_args()
    if args.command == "generate":
        cmd_generate(args)
    elif args.command == "render":
        cmd_render(args)


if __name__ == "__main__":
    main()

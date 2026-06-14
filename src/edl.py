#!/usr/bin/env python3
"""
edl.py — S1-4 EDL 生成 + ffmpeg 切割渲染

功能：
  1. 把 rules.py 的输出（规则决策 segments）转换为标准 EDL JSON（PRD §5 schema）
     同步输出 CSV 版便于人看。
  2. 支持人工微调回路：decided_by=human 的行重跑时不覆盖。
  3. 渲染命令：按 EDL keep=true 区间用 ffmpeg 切割拼接（视频轨+音频轨）。
     - 精确 seek：-ss 放在 -i 之后（帧精确模式，统一重编码）
     - 拼接：concat demuxer
     - S1 不做 crossfade/滤镜

用法：
  # 生成 EDL
  python3 src/edl.py generate \\
      --rules output/s1_rules.json \\
      --source reference/原素材.MP4 \\
      --out output/s1_edl.json

  # 渲染（stream-copy 快速模式，有 keyframe overshoot）
  python3 src/edl.py render \\
      --edl output/s1_edl.json \\
      --source reference/原素材.MP4 \\
      --out output/s1_roughcut_v1.mp4

  # 渲染（精确重编码模式，--precise，时长误差 ≤1 帧）
  python3 src/edl.py render --precise \\
      --edl output/s1_edl.json \\
      --source reference/原素材.MP4 \\
      --out output/s1_roughcut_v1_precise.mp4

  # 合并人工微调（保留 decided_by=human 行不覆盖）
  python3 src/edl.py merge \\
      --base output/s1_edl_new.json \\
      --human output/s1_edl_human.json \\
      --out output/s1_edl_merged.json
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
from typing import List, Dict, Any, Optional


# ─── EDL schema 转换 ──────────────────────────────────────────────────────────

def fmt_tc(s: float) -> str:
    """秒 → HH:MM:SS.mmm（EDL 标准格式）"""
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:06.3f}"


def rules_to_edl(rules_json: Dict, source_file: str, fps: float = 30.0) -> Dict:
    """
    把 rules.py 输出的 segments 转换为 PRD §5 标准 EDL JSON。

    人工微调保护：如果传入的 rules_json 中已有 decided_by=human 的段，
    在后续 merge 时会跳过这些段。这里初始生成全部 decided_by=rule。
    """
    segments_in = rules_json.get("segments", [])
    edl_segments = []

    for seg in segments_in:
        # 若已有 human 标记（由 merge 后重入），保留原样
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
        }
        # note 字段（人工改时用）
        if "note" in seg:
            edl_seg["note"] = seg["note"]
        else:
            edl_seg["note"] = ""

        edl_segments.append(edl_seg)

    # 获取 fps（优先用传入值）
    source_basename = os.path.basename(source_file)

    edl = {
        "source": source_basename,
        "fps": fps,
        "segments": edl_segments,
    }
    return edl


def write_edl_json(edl: Dict, out_path: str):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(edl, f, ensure_ascii=False, indent=2)
    print(f"[edl] EDL JSON 已保存 → {out_path}", file=sys.stderr)


def write_edl_csv(edl: Dict, out_path: str):
    """输出 CSV 版便于人看"""
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
            # 只写 fieldnames 中有的字段
            filtered = {k: row.get(k, "") for k in fieldnames}
            writer.writerow(filtered)
    print(f"[edl] EDL CSV 已保存 → {out_path}", file=sys.stderr)


# ─── 渲染：按 EDL keep=true 区间切割拼接 ─────────────────────────────────────

def _probe_duration(path: str, timeout: int = 10) -> float:
    """用 ffprobe 获取文件时长，失败返回 -1.0。"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=timeout,
        )
        return float(r.stdout.strip())
    except Exception:
        return -1.0


def render_edl(
    edl: Dict,
    source_path: str,
    out_path: str,
    keep_parts: bool = False,
    crf: int = 18,
    preset: str = "fast",
    precise: bool = False,
    fps: float = 30.0,
) -> str:
    """
    按 EDL 中 keep=true 的区间，用 ffmpeg 切割并拼接。

    模式说明：
      precise=False（默认，stream-copy 模式）
        - -ss 放在 -i 前（fast input seek），stream copy，~0.2s/段
        - 会有 keyframe overshoot 偏差（平均 +0.7s/切点）
        - 适合快速预览

      precise=True（精确重编码模式，--precise）
        - 两步 seek：-ss 粗 seek（start-5s）+ -ss 精修（在 -i 后）
        - 用 -frames:v N（帧级精确）控制段长，libx264 veryfast crf20 + aac 192k
        - 段视频使用 concat demuxer 的 duration 元数据修正容器时间戳
        - 音频用独立 atrim filtergraph 单次渲染，避免每段 AAC encoder delay 累加
        - 最终 mux 视频 + 音频
        - 时长误差 ≤ 1 帧（≤0.034s），通常 <0.01s
        - 适合最终成片交付

    Returns: out_path on success
    """
    keep_segs = [s for s in edl["segments"] if s["keep"]]
    if not keep_segs:
        raise ValueError("EDL 中没有 keep=true 的片段，无法渲染")

    mode_label = "精确重编码" if precise else "stream-copy"
    print(f"[edl] 渲染 {len(keep_segs)} 个保留片段 [{mode_label}] → {out_path}",
          file=sys.stderr)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    if precise:
        return _render_precise(
            keep_segs=keep_segs,
            source_path=source_path,
            out_path=out_path,
            keep_parts=keep_parts,
            crf=crf,
            preset=preset,
            fps=fps,
        )
    else:
        return _render_streamcopy(
            keep_segs=keep_segs,
            source_path=source_path,
            out_path=out_path,
            keep_parts=keep_parts,
        )


def _render_streamcopy(
    keep_segs: List[Dict],
    source_path: str,
    out_path: str,
    keep_parts: bool,
) -> str:
    """
    Stream-copy 模式（快速，有 keyframe overshoot）。

    策略：
      -ss 放在 -i 前（fast input seek），stream copy，~0.2s/段
      concat demuxer 拼接（stream copy）→ 最终文件
    注意：fast input seek 会对齐到最近 keyframe，每切点平均 +0.7s 偏差。
    """
    base, _ = os.path.splitext(out_path)
    tmp_parts = []

    for i, seg in enumerate(keep_segs):
        start_s  = seg["start_s"]
        end_s    = seg["end_s"]
        duration = round(end_s - start_s, 3)
        if duration <= 0:
            print(f"[edl]   跳过零长度片段 id={seg['id']}", file=sys.stderr)
            continue

        part_path = f"{base}_part_{i:04d}.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_s), "-t", str(duration),
            "-i", source_path,
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            part_path,
        ]
        print(f"[edl]   片段 {i+1}/{len(keep_segs)}: "
              f"[{start_s:.3f}s, {end_s:.3f}s] ({duration:.3f}s)",
              file=sys.stderr)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg 切割失败 id={seg['id']} (exit {result.returncode}):\n"
                f"{result.stderr[-1000:]}"
            )
        tmp_parts.append(part_path)

    if not tmp_parts:
        raise RuntimeError("没有有效片段可以拼接")

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        for p in tmp_parts:
            f.write(f"file '{os.path.abspath(p)}'\n")
        list_path = f.name

    try:
        print(f"[edl] 拼接 {len(tmp_parts)} 片段 (stream copy) → {out_path}...",
              file=sys.stderr)
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", list_path, "-c", "copy", out_path],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg concat 失败 (exit {result.returncode}):\n"
                f"{result.stderr[-1000:]}"
            )
    finally:
        os.unlink(list_path)

    if not keep_parts:
        for p in tmp_parts:
            try: os.unlink(p)
            except OSError: pass

    dur = _probe_duration(out_path)
    print(f"[edl] 渲染完成 [stream-copy]: {out_path} ({dur:.3f}s / {dur/60:.2f}min)",
          file=sys.stderr)
    return out_path


def _render_precise(
    keep_segs: List[Dict],
    source_path: str,
    out_path: str,
    keep_parts: bool,
    crf: int,
    preset: str,
    fps: float,
) -> str:
    """
    精确重编码模式（--precise）。

    算法（三步）：
      Step 1 — 每段：两步 seek(-ss coarse -i src -ss fine) + -frames:v N (帧精确)
               libx264 preset/crf + aac 192k → 独立 .mp4 文件
               coarse = max(0, start-5s)，fine = start - coarse
               N = round(duration * fps)，确保帧数精确

      Step 2 — 视频 concat：concat demuxer 附带 duration 元数据（N/fps 秒）
               修正容器时间戳，避免每段 AAC encoder delay 累加误差

      Step 3 — 音频 atrim filtergraph：单次从源文件裁剪所有保留段拼接 → .aac
               一次编码无段间 AAC delay 积累

      最终 mux：-c:v copy -c:a copy -shortest

    时长误差：≤1 帧（≤1/fps 秒），通常 <0.01s
    编码速度：ARM aarch64 上 1080p 30fps，约 2-3× 实时（veryfast）
    """
    base, _ = os.path.splitext(out_path)
    tmp_parts: List[str] = []

    # ── Step 1: 逐段精确重编码 ─────────────────────────────────────────────
    for i, seg in enumerate(keep_segs):
        start_s  = seg["start_s"]
        end_s    = seg["end_s"]
        duration = round(end_s - start_s, 6)
        if duration <= 0:
            print(f"[edl]   跳过零长度片段 id={seg['id']}", file=sys.stderr)
            continue

        n_frames = max(1, round(duration * fps))
        coarse   = max(0.0, start_s - 5.0)
        fine     = round(start_s - coarse, 6)
        part_path = f"{base}_precise_part_{i:04d}.mp4"

        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{coarse:.6f}",
            "-i", source_path,
            "-ss", f"{fine:.6f}",
            "-frames:v", str(n_frames),
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
            "-c:a", "aac", "-b:a", "192k",
            "-avoid_negative_ts", "make_zero",
            part_path,
        ]
        print(f"[edl]   精确编码 {i+1}/{len(keep_segs)}: "
              f"id={seg['id']} [{start_s:.3f}s, {end_s:.3f}s] "
              f"({duration:.3f}s = {n_frames}frames)",
              file=sys.stderr)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg 精确编码失败 id={seg['id']} (exit {result.returncode}):\n"
                f"{result.stderr[-1000:]}"
            )
        tmp_parts.append((part_path, n_frames))

    if not tmp_parts:
        raise RuntimeError("没有有效片段可以拼接")

    # ── Step 2: 视频轨 concat（附 duration 元数据，修正容器时间戳）─────────
    video_out = f"{base}_precise_video.mp4"
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        for idx, (p, n) in enumerate(tmp_parts):
            exact_dur = n / fps
            f.write(f"file '{os.path.abspath(p)}'\n")
            # duration 指令修正每段容器时长，消除 AAC encoder delay 引起的时间戳偏移
            if idx < len(tmp_parts) - 1:
                f.write(f"duration {exact_dur:.6f}\n")
        list_path = f.name

    try:
        print(f"[edl] 视频拼接（附 duration 元数据）→ {video_out}...", file=sys.stderr)
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", list_path, "-map", "0:v", "-c:v", "copy", video_out],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg 视频 concat 失败 (exit {result.returncode}):\n"
                f"{result.stderr[-1000:]}"
            )
    finally:
        os.unlink(list_path)

    # ── Step 3: 音频 atrim filtergraph（单次编码，无段间 delay 积累）───────
    audio_out = f"{base}_precise_audio.aac"
    a_filters: List[str] = []
    concat_inputs: List[str] = []
    for idx, seg in enumerate(keep_segs):
        s, e = seg["start_s"], seg["end_s"]
        if e - s <= 0:
            continue
        a_filters.append(
            f"[0:a]atrim=start={s}:end={e},asetpts=PTS-STARTPTS[a{idx}]"
        )
        concat_inputs.append(f"[a{idx}]")
    n_a = len(concat_inputs)
    filtergraph = ";".join(a_filters) + ";" + "".join(concat_inputs) + \
                  f"concat=n={n_a}:v=0:a=1[outa]"

    print(f"[edl] 音频 filtergraph 渲染（{n_a} 段）→ {audio_out}...", file=sys.stderr)
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", source_path,
         "-filter_complex", filtergraph,
         "-map", "[outa]",
         "-c:a", "aac", "-b:a", "192k",
         audio_out],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg 音频 filtergraph 失败 (exit {result.returncode}):\n"
            f"{result.stderr[-1000:]}"
        )

    # ── Final mux ──────────────────────────────────────────────────────────
    print(f"[edl] 最终 mux 视频 + 音频 → {out_path}...", file=sys.stderr)
    result = subprocess.run(
        ["ffmpeg", "-y",
         "-i", video_out, "-i", audio_out,
         "-c:v", "copy", "-c:a", "copy",
         "-shortest",
         out_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg mux 失败 (exit {result.returncode}):\n"
            f"{result.stderr[-1000:]}"
        )

    # 清理中间文件
    if not keep_parts:
        for p, _ in tmp_parts:
            try: os.unlink(p)
            except OSError: pass
    try: os.unlink(video_out)
    except OSError: pass
    try: os.unlink(audio_out)
    except OSError: pass

    dur = _probe_duration(out_path)
    print(f"[edl] 渲染完成 [precise]: {out_path} ({dur:.3f}s / {dur/60:.2f}min)",
          file=sys.stderr)
    return out_path


# ─── 人工微调合并 ──────────────────────────────────────────────────────────────

def merge_human_edits(base_edl: Dict, human_edl: Dict) -> Dict:
    """
    合并人工微调：
    - human_edl 中 decided_by=human 的段：覆盖 base_edl 中同 id 的段
    - 其余段：使用 base_edl 的值
    """
    human_map = {
        seg["id"]: seg
        for seg in human_edl.get("segments", [])
        if seg.get("decided_by") == "human"
    }

    merged_segs = []
    for seg in base_edl.get("segments", []):
        if seg["id"] in human_map:
            merged_segs.append(human_map[seg["id"]])
            print(f"[edl] 保留人工修改: id={seg['id']} ({seg.get('text', '')[:30]})",
                  file=sys.stderr)
        else:
            merged_segs.append(seg)

    result = dict(base_edl)
    result["segments"] = merged_segs
    return result


# ─── 探针：获取视频 fps ───────────────────────────────────────────────────────

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


# ─── CLI ─────────────────────────────────────────────────────────────────────

def cmd_generate(args):
    print(f"[edl] 加载规则结果: {args.rules}", file=sys.stderr)
    with open(args.rules, encoding="utf-8") as f:
        rules_json = json.load(f)

    fps = probe_fps(args.source) if args.source else 30.0
    print(f"[edl] 视频 fps: {fps}", file=sys.stderr)

    edl = rules_to_edl(rules_json, args.source or "unknown.mp4", fps=fps)

    # 若存在旧版 EDL，合并人工修改
    if args.human_edl and os.path.isfile(args.human_edl):
        print(f"[edl] 合并人工修改自: {args.human_edl}", file=sys.stderr)
        with open(args.human_edl, encoding="utf-8") as f:
            human_edl = json.load(f)
        edl = merge_human_edits(edl, human_edl)

    write_edl_json(edl, args.out)

    # 同步输出 CSV
    csv_path = os.path.splitext(args.out)[0] + ".csv"
    write_edl_csv(edl, csv_path)

    keep_count = sum(1 for s in edl["segments"] if s["keep"])
    total_keep_s = sum(
        s.get("end_s", 0) - s.get("start_s", 0)
        for s in edl["segments"] if s["keep"]
    )
    print(f"[edl] EDL 概览: {len(edl['segments'])} 总片段, {keep_count} 保留, "
          f"保留总时长 {total_keep_s:.1f}s ({total_keep_s/60:.2f}min)", file=sys.stderr)


def cmd_render(args):
    print(f"[edl] 加载 EDL: {args.edl}", file=sys.stderr)
    with open(args.edl, encoding="utf-8") as f:
        edl = json.load(f)

    fps = edl.get("fps", 30.0)
    render_edl(
        edl=edl,
        source_path=args.source,
        out_path=args.out,
        keep_parts=args.keep_parts,
        crf=args.crf,
        preset=args.preset,
        precise=args.precise,
        fps=fps,
    )


def cmd_merge(args):
    print(f"[edl] 合并: base={args.base}, human={args.human}", file=sys.stderr)
    with open(args.base, encoding="utf-8") as f:
        base_edl = json.load(f)
    with open(args.human, encoding="utf-8") as f:
        human_edl = json.load(f)

    merged = merge_human_edits(base_edl, human_edl)
    write_edl_json(merged, args.out)
    csv_path = os.path.splitext(args.out)[0] + ".csv"
    write_edl_csv(merged, csv_path)


def main():
    parser = argparse.ArgumentParser(
        description="S1-4 EDL 生成 + ffmpeg 渲染",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # generate 子命令
    p_gen = sub.add_parser("generate", help="从规则决策生成 EDL JSON + CSV")
    p_gen.add_argument("--rules", "-r", required=True,
                       help="rules.py 输出的 JSON 路径")
    p_gen.add_argument("--source", "-v",
                       help="原视频文件（用于探针 fps）")
    p_gen.add_argument("--out", "-o", required=True,
                       help="EDL JSON 输出路径")
    p_gen.add_argument("--human-edl",
                       help="含 decided_by=human 行的旧 EDL（合并时保留人工修改）",
                       default=None)

    # render 子命令
    p_rnd = sub.add_parser("render", help="按 EDL 渲染成片")
    p_rnd.add_argument("--edl", "-e", required=True,
                       help="EDL JSON 路径")
    p_rnd.add_argument("--source", "-v", required=True,
                       help="原视频文件")
    p_rnd.add_argument("--out", "-o", required=True,
                       help="输出视频路径")
    p_rnd.add_argument("--keep-parts", action="store_true",
                       help="保留中间片段文件")
    p_rnd.add_argument("--crf", type=int, default=20,
                       help="H.264 CRF 值（默认 20）")
    p_rnd.add_argument("--preset", default="veryfast",
                       help="H.264 preset（默认 veryfast）")
    p_rnd.add_argument("--precise", action="store_true",
                       help="精确重编码模式：帧级精确切割，消除 keyframe overshoot"
                            "（libx264 veryfast crf20 + aac 192k，"
                            "时长误差 ≤1 帧，耗时约 2-3× 实时）")

    # merge 子命令
    p_mrg = sub.add_parser("merge", help="合并人工微调到新版 EDL")
    p_mrg.add_argument("--base", required=True, help="新版（规则生成）EDL")
    p_mrg.add_argument("--human", required=True, help="含人工修改的旧版 EDL")
    p_mrg.add_argument("--out", "-o", required=True, help="合并后 EDL 输出路径")

    args = parser.parse_args()

    if args.command == "generate":
        cmd_generate(args)
    elif args.command == "render":
        cmd_render(args)
    elif args.command == "merge":
        cmd_merge(args)


if __name__ == "__main__":
    main()

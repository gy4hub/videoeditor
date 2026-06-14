#!/usr/bin/env python3
"""
roughcut.py — S1-5 端到端粗剪 CLI

串起 transcribe → align → rules → edl → render，各步产物落盘可断点续跑。

用法：
  # 全流程
  python3 src/roughcut.py run \\
      --video reference/原素材.MP4 \\
      --script materials/scripts/定稿_牛初乳.md \\
      --outdir output/

  # 跳过转写（复用已有转写 JSON）
  python3 src/roughcut.py run \\
      --video reference/原素材.MP4 \\
      --script materials/scripts/定稿_牛初乳.md \\
      --outdir output/ \\
      --transcript eval/s1-1_transcript_base.json

  # 跳过转写+对齐（复用已有对齐 JSON）
  python3 src/roughcut.py run \\
      --video reference/原素材.MP4 \\
      --script materials/scripts/定稿_牛初乳.md \\
      --outdir output/ \\
      --transcript eval/s1-1_transcript_base.json \\
      --alignment eval/s1-2_alignment.json

  # 只重渲染（改完 EDL 后用）
  python3 src/roughcut.py render \\
      --edl output/s1_edl.json \\
      --video reference/原素材.MP4 \\
      --out output/s1_roughcut_v1.mp4

  # 查看帮助
  python3 src/roughcut.py --help
"""

import argparse
import json
import os
import subprocess
import sys
import time

# 保证可以 import 同目录下模块
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SRC_DIR)


def log(msg: str):
    print(f"[roughcut] {msg}", file=sys.stderr)


def step_done(path: str) -> bool:
    """检查某步的产物是否已存在（断点续跑判断）"""
    return os.path.isfile(path) and os.path.getsize(path) > 0


def run_step(label: str, cmd: list, skip_if: str = None) -> bool:
    """
    运行一步命令。
    - skip_if: 若该路径文件已存在则跳过
    返回 True 表示成功（或已跳过）。
    """
    if skip_if and step_done(skip_if):
        log(f"[跳过] {label} — 产物已存在: {skip_if}")
        return True

    log(f"[开始] {label}")
    log(f"  命令: {' '.join(cmd)}")
    t0 = time.time()
    result = subprocess.run(cmd, capture_output=False)
    elapsed = time.time() - t0

    if result.returncode != 0:
        log(f"[失败] {label} (exit {result.returncode}, 耗时 {elapsed:.1f}s)")
        return False

    log(f"[完成] {label} (耗时 {elapsed:.1f}s)")
    return True


def probe_duration(video_path: str) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", video_path],
            capture_output=True, text=True, timeout=10,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def compute_final_metrics(edl_path: str, out_video: str, source_video: str) -> dict:
    """计算最终指标"""
    metrics = {}

    # 读 EDL 获取停顿残留
    try:
        with open(edl_path, encoding="utf-8") as f:
            edl = json.load(f)
        # 从 meta.metrics 读规则指标（如果 rules.json 嵌入了）
        # EDL 本身没有 metrics，需要读 rules.json
        keep_segs = [s for s in edl.get("segments", []) if s.get("keep")]
        metrics["keep_segments"] = len(keep_segs)
        metrics["edl_total_segments"] = len(edl.get("segments", []))
        total_keep_s = sum(s.get("end_s", 0) - s.get("start_s", 0) for s in keep_segs)
        metrics["edl_keep_duration_s"] = round(total_keep_s, 2)
    except Exception as e:
        metrics["edl_read_error"] = str(e)

    # 原素材时长
    metrics["source_duration_s"] = round(probe_duration(source_video), 2)

    # 成片时长
    if os.path.isfile(out_video):
        metrics["output_duration_s"] = round(probe_duration(out_video), 2)
    else:
        metrics["output_duration_s"] = None

    # 与既有人工粗剪对比（154s）
    manual_roughcut_s = 154.0
    metrics["manual_roughcut_reference_s"] = manual_roughcut_s
    if metrics.get("output_duration_s"):
        diff = metrics["output_duration_s"] - manual_roughcut_s
        metrics["vs_manual_roughcut_diff_s"] = round(diff, 2)
        metrics["compression_ratio"] = round(
            metrics["output_duration_s"] / metrics["source_duration_s"], 4
        ) if metrics["source_duration_s"] > 0 else None

    return metrics


def cmd_run(args):
    outdir = args.outdir
    os.makedirs(outdir, exist_ok=True)

    # 路径规划
    transcript_path = args.transcript or os.path.join(outdir, "transcript.json")
    alignment_path = args.alignment or os.path.join(outdir, "alignment.json")
    rules_path = os.path.join(outdir, "s1_rules.json")
    edl_path = os.path.join(outdir, "s1_edl.json")
    out_video = args.out or os.path.join(outdir, "s1_roughcut_v1.mp4")

    video_abs = os.path.abspath(args.video)
    script_abs = os.path.abspath(args.script)

    python = sys.executable

    log(f"=== 端到端粗剪 CLI ===")
    log(f"视频: {video_abs}")
    log(f"定稿: {script_abs}")
    log(f"输出目录: {outdir}")
    log(f"输出成片: {out_video}")

    # ── Step 1: 转写 ──────────────────────────────────────────────────────────
    if not step_done(transcript_path):
        # 先提取音频
        wav_path = os.path.join(outdir, "raw_audio.wav")
        if not step_done(wav_path):
            ok = run_step(
                "提取音频",
                ["ffmpeg", "-y", "-i", video_abs, "-ac", "1", "-ar", "16000", wav_path],
            )
            if not ok:
                log("错误：音频提取失败，终止。")
                sys.exit(1)

        transcribe_py = os.path.join(SRC_DIR, "transcribe.py")
        ok = run_step(
            "转写（faster-whisper）",
            [python, transcribe_py, wav_path, "--out", transcript_path],
            skip_if=transcript_path,
        )
        if not ok:
            log("错误：转写失败，终止。")
            sys.exit(1)
    else:
        log(f"[跳过] 转写 — 复用: {transcript_path}")

    # ── Step 2: 对齐 ──────────────────────────────────────────────────────────
    align_py = os.path.join(SRC_DIR, "align.py")
    alignment_report = os.path.join(outdir, "alignment_report.md")
    ok = run_step(
        "定稿对齐",
        [python, align_py,
         "--transcript", transcript_path,
         "--script", script_abs,
         "--out", alignment_path,
         "--report", alignment_report],
        skip_if=alignment_path,
    )
    if not ok:
        log("错误：对齐失败，终止。")
        sys.exit(1)

    # ── Step 3: 规则引擎 ──────────────────────────────────────────────────────
    rules_py = os.path.join(SRC_DIR, "rules.py")
    ok = run_step(
        "规则引擎（R1-R5）",
        [python, rules_py,
         "--alignment", alignment_path,
         "--transcript", transcript_path,
         "--out", rules_path],
        skip_if=rules_path,
    )
    if not ok:
        log("错误：规则引擎失败，终止。")
        sys.exit(1)

    # ── Step 4: 生成 EDL ──────────────────────────────────────────────────────
    edl_py = os.path.join(SRC_DIR, "edl.py")
    edl_cmd = [python, edl_py, "generate",
               "--rules", rules_path,
               "--source", video_abs,
               "--out", edl_path]
    # 人工微调保护
    if args.human_edl and os.path.isfile(args.human_edl):
        edl_cmd += ["--human-edl", args.human_edl]

    ok = run_step("生成 EDL", edl_cmd)
    if not ok:
        log("错误：EDL 生成失败，终止。")
        sys.exit(1)

    # ── Step 5: 渲染 ──────────────────────────────────────────────────────────
    render_cmd = [python, edl_py, "render",
                  "--edl", edl_path,
                  "--source", video_abs,
                  "--out", out_video,
                  "--crf", str(args.crf),
                  "--preset", args.preset]
    if args.keep_parts:
        render_cmd.append("--keep-parts")

    ok = run_step("渲染成片", render_cmd)
    if not ok:
        log("错误：渲染失败，终止。")
        sys.exit(1)

    # ── Step 6: 高清滤镜（可选）─────────────────────────────────────────────
    if args.enhance:
        enhance_py = os.path.join(SRC_DIR, "enhance.py")
        grade = args.enhance_grade
        base, ext = os.path.splitext(out_video)
        enhanced_video = f"{base}_hd{ext}"
        ok = run_step(
            f"高清滤镜 [{grade}]",
            [python, enhance_py, "apply",
             "--input", out_video,
             "--out", enhanced_video,
             "--grade", grade],
        )
        if not ok:
            log("警告：滤镜失败，跳过（粗剪成片仍可用）。")
        else:
            log(f"滤镜成片: {enhanced_video}")

    # ── 计算最终指标 ──────────────────────────────────────────────────────────
    log("=== 最终指标 ===")
    metrics = compute_final_metrics(edl_path, out_video, video_abs)

    # 从 rules.json 读取停顿残留和语气词残留指标
    try:
        with open(rules_path, encoding="utf-8") as f:
            rules_json = json.load(f)
        rule_metrics = rules_json.get("meta", {}).get("metrics", {})
        metrics["pause_residuals_gt08s"] = rule_metrics.get("pause_residuals_gt08s", "N/A")
        metrics["filler_word_residuals_estimate"] = rule_metrics.get(
            "filler_word_residuals_estimate", "N/A")
    except Exception:
        pass

    for k, v in metrics.items():
        log(f"  {k}: {v}")

    # 保存指标 JSON
    metrics_path = os.path.join(outdir, "s1_metrics.json")
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    log(f"指标已保存 → {metrics_path}")

    log("=== 全流程完成 ===")
    log(f"成片: {out_video}")
    log(f"EDL:  {edl_path}")
    log(f"EDL CSV: {os.path.splitext(edl_path)[0]}.csv")


def cmd_render_only(args):
    """--from-edl 模式：只重渲染"""
    log(f"[重渲染模式] EDL: {args.edl}")

    python = sys.executable
    edl_py = os.path.join(SRC_DIR, "edl.py")

    render_cmd = [python, edl_py, "render",
                  "--edl", args.edl,
                  "--source", args.video,
                  "--out", args.out,
                  "--crf", str(args.crf),
                  "--preset", args.preset]

    ok = run_step("重渲染", render_cmd)
    if not ok:
        log("错误：渲染失败。")
        sys.exit(1)

    dur = probe_duration(args.out)
    log(f"重渲染完成: {args.out} ({dur:.1f}s)")


def main():
    parser = argparse.ArgumentParser(
        description="roughcut.py — 端到端粗剪 CLI (S1-5)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # run 子命令
    p_run = sub.add_parser("run", help="全流程：转写→对齐→规则→EDL→渲染")
    p_run.add_argument("--video", "-v", required=True, help="原素材 MP4 路径")
    p_run.add_argument("--script", "-s", required=True, help="定稿 Markdown 路径")
    p_run.add_argument("--outdir", "-d", required=True, help="输出目录")
    p_run.add_argument("--out", help="成片输出路径（默认 outdir/s1_roughcut_v1.mp4）")
    p_run.add_argument("--transcript", "-t",
                       help="复用已有转写 JSON（跳过转写步骤）",
                       default=None)
    p_run.add_argument("--alignment", "-a",
                       help="复用已有对齐 JSON（跳过对齐步骤）",
                       default=None)
    p_run.add_argument("--human-edl",
                       help="含人工修改的 EDL（合并保留 human 行）",
                       default=None)
    p_run.add_argument("--crf", type=int, default=18,
                       help="H.264 CRF（默认 18）")
    p_run.add_argument("--preset", default="fast",
                       help="H.264 preset（默认 fast）")
    p_run.add_argument("--keep-parts", action="store_true",
                       help="保留渲染中间片段")
    p_run.add_argument("--enhance", action="store_true",
                       help="渲染后自动应用高清滤镜（输出 *_hd.mp4）")
    p_run.add_argument("--enhance-grade", default="tianbaba",
                       choices=["basic", "enhanced", "tianbaba"],
                       help="滤镜档位（默认 tianbaba，对标剪映高清增强/去灰/去雾）")

    # render 子命令（--from-edl 等效）
    p_rnd = sub.add_parser("render", help="只重渲染（改完 EDL 后用）")
    p_rnd.add_argument("--edl", "-e", required=True, help="EDL JSON 路径")
    p_rnd.add_argument("--video", "-v", required=True, help="原素材 MP4 路径")
    p_rnd.add_argument("--out", "-o", required=True, help="输出视频路径")
    p_rnd.add_argument("--crf", type=int, default=18, help="H.264 CRF（默认 18）")
    p_rnd.add_argument("--preset", default="fast", help="H.264 preset（默认 fast）")

    args = parser.parse_args()

    if args.command == "run":
        cmd_run(args)
    elif args.command == "render":
        cmd_render_only(args)


if __name__ == "__main__":
    main()

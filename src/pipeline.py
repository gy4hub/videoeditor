#!/usr/bin/env python3
"""
pipeline.py — 粗剪全流程统一 CLI  (S3-2)

设计原则：
  本脚本不调任何外部 API，不引入额外费用。
  NG 检测和字幕翻译均由运行本 skill 的 Agent（LLM）完成，
  本脚本只负责串联各步骤的"执行"层。

工作流（Agent 主导）：
  Step 1  转写（本脚本执行）
          python3 src/pipeline.py transcribe --media 原素材.MP4 --outdir output/

  Step 2  Agent 阅读 transcript.json + 定稿 → 标注 NG 窗口 → 写 ng_windows.json
          （或：python3 src/ng_detect.py prompt 输出分析 prompt，Agent 在对话中给出 JSON）

  Step 3  NG 重建 EDL（本脚本执行）
          python3 src/pipeline.py run --media ... --ng-json output/ng_windows.json ...

  Step 4-7 切点吸附 → 渲染 → 滤镜 → QC（本脚本自动串联）

  字幕（可选，Agent 主导）：
          python3 src/subtitle.py align → Agent 翻译 → subtitle.py generate → subtitle.py burn

用法：
  # Step 1：只转写
  python3 src/pipeline.py transcribe \\
      --media reference/test1.MP4 \\
      --outdir output/test1/

  # Step 3+：从已有 ng_windows.json 继续（跳过转写和 Agent 分析步骤）
  python3 src/pipeline.py run \\
      --media  reference/test1.MP4 \\
      --outdir output/test1/ \\
      --ng-json output/test1/ng_windows.json \\
      [--no-enhance] [--no-subtitle]
"""

import argparse
import json
import os
import subprocess
import sys
import time

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable


# ═══════════════════════════════════════════════════════════════
#  工具函数
# ═══════════════════════════════════════════════════════════════

def log(msg: str):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def run_step(label: str, cmd: list, check: bool = True, timeout: int = 3600) -> bool:
    """运行子进程，打印耗时，返回是否成功"""
    log(f"▶ {label}")
    t0 = time.time()
    try:
        r = subprocess.run(cmd, check=check, timeout=timeout)
        elapsed = time.time() - t0
        ok = (r.returncode == 0)
        log(f"{'✅' if ok else '❌'} {label} — {elapsed:.1f}s")
        return ok
    except subprocess.CalledProcessError as e:
        log(f"❌ {label} 失败 (exit {e.returncode})")
        return False
    except subprocess.TimeoutExpired:
        log(f"❌ {label} 超时 ({timeout}s)")
        return False


def ffmpeg_concat_edl(edl_path: str, media: str, out_path: str,
                      crf: int = 20, preset: str = "veryfast") -> bool:
    """
    按 EDL JSON 的 segments 切割拼接视频（重编码，帧精确）。
    使用 concat demuxer + scale2ref 保证画质一致。
    """
    with open(edl_path, encoding="utf-8") as f:
        edl = json.load(f)

    segs = [s for s in edl.get("segments", []) if s.get("keep", True)]
    if not segs:
        log("EDL 没有可用片段")
        return False

    outdir = os.path.dirname(out_path) or "."
    os.makedirs(outdir, exist_ok=True)

    # 生成 filter_complex：每段 trim+setpts，最后 concat
    clips = []
    filter_parts = []
    for i, s in enumerate(segs):
        st = s["start_s"]
        et = s["end_s"]
        # video
        filter_parts.append(
            f"[0:v]trim=start={st:.3f}:end={et:.3f},setpts=PTS-STARTPTS[v{i}];"
        )
        # audio
        filter_parts.append(
            f"[0:a]atrim=start={st:.3f}:end={et:.3f},asetpts=PTS-STARTPTS[a{i}];"
        )
        clips.append(f"[v{i}][a{i}]")

    n = len(segs)
    filter_complex = "".join(filter_parts) + "".join(clips) + f"concat=n={n}:v=1:a=1[vout][aout]"

    cmd = [
        "ffmpeg", "-y",
        "-i", media,
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        out_path,
    ]

    log(f"  ffmpeg concat {n} 段 → {os.path.basename(out_path)}")
    return run_step("ffmpeg concat", cmd)


# ═══════════════════════════════════════════════════════════════
#  子命令：transcribe
# ═══════════════════════════════════════════════════════════════

def cmd_transcribe(args):
    """只做转写，输出 transcript.json"""
    outdir = args.outdir
    os.makedirs(outdir, exist_ok=True)

    transcript_path = os.path.join(outdir, "transcript.json")
    audio_path = os.path.join(outdir, "audio_16k.wav")

    # 提取音频
    if not os.path.exists(audio_path):
        run_step("提取音频", [
            "ffmpeg", "-y", "-i", args.media,
            "-ac", "1", "-ar", "16000", audio_path,
        ])

    # 转写
    if not os.path.exists(transcript_path):
        run_step("ASR 转写", [
            PYTHON, os.path.join(SRC_DIR, "transcribe.py"),
            audio_path,
            "--model", args.whisper_model,
            "--language", "zh",
            "--output", transcript_path,
        ])

    log(f"转写完成 → {transcript_path}")
    return transcript_path


# ═══════════════════════════════════════════════════════════════
#  子命令：run（全流程）
# ═══════════════════════════════════════════════════════════════

def cmd_run(args):
    outdir = args.outdir
    os.makedirs(outdir, exist_ok=True)

    t_total = time.time()
    log(f"=== Pipeline START ===")
    log(f"  media  : {args.media}")
    log(f"  outdir : {outdir}")

    # ── Step 1: 转写 ──────────────────────────────────────────
    transcript_path = args.transcript or os.path.join(outdir, "transcript.json")
    audio_path = os.path.join(outdir, "audio_16k.wav")

    if not os.path.exists(transcript_path):
        if not os.path.exists(audio_path):
            ok = run_step("Step 1a 提取音频", [
                "ffmpeg", "-y", "-i", args.media,
                "-ac", "1", "-ar", "16000", audio_path,
            ])
            if not ok:
                sys.exit(1)

        ok = run_step("Step 1b ASR 转写", [
            PYTHON, os.path.join(SRC_DIR, "transcribe.py"),
            audio_path,
            "--model", args.whisper_model,
            "--language", "zh",
            "--output", transcript_path,
        ])
        if not ok:
            sys.exit(1)
    else:
        log(f"Step 1 跳过（已有 transcript）: {transcript_path}")

    # ── Step 2: NG 重建 EDL ───────────────────────────────────
    # Agent 在 Step 1 转写完成后，阅读 transcript.json + 定稿，
    # 标注 NG 窗口并写入 ng_windows.json，再调用本命令继续。
    edl_ng_path = os.path.join(outdir, "edl_ng.json")

    if not os.path.exists(edl_ng_path):
        if args.ng_json and os.path.exists(args.ng_json):
            # 有 ng_windows.json → 用 manual 模式重建 EDL
            ok = run_step("Step 2 NG 重建 EDL", [
                PYTHON, os.path.join(SRC_DIR, "ng_detect.py"), "manual",
                "--transcript", transcript_path,
                "--ng-json", args.ng_json,
                "--out", edl_ng_path,
                "--source", args.media,
            ])
            if not ok:
                sys.exit(1)
        else:
            # 没有 ng_windows.json → 停顿分割基础 EDL（无语义过滤）
            log("Step 2: 无 ng_windows.json，生成基础 EDL（停顿分割，无 NG 过滤）")
            log("  提示：先运行 transcribe，再让 Agent 分析 transcript.json，")
            log("  写出 ng_windows.json 后用 --ng-json 参数重新运行。")
            _make_basic_edl(transcript_path, edl_ng_path, args.media)
    else:
        log(f"Step 2 跳过（已有 edl_ng）: {edl_ng_path}")

    # ── Step 3: 切点吸附 ──────────────────────────────────────
    edl_snapped_path = os.path.join(outdir, "edl_snapped.json")

    if not os.path.exists(edl_snapped_path):
        ok = run_step("Step 3 波形切点吸附", [
            PYTHON, os.path.join(SRC_DIR, "snap_cuts.py"),
            "--edl", edl_ng_path,
            "--audio", audio_path,
            "--out", edl_snapped_path,
        ])
        if not ok:
            log("WARNING: snap_cuts 失败，跳过吸附（使用原始 EDL）")
            import shutil
            shutil.copy(edl_ng_path, edl_snapped_path)
    else:
        log(f"Step 3 跳过（已有 edl_snapped）: {edl_snapped_path}")

    # ── Step 4: 渲染 ──────────────────────────────────────────
    roughcut_path = os.path.join(outdir, "roughcut.mp4")

    if not os.path.exists(roughcut_path):
        ok = ffmpeg_concat_edl(
            edl_snapped_path, args.media, roughcut_path,
            crf=args.crf, preset=args.ffmpeg_preset,
        )
        if not ok:
            log("ERROR: 渲染失败，中止")
            sys.exit(1)
    else:
        log(f"Step 4 跳过（已有 roughcut）: {roughcut_path}")

    final_video = roughcut_path

    # ── Step 5: 高清滤镜（可选）──────────────────────────────
    if not args.no_enhance:
        hd_path = os.path.join(outdir, "roughcut_hd.mp4")
        if not os.path.exists(hd_path):
            ok = run_step(f"Step 5 高清滤镜 [{args.enhance_grade}]", [
                PYTHON, os.path.join(SRC_DIR, "enhance.py"), "apply",
                "--input", roughcut_path,
                "--out", hd_path,
                "--grade", args.enhance_grade,
            ])
            if not ok:
                log("WARNING: 滤镜失败，跳过（使用无滤镜版本）")
            else:
                final_video = hd_path
        else:
            log(f"Step 5 跳过（已有 roughcut_hd）: {hd_path}")
            final_video = hd_path

    # ── Step 6: 字幕烧录（可选，需 Agent 先完成翻译）────────
    # 字幕工作流由 Agent 主导（见 SKILL.md 第三节），
    # 本步骤仅在 --srt 参数指定了已生成的 SRT 时执行烧录。
    if not args.no_subtitle and args.srt and os.path.exists(args.srt):
        sub_path = os.path.join(outdir, "roughcut_hd_sub.mp4")
        if not os.path.exists(sub_path):
            ok = run_step("Step 6 烧录字幕", [
                PYTHON, os.path.join(SRC_DIR, "subtitle.py"), "burn",
                "--video", final_video,
                "--srt", args.srt,
                "--out", sub_path,
            ])
            if ok:
                final_video = sub_path
            else:
                log("WARNING: 字幕烧录失败，跳过")
        else:
            log(f"Step 6 跳过（已有 subtitle）: {sub_path}")
            final_video = sub_path
    elif not args.no_subtitle and not args.srt:
        log("Step 6 跳过字幕（无 --srt 参数；字幕需 Agent 主导，见 SKILL.md §三）")

    # ── Step 7: QC 报告 ───────────────────────────────────────
    report_path = os.path.join(outdir, "qc_report.md")
    run_step("Step 7 QC 报告", [
        PYTHON, os.path.join(SRC_DIR, "qc_report.py"),
        "--edl", edl_snapped_path,
        "--source-duration", _get_duration(args.media),
        "--out", report_path,
    ], check=False)  # QC 失败不中止管线

    elapsed = time.time() - t_total
    log(f"=== Pipeline 完成 ({elapsed:.0f}s) ===")
    log(f"  成片  : {final_video}")
    log(f"  EDL   : {edl_snapped_path}")
    log(f"  报告  : {report_path}")

    return final_video


# ═══════════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════════

def _get_duration(media_path: str) -> str:
    """用 ffprobe 获取视频时长（秒），返回字符串"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", media_path],
            capture_output=True, text=True, check=True,
        )
        info = json.loads(r.stdout)
        return str(float(info["format"]["duration"]))
    except Exception:
        return "0"


def _make_basic_edl(transcript_path: str, out_path: str, source: str,
                    pause_thresh: float = 0.8, pad: float = 0.15):
    """从转写做停顿分割，生成基础 EDL（无 NG 过滤）"""
    with open(transcript_path, encoding="utf-8") as f:
        tr = json.load(f)
    words = tr.get("words", [])
    if not words:
        return

    segments = []

    def flush(buf):
        st = max(0.0, round(buf[0]["start"] - pad, 3))
        et = round(buf[-1]["end"] + pad, 3)
        text = "".join(w["word"] for w in buf)
        segments.append({
            "id": len(segments) + 1,
            "start_s": st, "end_s": et,
            "keep": True, "decided_by": "rule",
            "text": text,
        })

    buf = [words[0]]
    for w in words[1:]:
        if w["start"] - buf[-1]["end"] >= pause_thresh:
            flush(buf); buf = [w]
        else:
            buf.append(w)
    flush(buf)

    segments = [s for s in segments if s["end_s"] - s["start_s"] >= 0.3]
    for i, s in enumerate(segments):
        s["id"] = i + 1

    total_s = sum(s["end_s"] - s["start_s"] for s in segments)
    edl = {
        "version": "2.0", "source": source,
        "generated_by": "pipeline.py (基础停顿分割，无 NG 过滤)",
        "ng_windows": [],
        "segments": segments,
        "meta": {"keep_count": len(segments), "total_keep_s": round(total_s, 2),
                  "ng_window_count": 0, "words_removed": 0},
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(edl, f, ensure_ascii=False, indent=2)
    log(f"基础 EDL → {out_path} ({len(segments)} 段, {total_s:.1f}s)")


# ═══════════════════════════════════════════════════════════════
#  CLI 入口
# ═══════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(
        description="pipeline.py — 粗剪全流程统一 CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # ── run ───────────────────────────────────────────────────
    pr = sub.add_parser("run", help="全流程：转写→NG检测→切点→渲染→滤镜→字幕→报告")
    pr.add_argument("--media", required=True, help="原始素材 MP4/MOV")
    pr.add_argument("--script", default="", help="飞书定稿文本文件（auto/字幕模式必填）")
    pr.add_argument("--outdir", default="output/", help="输出目录（默认 output/）")
    pr.add_argument("--transcript", default="", help="已有转写 JSON（跳过转写步骤）")
    pr.add_argument("--ng-json", default="",
                    help="Agent 标注的 NG 窗口 JSON（由 ng_detect.py prompt + Agent 分析生成）")
    pr.add_argument("--no-enhance", action="store_true", help="跳过高清滤镜")
    pr.add_argument("--enhance-grade", default="tianbaba", help="滤镜档位（默认 tianbaba）")
    pr.add_argument("--no-subtitle", action="store_true", help="跳过字幕烧录")
    pr.add_argument("--srt", default="",
                    help="已生成的 SRT 路径（由 subtitle.py 工作流产出）；有则烧录")
    pr.add_argument("--whisper-model", default="Systran/faster-whisper-medium",
                    help="Whisper 模型")
    pr.add_argument("--crf", type=int, default=20, help="渲染 CRF（默认 20）")
    pr.add_argument("--ffmpeg-preset", default="veryfast", help="ffmpeg preset")
    pr.set_defaults(func=cmd_run)

    # ── transcribe ────────────────────────────────────────────
    pt = sub.add_parser("transcribe", help="只做转写，输出 transcript.json")
    pt.add_argument("--media", required=True)
    pt.add_argument("--outdir", default="output/")
    pt.add_argument("--whisper-model", default="Systran/faster-whisper-medium")
    pt.set_defaults(func=cmd_transcribe)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

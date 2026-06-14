#!/usr/bin/env python3
"""
breath.py — S2-1 气口平滑模块（FR-5/FR-6）v4 联合切割架构

v4 修复音画不同步（根因：双管线切点不一致 + acrossfade 吞时累积）：
  - 每段用同一时间窗口一次性切出含音频的段文件（音画先天同步）
  - 气口防爆音改为无时长损耗方案：每段头尾 8ms afade in/out
  - concat demuxer 同时拼双流（音视频均来自同一段文件）

v6 修复哑音 bug（根因：-frames:v 与 HEVC 双重 -ss seek 的音频截断问题）：
  - 原 -frames:v n_frames 在 HEVC 源 + 双 -ss seek 时导致音频编码器仅获得
    约 4.4s 的解码帧，其余时长由 muxer 用静音填充，造成全部 36 段哑尾
  - 修复：改用 -t duration（统一截止时长），对音视频流施加同一 wall-clock 限制
  - 渲染后对每段自动验证（时长差 <50ms、哑尾 <500ms），不过即重渲染（最多 2 次）

气口平滑渲染入口：
  python3 src/breath.py render \
      --edl output/s2_edl.json \
      --optimized-cuts output/s2_optimized_cuts.json \
      --source reference/原素材.MP4 \
      --out output/s1_roughcut_v4.mp4

配置参数（全部在 BREATH_CFG 中）：
  RMS_WINDOW_S     : 能量谷值搜索窗口半径（秒），默认 0.25s
  RMS_FRAME_MS     : RMS 计算帧长（ms），默认 10ms
  PAD_DEFAULT_MS   : 默认呼吸垫（ms），EDL 中 pad_in_ms/pad_out_ms 优先
  AFADE_MS         : 每段头尾 afade 时长（ms），默认 8ms（不产生重叠，无时长损耗）
  CRF              : libx264 CRF，默认 20
  PRESET           : libx264 preset，默认 veryfast
  FPS              : 目标帧率，默认 30.0
  SAMPLE_RATE      : 目标采样率（Hz），默认 48000
"""

import argparse
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import wave
from typing import List, Dict, Tuple, Optional

# ── 段级自动校验阈值 ──────────────────────────────────────────────────────────
PART_CHECK_DUR_DIFF_S   = 0.050   # 容器时长 vs EDL 时长差阈值（50ms）
PART_CHECK_SILENT_TAIL_S = 0.500  # 哑尾阈值（500ms）
PART_CHECK_SILENT_RMS    = 0.001  # 静音判定 RMS 阈值
PART_CHECK_FRAME_MS      = 50     # 静音检测分帧（ms）

# ════════════════════════════════════════════════════════════════════════════
BREATH_CFG = {
    "RMS_WINDOW_S":    0.250,   # ±250ms 窗口搜索能量谷值
    "RMS_FRAME_MS":    10,      # RMS 分帧长度（ms）
    "PAD_DEFAULT_MS":  150,     # 默认呼吸垫（ms），被 EDL pad_in/out_ms 覆盖
    "AFADE_MS":        8,       # 每段头尾 afade 时长（ms），无重叠无时长损耗
    "CRF":             20,
    "PRESET":          "veryfast",
    "FPS":             30.0,
    "SAMPLE_RATE":     48000,   # 目标音频采样率
}
# ════════════════════════════════════════════════════════════════════════════


# ─── 音频 RMS 工具 ─────────────────────────────────────────────────────────────

def extract_pcm_mono(source_path: str, out_wav: str,
                     start_s: float, end_s: float,
                     sample_rate: int = 16000) -> str:
    """
    用 ffmpeg 从 source_path 提取 [start_s, end_s] 的单声道 16-bit PCM WAV。
    """
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start_s:.6f}", "-to", f"{end_s:.6f}",
        "-i", source_path,
        "-ac", "1", "-ar", str(sample_rate),
        "-sample_fmt", "s16",
        out_wav,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg PCM extract failed:\n{r.stderr[-500:]}")
    return out_wav


def compute_rms_frames(wav_path: str, frame_ms: int = 10) -> Tuple[List[float], float]:
    """
    读取 WAV 文件，按 frame_ms 分帧计算 RMS。

    Returns
    -------
    rms_values  : List[float]  — 每帧 RMS（0~1 归一化到 16-bit 满量程）
    frame_dur_s : float        — 每帧时长（秒）
    """
    with wave.open(wav_path, 'rb') as wf:
        n_channels = wf.getnchannels()
        sample_rate = wf.getframerate()
        n_frames_total = wf.getnframes()
        raw = wf.readframes(n_frames_total)

    # 解码为 int16
    n_samples = len(raw) // 2
    samples = struct.unpack(f"<{n_samples}h", raw[:n_samples * 2])

    # 若多声道，只取第 0 声道
    if n_channels > 1:
        samples = samples[::n_channels]

    frame_samples = int(sample_rate * frame_ms / 1000)
    if frame_samples == 0:
        frame_samples = 1

    rms_values = []
    max_val = 32768.0
    for i in range(0, len(samples), frame_samples):
        chunk = samples[i: i + frame_samples]
        if not chunk:
            break
        rms = math.sqrt(sum(x * x for x in chunk) / len(chunk)) / max_val
        rms_values.append(rms)

    frame_dur_s = frame_samples / sample_rate
    return rms_values, frame_dur_s


def find_rms_valley(
    source_path: str,
    nominal_cut_s: float,
    window_s: float = 0.25,
    frame_ms: int = 10,
) -> float:
    """
    在 [nominal_cut_s - window_s, nominal_cut_s + window_s] 内
    找 RMS 最小值点，返回调整后的切点时间（秒）。

    如果提取或计算失败，返回 nominal_cut_s（退化为原始切点）。
    """
    search_start = max(0.0, nominal_cut_s - window_s)
    search_end   = nominal_cut_s + window_s

    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            tmp_wav = tf.name

        extract_pcm_mono(source_path, tmp_wav, search_start, search_end)
        rms_vals, frame_dur_s = compute_rms_frames(tmp_wav, frame_ms)
        os.unlink(tmp_wav)

        if not rms_vals:
            return nominal_cut_s

        # 找最小 RMS 帧
        min_idx = min(range(len(rms_vals)), key=lambda i: rms_vals[i])
        adjusted_cut = search_start + min_idx * frame_dur_s + frame_dur_s / 2.0

        # 限制偏移不超过 window_s
        adjusted_cut = max(search_start, min(search_end, adjusted_cut))

        shift = adjusted_cut - nominal_cut_s
        print(f"[breath] 切点优化: {nominal_cut_s:.3f}s → {adjusted_cut:.3f}s "
              f"(偏移 {shift:+.3f}s, min_rms={rms_vals[min_idx]:.4f})",
              file=sys.stderr)
        return adjusted_cut

    except Exception as e:
        print(f"[breath] 切点优化失败 @ {nominal_cut_s:.3f}s: {e}，使用原始切点",
              file=sys.stderr)
        return nominal_cut_s


# ─── 呼吸垫：从 EDL 中读取 pad 参数 ─────────────────────────────────────────────

def get_pad(seg: Dict, cfg: Dict, side: str) -> float:
    """
    返回 seg 的呼吸垫时长（秒）。
    side='in' → pad_in_ms；side='out' → pad_out_ms。
    若 EDL 中为 0，使用 PAD_DEFAULT_MS。
    """
    key = f"pad_{side}_ms"
    ms = seg.get(key, 0)
    if ms <= 0:
        ms = cfg["PAD_DEFAULT_MS"]
    return ms / 1000.0


# ─── 段级自动校验 ────────────────────────────────────────────────────────────────

def validate_part(part_path: str, expected_dur: float) -> Tuple[bool, List[str]]:
    """
    对渲染完成的单段文件做自动校验：
      1. 容器视频流时长 vs 预期时长，差值 < PART_CHECK_DUR_DIFF_S
      2. 音频哑尾 < PART_CHECK_SILENT_TAIL_S

    Returns
    -------
    (ok, issues)  — ok=True 表示通过，issues 是违规描述列表
    """
    issues = []

    # 时长检查
    try:
        rv = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-select_streams', 'v:0',
             '-show_entries', 'stream=duration', '-of', 'csv=p=0', part_path],
            capture_output=True, text=True, timeout=10,
        )
        vdur = float(rv.stdout.strip()) if rv.stdout.strip() else -1.0
        if vdur > 0 and abs(vdur - expected_dur) > PART_CHECK_DUR_DIFF_S:
            issues.append(f"V_DUR({vdur:.3f}s vs expected {expected_dur:.3f}s, "
                          f"diff={vdur-expected_dur:+.3f}s)")
    except Exception as e:
        issues.append(f"DUR_CHECK_ERROR({e})")

    # 哑尾检查
    try:
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tf:
            wav_tmp = tf.name
        r = subprocess.run(
            ['ffmpeg', '-y', '-i', part_path,
             '-ac', '1', '-ar', '16000', '-sample_fmt', 's16', wav_tmp],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            with wave.open(wav_tmp, 'rb') as wf:
                sr  = wf.getframerate()
                n   = wf.getnframes()
                raw = wf.readframes(n)
            os.unlink(wav_tmp)
            samples    = struct.unpack(f'<{len(raw)//2}h', raw[:len(raw)//2*2])
            frame_smp  = int(sr * PART_CHECK_FRAME_MS / 1000)
            total_dur  = n / sr
            last_nz    = 0.0
            for j in range(len(samples) // frame_smp):
                chunk = samples[j * frame_smp: (j + 1) * frame_smp]
                rms = math.sqrt(sum(x * x for x in chunk) / len(chunk)) / 32768.0
                if rms > PART_CHECK_SILENT_RMS:
                    last_nz = j * PART_CHECK_FRAME_MS / 1000.0
            silent_tail = total_dur - last_nz
            if silent_tail > PART_CHECK_SILENT_TAIL_S:
                issues.append(f"SILENT_TAIL({silent_tail:.3f}s, speech_end={last_nz:.3f}s)")
        else:
            try: os.unlink(wav_tmp)
            except OSError: pass
            issues.append("AUDIO_EXTRACT_FAILED")
    except Exception as e:
        issues.append(f"SILENT_CHECK_ERROR({e})")

    return (len(issues) == 0), issues


# ─── 联合切割渲染核心（v4 音画同步架构）──────────────────────────────────────────

def render_with_breath(
    edl: Dict,
    source_path: str,
    out_path: str,
    cfg: Dict,
    optimized_cuts: Optional[List[Dict]] = None,
) -> str:
    """
    v4 联合切割架构（修复音画不同步）：
      - 每个 keep 段用同一时间窗口一次性切出含音频的段文件
      - 每段音频头尾各加 8ms afade in/out（无重叠，不产生时长损耗）
      - concat demuxer 同时拼双流（音视频均来自同一段文件）
      - 段内音画先天同步，无累积错位

    与 v3 的关键区别：
      v3: 视频用 RMS 优化切点独立编码，音频用 EDL 名义时间 atrim 独立拼接
          → 双管线切点不同（最大差 ±245ms）+ acrossfade 33×15ms=0.495s 吞时累积
      v4: 同一段文件同时含音视频，concat 拼双流
          → 段内音画先天对齐，无累积误差
    """
    keep_segs = [s for s in edl["segments"] if s["keep"]]
    if not keep_segs:
        raise ValueError("EDL 中没有 keep=true 的片段")

    fps = edl.get("fps", cfg["FPS"])
    crf = cfg["CRF"]
    preset = cfg["PRESET"]
    afade_s = cfg["AFADE_MS"] / 1000.0
    rms_window = cfg["RMS_WINDOW_S"]
    frame_ms = cfg["RMS_FRAME_MS"]
    sample_rate = cfg.get("SAMPLE_RATE", 48000)

    print(f"[breath v4] 联合切割渲染: {len(keep_segs)} 段, "
          f"afade={cfg['AFADE_MS']}ms (无时长损耗), rms_window=±{cfg['RMS_WINDOW_S']}s",
          file=sys.stderr)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    base = os.path.splitext(out_path)[0]

    # ── Step 0: 确定每段的 opt_start/opt_end ──────────────────────────────
    # 优先使用传入的 optimized_cuts（已有结果直接用，不重新计算）
    # 若无，则现场计算 RMS 谷值
    if optimized_cuts:
        opt_map = {o["id"]: o for o in optimized_cuts}
        print(f"[breath v4] 使用预计算 optimized_cuts ({len(opt_map)} 段)", file=sys.stderr)
    else:
        opt_map = {}

    optimized_segs = []
    for i, seg in enumerate(keep_segs):
        sid = seg["id"]

        if sid in opt_map:
            # 直接用预计算结果
            opt_start = opt_map[sid]["opt_start"]
            opt_end   = opt_map[sid]["opt_end"]
        else:
            # 现场计算
            raw_start = seg["start_s"]
            raw_end   = seg["end_s"]
            opt_start = raw_start
            opt_end   = raw_end

            if i > 0:
                opt_start = find_rms_valley(source_path, raw_start, rms_window, frame_ms)
                if optimized_segs:
                    prev_end = optimized_segs[-1]["opt_end"]
                    opt_start = max(opt_start, prev_end + 0.001)

            if i < len(keep_segs) - 1:
                opt_end = find_rms_valley(source_path, raw_end, rms_window, frame_ms)
                opt_end = min(opt_end, raw_end + rms_window)

        # 保证最小时长 50ms
        if opt_end - opt_start < 0.05:
            opt_end = opt_start + 0.05

        optimized_segs.append({
            "id": sid,
            "opt_start": round(opt_start, 6),
            "opt_end":   round(opt_end, 6),
            "duration":  round(opt_end - opt_start, 6),
            "orig": seg,
        })

    # ── Step 1: 逐段联合切割（音视频同一窗口）────────────────────────────────
    # v6 修复：使用 -t duration（替代原 -frames:v n_frames）
    # 原 -frames:v 在 HEVC 源 + 双重 -ss seek 场景下，音频编码器只获得约
    # 4.4s 的解码帧（受 HEVC GOP 缓冲影响），其余时长由 muxer 静音填充。
    # 改用 -t duration 后，ffmpeg 以 wall-clock 时长统一截止音视频，消除该问题。
    # 渲染后立即做段级自动校验（时长差 <50ms、无哑尾），不过即重试（最多 2 次）。
    tmp_parts: List[Tuple[str, float]] = []

    for i, oseg in enumerate(optimized_segs):
        start_s  = oseg["opt_start"]
        end_s    = oseg["opt_end"]
        duration = round(end_s - start_s, 6)
        if duration <= 0:
            print(f"[breath v6] 跳过零长片段 id={oseg['id']}", file=sys.stderr)
            continue

        # v6: 直接用 -ss start_s（单次输入定位）+ -t duration
        # 不再使用 coarse+fine 双重 seek（避免 HEVC GOP 解码缓冲引起的音频截断）
        part_path = f"{base}_v6part_{i:04d}.mp4"

        # afade: in 从第 0 秒开始 8ms，out 从 (duration - 8ms) 开始 8ms
        fade_out_start = max(0.0, duration - afade_s)

        # audio filter: afade in + afade out（不改变总时长）
        af = (f"afade=t=in:st=0:d={afade_s:.4f},"
              f"afade=t=out:st={fade_out_start:.6f}:d={afade_s:.4f},"
              f"aresample={sample_rate}")

        def build_cmd(out: str) -> List[str]:
            return [
                "ffmpeg", "-y",
                "-ss", f"{start_s:.6f}",
                "-i", source_path,
                "-t", f"{duration:.6f}",
                "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                "-vf", f"fps={fps}",
                "-c:a", "aac", "-b:a", "192k",
                "-af", af,
                "-ar", str(sample_rate),
                "-avoid_negative_ts", "make_zero",
                out,
            ]

        print(f"[breath v6] 联合编码 {i+1}/{len(optimized_segs)}: "
              f"id={oseg['id']} [{start_s:.3f}s,{end_s:.3f}s] {duration:.3f}s",
              file=sys.stderr)

        max_attempts = 2
        ok = False
        for attempt in range(1, max_attempts + 1):
            cmd = build_cmd(part_path)
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(
                    f"ffmpeg 联合编码失败 id={oseg['id']} attempt={attempt}:\n{r.stderr[-800:]}"
                )
            ok, issues = validate_part(part_path, duration)
            if ok:
                if attempt > 1:
                    print(f"[breath v6]   重试成功（attempt {attempt}）", file=sys.stderr)
                break
            else:
                print(f"[breath v6]   校验失败 attempt={attempt}: {issues}", file=sys.stderr)
                if attempt < max_attempts:
                    print(f"[breath v6]   重试渲染...", file=sys.stderr)

        if not ok:
            print(f"[breath v6]   警告：id={oseg['id']} 校验未通过，继续流程（issues={issues}）",
                  file=sys.stderr)

        tmp_parts.append((part_path, duration))

    if not tmp_parts:
        raise RuntimeError("没有有效片段可渲染")

    # ── Step 2: concat demuxer 同时拼双流 ─────────────────────────────────
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                     delete=False, encoding="utf-8") as tf:
        for idx, (p, dur) in enumerate(tmp_parts):
            tf.write(f"file '{os.path.abspath(p)}'\n")
            if idx < len(tmp_parts) - 1:
                tf.write(f"duration {dur:.6f}\n")
        list_path = tf.name

    try:
        print(f"[breath v6] 双流 concat → {out_path}", file=sys.stderr)
        r = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", list_path,
             "-c:v", "copy", "-c:a", "copy",
             out_path],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"concat 失败:\n{r.stderr[-800:]}")
    finally:
        os.unlink(list_path)

    # 清理中间文件
    for p, _ in tmp_parts:
        try: os.unlink(p)
        except OSError: pass

    # 报告时长及音视频流差值
    try:
        rv = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
             "-show_entries", "stream=duration",
             "-of", "csv=p=0", out_path],
            capture_output=True, text=True, timeout=10,
        )
        ra = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "a:0",
             "-show_entries", "stream=duration",
             "-of", "csv=p=0", out_path],
            capture_output=True, text=True, timeout=10,
        )
        vdur = float(rv.stdout.strip()) if rv.stdout.strip() else -1
        adur = float(ra.stdout.strip()) if ra.stdout.strip() else -1
        diff = abs(vdur - adur) if vdur > 0 and adur > 0 else -1
        print(f"[breath v4] 完成: {out_path}\n"
              f"  视频流: {vdur:.3f}s  音频流: {adur:.3f}s  |V-A|={diff:.3f}s",
              file=sys.stderr)
    except Exception:
        print(f"[breath v4] 完成: {out_path}", file=sys.stderr)

    return out_path


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="S2-1 气口平滑渲染 v4（联合切割架构，修复音画不同步）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_rnd = sub.add_parser("render", help="气口平滑渲染（v4 联合切割）")
    p_rnd.add_argument("--edl", "-e", required=True, help="EDL JSON 路径")
    p_rnd.add_argument("--source", "-v", required=True, help="原视频文件")
    p_rnd.add_argument("--out", "-o", required=True, help="输出视频路径")
    p_rnd.add_argument("--optimized-cuts", default=None,
                       help="预计算的 optimized_cuts JSON（跳过重新计算 RMS 谷值）")
    p_rnd.add_argument("--afade-ms", type=int, default=BREATH_CFG["AFADE_MS"],
                       help=f"每段头尾 afade 时长 ms（默认 {BREATH_CFG['AFADE_MS']}，无时长损耗）")
    p_rnd.add_argument("--rms-window-s", type=float, default=BREATH_CFG["RMS_WINDOW_S"],
                       help=f"RMS 谷值搜索窗口秒（默认 {BREATH_CFG['RMS_WINDOW_S']}）")
    p_rnd.add_argument("--crf", type=int, default=BREATH_CFG["CRF"])
    p_rnd.add_argument("--preset", default=BREATH_CFG["PRESET"])

    args = parser.parse_args()

    if args.command == "render":
        with open(args.edl, encoding="utf-8") as f:
            edl = json.load(f)

        optimized_cuts = None
        if args.optimized_cuts:
            with open(args.optimized_cuts, encoding="utf-8") as f:
                optimized_cuts = json.load(f)

        cfg = dict(BREATH_CFG)
        cfg["AFADE_MS"] = args.afade_ms
        cfg["RMS_WINDOW_S"] = args.rms_window_s
        cfg["CRF"] = args.crf
        cfg["PRESET"] = args.preset

        render_with_breath(edl, args.source, args.out, cfg, optimized_cuts)


if __name__ == "__main__":
    main()

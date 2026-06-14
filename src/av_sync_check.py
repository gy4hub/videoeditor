#!/usr/bin/env python3
"""
av_sync_check.py — v4 音画同步硬检

对 5 个保留段（首/尾/中各取）逐段验证：
  1. 互相关法（cross-correlation）：
     从 v4 成片抽取该段输出位置音频 3s，与原素材源时间窗音频做互相关，
     报告 lag（理想 |lag| < 40ms）
  2. 感知哈希法（perceptual hash）：
     从 v4 成片抽取该段对应帧与原素材对应源时间帧比对，
     确认视频映射正确（hamming 距离 < 10 算通过）

5 段全部 |lag| < 40ms 才算通过。

用法：
  python3 src/av_sync_check.py \
      --v4 output/s1_roughcut_v4.mp4 \
      --source reference/原素材.MP4 \
      --optimized-cuts output/s2_optimized_cuts.json \
      [--out eval/av_sync_result.json]
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
from typing import List, Dict, Tuple


# ─── 音频提取工具 ────────────────────────────────────────────────────────────────

def extract_wav_mono(video_path: str, out_wav: str,
                     start_s: float, duration_s: float,
                     sample_rate: int = 16000) -> str:
    """从视频/音频文件提取单声道 PCM WAV。"""
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start_s:.6f}",
        "-i", video_path,
        "-t", f"{duration_s:.6f}",
        "-ac", "1", "-ar", str(sample_rate),
        "-sample_fmt", "s16",
        out_wav,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg PCM extract failed:\n{r.stderr[-400:]}")
    return out_wav


def read_wav_samples(wav_path: str) -> Tuple[List[float], int]:
    """读取 WAV 返回归一化 float 样本列表和采样率。"""
    with wave.open(wav_path, 'rb') as wf:
        n_ch = wf.getnchannels()
        sr = wf.getframerate()
        n_fr = wf.getnframes()
        raw = wf.readframes(n_fr)
    n_samp = len(raw) // 2
    samples = list(struct.unpack(f"<{n_samp}h", raw[:n_samp * 2]))
    if n_ch > 1:
        samples = samples[::n_ch]
    max_v = 32768.0
    return [s / max_v for s in samples], sr


# ─── 互相关 ────────────────────────────────────────────────────────────────────

def cross_correlate_lag(sig_a: List[float], sig_b: List[float],
                        sample_rate: int,
                        max_lag_ms: int = 500) -> Tuple[float, float]:
    """
    计算 sig_a 相对于 sig_b 的时间偏移（lag）。
    sig_a = v4 成片中提取的音频
    sig_b = 原素材对应位置音频

    Returns: (lag_ms, correlation_peak)
    正值 lag 表示 sig_a 相对 sig_b 滞后（音频比视频源晚）
    """
    max_lag_samples = int(max_lag_ms * sample_rate / 1000)
    n = min(len(sig_a), len(sig_b))
    if n == 0:
        return 0.0, 0.0

    # 截取相同长度
    a = sig_a[:n]
    b = sig_b[:n]

    # 计算归一化互相关（暴力 O(n*lag)，n ≈ 3s * 16000 = 48000，lag ≈ 16000）
    # 足够快（< 0.5s）
    sum_a2 = sum(x * x for x in a)
    sum_b2 = sum(x * x for x in b)
    norm = math.sqrt(sum_a2 * sum_b2) if sum_a2 > 0 and sum_b2 > 0 else 1.0

    best_lag = 0
    best_corr = -1.0

    for lag in range(-max_lag_samples, max_lag_samples + 1):
        corr = 0.0
        if lag >= 0:
            for i in range(n - lag):
                corr += a[i + lag] * b[i]
        else:
            for i in range(n + lag):
                corr += a[i] * b[i - lag]
        corr /= norm
        if corr > best_corr:
            best_corr = corr
            best_lag = lag

    lag_ms = best_lag * 1000.0 / sample_rate
    return lag_ms, best_corr


def fast_xcorr_lag(wav_a: str, wav_b: str,
                   max_lag_ms: int = 200) -> Tuple[float, float]:
    """
    用 ffmpeg aresample + 简化互相关计算 lag。
    使用 8kHz 降采样加速计算。
    """
    sa, sr_a = read_wav_samples(wav_a)
    sb, sr_b = read_wav_samples(wav_b)
    assert sr_a == sr_b, f"Sample rate mismatch: {sr_a} vs {sr_b}"
    return cross_correlate_lag(sa, sb, sr_a, max_lag_ms)


# ─── 视频帧感知哈希 ─────────────────────────────────────────────────────────────

def extract_frame_pgm(video_path: str, out_pgm: str, seek_s: float) -> str:
    """提取视频中 seek_s 处的帧为灰度 PGM。"""
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{seek_s:.6f}",
        "-i", video_path,
        "-frames:v", "1",
        "-vf", "scale=32:32,format=gray",
        "-f", "rawvideo", "-pix_fmt", "gray",
        out_pgm,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Frame extract failed:\n{r.stderr[-400:]}")
    return out_pgm


def dhash(raw_gray_32x32: bytes) -> int:
    """计算 32×32 灰度图的 dHash（256 bit）。"""
    pixels = list(raw_gray_32x32)
    # 使用 16x16 缩略图的行差分
    # 先缩减到 16x16（每 2x2 块取均值）
    size = 16
    thumb = []
    for r in range(size):
        for c in range(size):
            block = [
                pixels[(r * 2 + dr) * 32 + (c * 2 + dc)]
                for dr in range(2) for dc in range(2)
            ]
            thumb.append(sum(block) // 4)

    # 行差分 hash（每行 15 bit，共 16 行 = 240 bit，用 16*16 列差分 = 256 bit）
    hash_val = 0
    for i in range(size):
        for j in range(size - 1):
            bit = 1 if thumb[i * size + j] > thumb[i * size + j + 1] else 0
            hash_val = (hash_val << 1) | bit
    return hash_val


def hamming(h1: int, h2: int) -> int:
    x = h1 ^ h2
    count = 0
    while x:
        count += x & 1
        x >>= 1
    return count


def video_frame_match(v4_path: str, source_path: str,
                      v4_seek_s: float, src_seek_s: float) -> Tuple[int, bool]:
    """
    在 v4 中 v4_seek_s 处和原素材 src_seek_s 处各取一帧，
    计算感知 hash Hamming 距离。距离 < 10 认为映射正确。
    """
    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as tf:
        f_v4 = tf.name
    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as tf:
        f_src = tf.name
    try:
        extract_frame_pgm(v4_path, f_v4, v4_seek_s)
        extract_frame_pgm(source_path, f_src, src_seek_s)

        with open(f_v4, "rb") as f:
            raw_v4 = f.read(32 * 32)
        with open(f_src, "rb") as f:
            raw_src = f.read(32 * 32)

        if len(raw_v4) < 32 * 32 or len(raw_src) < 32 * 32:
            return 999, False

        h1 = dhash(raw_v4)
        h2 = dhash(raw_src)
        dist = hamming(h1, h2)
        return dist, dist < 10
    finally:
        for p in [f_v4, f_src]:
            try:
                os.unlink(p)
            except OSError:
                pass


# ─── 主检查逻辑 ─────────────────────────────────────────────────────────────────

def run_av_sync_check(
    v4_path: str,
    source_path: str,
    optimized_cuts: List[Dict],
    out_json: str = None,
    sample_rate: int = 8000,
    check_duration_s: float = 3.0,
    max_lag_ms: int = 200,
) -> Dict:
    """
    抽取 5 个保留段（首/尾/中各取），对每段做：
      1. 互相关 lag 检测
      2. 视频帧感知哈希比对

    Returns: 检查结果 dict
    """
    n = len(optimized_cuts)
    # 选 5 个检查点：索引 0, n//4, n//2, 3*n//4, n-1
    indices = sorted(set([0, n // 4, n // 2, 3 * n // 4, n - 1]))
    if len(indices) < 5:
        indices = list(range(min(5, n)))

    fps = 30.0

    # 计算 v4 中每段的输出时间位置
    # 每段在 v4 中的起始时间 = 前面所有段的 exact_dur 之和
    seg_v4_starts = []
    t = 0.0
    for opt in optimized_cuts:
        seg_v4_starts.append(t)
        dur = opt["opt_end"] - opt["opt_start"]
        n_frames = max(1, round(dur * fps))
        t += n_frames / fps

    results = []
    all_pass = True

    for idx in indices:
        opt = optimized_cuts[idx]
        src_start = opt["opt_start"]
        v4_start = seg_v4_starts[idx]
        dur = opt["opt_end"] - opt["opt_start"]
        n_frames = max(1, round(dur * fps))
        exact_dur = n_frames / fps

        # 使用前 min(check_duration_s, exact_dur) 秒
        check_dur = min(check_duration_s, exact_dur)
        if check_dur < 0.5:
            check_dur = min(check_duration_s, exact_dur)

        print(f"\n[av_sync] 检查段 idx={idx} id={opt['id']} "
              f"src=[{src_start:.3f},{src_start+exact_dur:.3f}] "
              f"v4_start={v4_start:.3f}s",
              file=sys.stderr)

        # ── 互相关 lag ─────────────────────────────────────────────────────
        lag_ms = None
        corr_peak = None
        lag_pass = None
        lag_err = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                wav_v4 = tf.name
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                wav_src = tf.name

            extract_wav_mono(v4_path, wav_v4, v4_start, check_dur, sample_rate)
            extract_wav_mono(source_path, wav_src, src_start, check_dur, sample_rate)

            lag_ms, corr_peak = fast_xcorr_lag(wav_v4, wav_src, max_lag_ms)
            lag_pass = abs(lag_ms) < 40.0

            print(f"[av_sync]   音频 lag={lag_ms:+.1f}ms  corr={corr_peak:.3f}  "
                  f"{'PASS' if lag_pass else 'FAIL'}",
                  file=sys.stderr)
        except Exception as e:
            lag_err = str(e)
            lag_pass = False
            print(f"[av_sync]   互相关失败: {e}", file=sys.stderr)
        finally:
            for p in [wav_v4, wav_src]:
                try:
                    os.unlink(p)
                except Exception:
                    pass

        # ── 视频帧感知哈希 ─────────────────────────────────────────────────
        # 取段中间帧
        mid_offset = exact_dur / 2.0
        v4_frame_seek = v4_start + mid_offset
        src_frame_seek = src_start + mid_offset
        hash_dist = None
        hash_pass = None
        hash_err = None
        try:
            hash_dist, hash_pass = video_frame_match(
                v4_path, source_path, v4_frame_seek, src_frame_seek
            )
            print(f"[av_sync]   视频 hash_dist={hash_dist}  "
                  f"{'PASS' if hash_pass else 'FAIL'}",
                  file=sys.stderr)
        except Exception as e:
            hash_err = str(e)
            hash_pass = False
            print(f"[av_sync]   帧哈希失败: {e}", file=sys.stderr)

        seg_pass = bool(lag_pass) and bool(hash_pass)
        if not seg_pass:
            all_pass = False

        results.append({
            "seg_idx": idx,
            "seg_id": opt["id"],
            "src_start": round(src_start, 3),
            "src_end": round(src_start + exact_dur, 3),
            "v4_start": round(v4_start, 3),
            "v4_end": round(v4_start + exact_dur, 3),
            "check_dur_s": round(check_dur, 3),
            "lag_ms": round(lag_ms, 1) if lag_ms is not None else None,
            "corr_peak": round(corr_peak, 3) if corr_peak is not None else None,
            "lag_pass": lag_pass,
            "hash_dist": hash_dist,
            "hash_pass": hash_pass,
            "seg_pass": seg_pass,
            "lag_err": lag_err,
            "hash_err": hash_err,
        })

    summary = {
        "v4_path": v4_path,
        "source_path": source_path,
        "n_segments_checked": len(results),
        "all_pass": all_pass,
        "threshold_lag_ms": 40,
        "threshold_hash_dist": 10,
        "segments": results,
    }

    if out_json:
        os.makedirs(os.path.dirname(os.path.abspath(out_json)), exist_ok=True)
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"\n[av_sync] 结果已保存 → {out_json}", file=sys.stderr)

    return summary


def main():
    parser = argparse.ArgumentParser(
        description="v4 音画同步硬检（互相关 lag + 视频帧感知哈希）"
    )
    parser.add_argument("--v4", required=True, help="v4 成片路径")
    parser.add_argument("--source", required=True, help="原素材路径")
    parser.add_argument("--optimized-cuts", required=True,
                        help="s2_optimized_cuts.json 路径")
    parser.add_argument("--out", default=None, help="结果 JSON 输出路径")
    parser.add_argument("--max-lag-ms", type=int, default=200)
    args = parser.parse_args()

    with open(args.optimized_cuts, encoding="utf-8") as f:
        opt_cuts = json.load(f)

    result = run_av_sync_check(
        v4_path=args.v4,
        source_path=args.source,
        optimized_cuts=opt_cuts,
        out_json=args.out,
        max_lag_ms=args.max_lag_ms,
    )

    print("\n" + "=" * 60)
    print(f"音画同步检查结果: {'全部通过' if result['all_pass'] else '存在不通过项'}")
    print(f"{'段idx':>6} {'id':>4} {'lag_ms':>8} {'lag_pass':>9} {'hash_dist':>10} {'hash_pass':>10} {'整体':>6}")
    for s in result["segments"]:
        lag_str = f"{s['lag_ms']:+.1f}" if s["lag_ms"] is not None else "ERR"
        hd_str = str(s["hash_dist"]) if s["hash_dist"] is not None else "ERR"
        print(f"{s['seg_idx']:>6} {s['seg_id']:>4} {lag_str:>8} "
              f"{'PASS' if s['lag_pass'] else 'FAIL':>9} "
              f"{hd_str:>10} "
              f"{'PASS' if s['hash_pass'] else 'FAIL':>10} "
              f"{'OK' if s['seg_pass'] else 'FAIL':>6}")
    print("=" * 60)


if __name__ == "__main__":
    main()

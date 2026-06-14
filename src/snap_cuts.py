#!/usr/bin/env python3
"""
snap_cuts.py — 波形吸附切点

对 EDL 中每个片段的 start_s / end_s：
  - 在 [t - search_s, t + search_s] 窗口内计算帧级 RMS
  - 将切点吸附到 RMS 最低的帧（实际静音/呼吸点）
  - 同时检查吸附后的点是否比原始点更安静

输出：带 _snapped 后缀的新 EDL JSON，
       以及每个切点的 before/after RMS 对比报告。

用法：
  python3 src/snap_cuts.py \
      --edl output/t1_edl_v3.json \
      --audio output/t1_audio_16k.wav \
      --out output/t1_edl_v3_snapped.json \
      [--search 0.3] [--frame 0.01]
"""

import argparse
import json
import numpy as np
import librosa
import sys


def rms_db(y):
    """RMS in dB, returns -inf for silence"""
    r = np.sqrt(np.mean(y ** 2)) if len(y) > 0 else 0
    return 20 * np.log10(r + 1e-9)


def find_silence_snap(y, sr, target_s, search_s=0.3, frame_s=0.01, direction='both'):
    """
    在 target_s 附近的 search_s 窗口内找 RMS 最低帧。
    direction: 'left' 只向左搜索（找结束点），'right' 只向右（找开始点），'both' 双向
    返回: (best_time_s, best_rms_db, original_rms_db)
    """
    frame_samples = int(frame_s * sr)
    target_sample = int(target_s * sr)

    if direction == 'left':
        lo = max(0, target_sample - int(search_s * sr))
        hi = target_sample
    elif direction == 'right':
        lo = target_sample
        hi = min(len(y), target_sample + int(search_s * sr))
    else:
        lo = max(0, target_sample - int(search_s * sr))
        hi = min(len(y), target_sample + int(search_s * sr))

    best_t = target_s
    best_rms = float('inf')
    frames = []

    for start in range(lo, hi - frame_samples, frame_samples):
        chunk = y[start: start + frame_samples]
        r = float(np.sqrt(np.mean(chunk ** 2)))
        t = (start + frame_samples // 2) / sr
        frames.append((t, r))
        if r < best_rms:
            best_rms = r
            best_t = t

    orig_rms = float(np.sqrt(np.mean(y[max(0, target_sample - frame_samples // 2):
                                        target_sample + frame_samples // 2] ** 2)))

    return round(best_t, 3), rms_db(np.array([best_rms])), rms_db(np.array([orig_rms]))


def snap_edl(edl_path, audio_path, out_path, search_s=0.3, frame_s=0.01):
    print(f"[snap] 加载音频: {audio_path}")
    y, sr = librosa.load(audio_path, sr=None, mono=True)
    duration = len(y) / sr
    print(f"[snap] 采样率: {sr}Hz, 时长: {duration:.1f}s")

    with open(edl_path, encoding='utf-8') as f:
        edl = json.load(f)

    segs = edl['segments']
    print(f"[snap] 片段数: {len(segs)}")
    print()
    print(f"  {'ID':>3}  {'orig_start':>10} → {'snap_start':>10}  Δ{'':2}  "
          f"{'orig_end':>8} → {'snap_end':>8}  Δ{'':2}  改善(dB)")
    print("  " + "─" * 80)

    total_improvement = 0
    for s in segs:
        orig_st = s['start_s']
        orig_et = s['end_s']

        # 开始点: 向右搜索（找片段开始前的静音，不要吃掉词头）
        snap_st, rms_st_new, rms_st_old = find_silence_snap(
            y, sr, orig_st, search_s, frame_s, direction='right')

        # 结束点: 向左搜索（找片段结束后的静音，不要吃掉词尾）
        snap_et, rms_et_new, rms_et_old = find_silence_snap(
            y, sr, orig_et, search_s, frame_s, direction='left')

        # 安全检查: 吸附后片段不能变短于 0.3s
        if snap_et - snap_st < 0.3:
            snap_st = orig_st
            snap_et = orig_et

        delta_st = round(snap_st - orig_st, 3)
        delta_et = round(snap_et - orig_et, 3)
        improvement = round((rms_st_old - rms_st_new + rms_et_old - rms_et_new) / 2, 1)
        total_improvement += improvement

        print(f"  [{s['id']:3d}]  {orig_st:10.3f} → {snap_st:10.3f}  ({delta_st:+.3f})  "
              f"{orig_et:8.3f} → {snap_et:8.3f}  ({delta_et:+.3f})  {improvement:+.1f}dB")

        s['start_s'] = snap_st
        s['end_s'] = snap_et
        s['snap_delta_start'] = delta_st
        s['snap_delta_end'] = delta_et

    print()
    total_dur = sum(s['end_s'] - s['start_s'] for s in segs)
    print(f"[snap] 吸附完成，平均切点改善: {total_improvement/len(segs):+.1f}dB")
    print(f"[snap] 吸附后总时长: {total_dur:.2f}s")

    edl['snapped'] = True
    edl['snap_params'] = {'search_s': search_s, 'frame_s': frame_s}

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(edl, f, ensure_ascii=False, indent=2)
    print(f"[snap] 保存 → {out_path}")


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--edl', required=True)
    p.add_argument('--audio', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--search', type=float, default=0.3,
                   help='切点搜索窗口宽度（秒），默认 0.3')
    p.add_argument('--frame', type=float, default=0.01,
                   help='RMS 分析帧大小（秒），默认 10ms')
    args = p.parse_args()
    snap_edl(args.edl, args.audio, args.out, args.search, args.frame)

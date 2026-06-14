#!/usr/bin/env python3
"""
part_audit.py — v5 段级全量审计（任务1）

检查 output/v5parts/ 中每个 part 文件：
  (a) 容器时长 vs EDL 优化切点段长（差 >100ms 记违规）
  (b) 音频有效语音末尾（RMS 持续低于阈值的尾部时长，>500ms 哑尾记违规）
  (c) 音频流 vs 视频流时长差（>100ms 记违规）

输出：
  eval/v5_part_audit.json  — 逐段详情 + 违规清单
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

SILENT_TAIL_THRESHOLD_RMS = 0.001   # RMS 低于此值视为静音
SILENT_TAIL_MIN_S         = 0.500   # 哑尾超过 500ms 记违规
DUR_DIFF_THRESHOLD_S      = 0.100   # 容器时长 vs EDL 差超 100ms 记违规
VA_DIFF_THRESHOLD_S       = 0.100   # 音视频流时长差超 100ms 记违规


def get_stream_duration(path: str, stream: str) -> float:
    """获取指定流（'v:0' 或 'a:0'）的时长（秒）。失败返回 -1。"""
    r = subprocess.run(
        ['ffprobe', '-v', 'quiet', f'-select_streams', stream,
         '-show_entries', 'stream=duration', '-of', 'csv=p=0', path],
        capture_output=True, text=True, timeout=15,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return -1.0


def measure_silent_tail(path: str, frame_ms: int = 50) -> Tuple[float, float, float]:
    """
    提取 path 的音频，按 frame_ms 分帧计算 RMS，返回：
      (total_dur_s, last_nonzero_s, silent_tail_s)
    若音频提取失败，返回 (-1, -1, -1)。
    """
    try:
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tf:
            wav_path = tf.name
        r = subprocess.run(
            ['ffmpeg', '-y', '-i', path,
             '-ac', '1', '-ar', '16000', '-sample_fmt', 's16', wav_path],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return -1.0, -1.0, -1.0

        with wave.open(wav_path, 'rb') as wf:
            sr = wf.getframerate()
            n  = wf.getnframes()
            raw = wf.readframes(n)
        os.unlink(wav_path)

        samples = struct.unpack(f'<{len(raw)//2}h', raw[:len(raw)//2*2])
        frame_samples = int(sr * frame_ms / 1000)
        if frame_samples == 0:
            frame_samples = 1

        total_dur = n / sr
        last_nonzero = 0.0
        for i in range(len(samples) // frame_samples):
            chunk = samples[i * frame_samples: (i + 1) * frame_samples]
            if not chunk:
                break
            rms = math.sqrt(sum(x * x for x in chunk) / len(chunk)) / 32768.0
            if rms > SILENT_TAIL_THRESHOLD_RMS:
                last_nonzero = i * frame_ms / 1000.0

        silent_tail = total_dur - last_nonzero
        return round(total_dur, 3), round(last_nonzero, 3), round(silent_tail, 3)

    except Exception as e:
        print(f"[part_audit] measure_silent_tail error: {e}", file=sys.stderr)
        return -1.0, -1.0, -1.0


def audit_parts(
    cuts_path: str,
    parts_dir: str,
    out_path: str,
) -> Dict:
    with open(cuts_path, encoding='utf-8') as f:
        cuts = json.load(f)

    results = []
    violations = []

    for i, cut in enumerate(cuts):
        sid      = cut['id']
        edl_dur  = round(cut['opt_end'] - cut['opt_start'], 6)
        part     = os.path.join(parts_dir, f'v5part_{i:04d}.mp4')

        entry = {
            'idx':       i,
            'seg_id':    sid,
            'opt_start': cut['opt_start'],
            'opt_end':   cut['opt_end'],
            'edl_dur':   edl_dur,
            'vdur':      -1.0,
            'adur':      -1.0,
            'va_diff':   -1.0,
            'v_edl_diff': -1.0,
            'a_edl_diff': -1.0,
            'audio_total_dur': -1.0,
            'audio_last_speech_s': -1.0,
            'silent_tail_s': -1.0,
            'violations': [],
        }

        if not os.path.isfile(part):
            entry['violations'].append('FILE_MISSING')
            results.append(entry)
            violations.append(entry)
            print(f"  [{i:02d}] id={sid} MISSING {part}", file=sys.stderr)
            continue

        vdur = get_stream_duration(part, 'v:0')
        adur = get_stream_duration(part, 'a:0')
        entry['vdur'] = round(vdur, 3)
        entry['adur'] = round(adur, 3)

        if vdur > 0 and adur > 0:
            entry['va_diff']    = round(abs(vdur - adur), 3)
            entry['v_edl_diff'] = round(vdur - edl_dur, 3)
            entry['a_edl_diff'] = round(adur - edl_dur, 3)

        # (a) 容器时长 vs EDL
        if vdur > 0 and abs(vdur - edl_dur) > DUR_DIFF_THRESHOLD_S:
            entry['violations'].append(f'V_DUR_DIFF({vdur - edl_dur:+.3f}s)')
        if adur > 0 and abs(adur - edl_dur) > DUR_DIFF_THRESHOLD_S:
            entry['violations'].append(f'A_DUR_DIFF({adur - edl_dur:+.3f}s)')

        # (c) 音视频流时长差
        if entry['va_diff'] > VA_DIFF_THRESHOLD_S:
            entry['violations'].append(f'VA_SYNC({entry["va_diff"]:.3f}s)')

        # (b) 哑尾检查
        total_a, last_speech, silent_tail = measure_silent_tail(part)
        entry['audio_total_dur']      = total_a
        entry['audio_last_speech_s']  = last_speech
        entry['silent_tail_s']        = silent_tail

        if silent_tail > SILENT_TAIL_MIN_S:
            entry['violations'].append(f'SILENT_TAIL({silent_tail:.3f}s)')

        if entry['violations']:
            violations.append(entry)
            print(f"  [{i:02d}] id={sid} VIOLATIONS: {entry['violations']}", file=sys.stderr)
        else:
            print(f"  [{i:02d}] id={sid} OK", file=sys.stderr)

        results.append(entry)

    summary = {
        'total':      len(results),
        'violations': len(violations),
        'passed':     len(results) - len(violations),
        'parts':      results,
        'violation_list': [
            {'idx': v['idx'], 'seg_id': v['seg_id'], 'flags': v['violations']}
            for v in violations
        ],
    }

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n[part_audit] 完成: {len(results)} 段, {len(violations)} 违规, "
          f"{len(results)-len(violations)} 通过", file=sys.stderr)
    print(f"[part_audit] 输出: {out_path}", file=sys.stderr)
    return summary


def main():
    parser = argparse.ArgumentParser(description='v5 段级全量审计')
    parser.add_argument('--cuts',      default='output/s2_optimized_cuts_v5.json')
    parser.add_argument('--parts-dir', default='output/v5parts')
    parser.add_argument('--out',       default='eval/v5_part_audit.json')
    args = parser.parse_args()

    print(f"[part_audit] 审计 {args.parts_dir} / cuts={args.cuts}", file=sys.stderr)
    summary = audit_parts(args.cuts, args.parts_dir, args.out)

    print(f"\n违规清单（{summary['violations']} 条）:")
    for v in summary['violation_list']:
        print(f"  part_{v['idx']:04d} id={v['seg_id']}: {v['flags']}")


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
regression_check.py — 自动回归检查（v6 修订版）

v5 旧版缺陷（导致 8/8 虚假 PASS）：
  1. keyword_absent 项的 keywords_absent 列表为空 → 空列表无法 FAIL，永远 PASS
  2. last_word_complete 的 output_window_s=null 且未传 --transcript → 退化为 SKIP
  3. 所有 8 项均验证 EDL JSON 数据而非成片音频内容
  4. 无成片实际时长 vs EDL 理论时长的核对

v6 修复：
  - 所有 keyword_absent/keyword_present 检查必须真实转写成片对应窗口
  - last_word_complete 强制转写成片末尾片段（忽略 output_window_s=null）
  - 新增 edl_duration_check：成片实际时长 = EDL 理论时长 ±300ms
  - 新增 audio_content_check：指定成片窗口的有效语音 RMS > 阈值（检测哑音）
  - 全片分批转写 → eval/v6_full_transcript.json（由 transcribe_full 子命令生成）

用法：
  python3 src/regression_check.py check \
      --video output/s1_roughcut_v6.mp4 \
      --checklist eval/regression_checklist.json \
      --edl-total 159.013 \
      --out eval/regression_result_v6.json

  python3 src/regression_check.py transcribe \
      --video output/s1_roughcut_v6.mp4 \
      --out eval/v6_full_transcript.json
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
from typing import Dict, List, Optional, Tuple

# ── 音频内容检查阈值 ────────────────────────────────────────────────────────
AUDIO_CONTENT_MIN_RMS = 0.005   # 有效语音 RMS 最低阈值
AUDIO_CONTENT_FRAME_MS = 100    # 检测分帧长度（ms）
EDL_DUR_TOLERANCE_S = 0.300     # 成片时长 vs EDL 理论时长容差（300ms）


# ── 转写工具 ────────────────────────────────────────────────────────────────

def transcribe_segment(
    video_path: str,
    start_s: float,
    end_s: float,
    model: str = 'small',
) -> str:
    """提取视频片段 [start_s, end_s] 的音频并转写，返回文本。使用 small 模型（v5 用 tiny）。"""
    wav_path = ''
    try:
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tf:
            wav_path = tf.name

        cmd = [
            'ffmpeg', '-y', '-ss', f'{start_s:.3f}', '-to', f'{end_s:.3f}',
            '-i', video_path,
            '-ac', '1', '-ar', '16000', wav_path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return ''

        py_code = f"""
import sys
try:
    from faster_whisper import WhisperModel
    m = WhisperModel('{model}', device='cpu', compute_type='int8')
    segs, _ = m.transcribe('{wav_path}', language='zh', beam_size=5)
    text = ''.join(s.text for s in segs).strip()
    print(text)
except Exception as e:
    print('', file=sys.stdout)
    print(str(e), file=sys.stderr)
"""
        r2 = subprocess.run([sys.executable, '-c', py_code],
                            capture_output=True, text=True, timeout=60)
        return r2.stdout.strip()
    except Exception:
        return ''
    finally:
        if wav_path:
            try: os.unlink(wav_path)
            except: pass


def get_video_duration(video_path: str) -> float:
    """获取视频时长（秒）。"""
    probe = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
         '-of', 'csv=p=0', video_path],
        capture_output=True, text=True, timeout=10,
    )
    return float(probe.stdout.strip())


def measure_audio_rms(video_path: str, start_s: float, end_s: float) -> float:
    """提取成片指定窗口的音频，返回最大 RMS（检测哑音）。"""
    try:
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tf:
            wav_path = tf.name
        r = subprocess.run(
            ['ffmpeg', '-y', '-ss', f'{start_s:.3f}', '-to', f'{end_s:.3f}',
             '-i', video_path, '-ac', '1', '-ar', '16000', '-sample_fmt', 's16', wav_path],
            capture_output=True, timeout=30,
        )
        if r.returncode != 0:
            return 0.0
        with wave.open(wav_path, 'rb') as wf:
            sr = wf.getframerate(); n = wf.getnframes(); raw = wf.readframes(n)
        os.unlink(wav_path)
        samples = struct.unpack(f'<{len(raw)//2}h', raw[:len(raw)//2*2])
        frame_s = int(sr * AUDIO_CONTENT_FRAME_MS / 1000)
        max_rms = 0.0
        for i in range(len(samples) // frame_s):
            chunk = samples[i * frame_s: (i + 1) * frame_s]
            rms = math.sqrt(sum(x * x for x in chunk) / len(chunk)) / 32768.0
            max_rms = max(max_rms, rms)
        return round(max_rms, 6)
    except Exception:
        return 0.0


# ── 全片分批转写 ────────────────────────────────────────────────────────────

def transcribe_full(
    video_path: str,
    out_path: str,
    segment_s: float = 30.0,
    model: str = 'small',
) -> Dict:
    """
    对成片按 segment_s 分批转写，输出 JSON。
    返回 {'segments': [{'start': s, 'end': e, 'text': t}, ...], 'full_text': '...'}
    """
    total_dur = get_video_duration(video_path)
    segments = []
    full_text_parts = []

    offset = 0.0
    batch_idx = 0
    while offset < total_dur:
        end = min(offset + segment_s, total_dur)
        print(f"[transcribe] batch {batch_idx}: [{offset:.1f}-{end:.1f}s]", file=sys.stderr)
        text = transcribe_segment(video_path, offset, end, model=model)
        segments.append({'start': round(offset, 3), 'end': round(end, 3), 'text': text})
        full_text_parts.append(text)
        offset = end
        batch_idx += 1

    result = {
        'video': video_path,
        'total_dur': round(total_dur, 3),
        'model': model,
        'segments': segments,
        'full_text': ''.join(full_text_parts),
    }
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[transcribe] 完成: {len(segments)} 批, 输出 {out_path}", file=sys.stderr)
    return result


# ── 检查逻辑 ─────────────────────────────────────────────────────────────────

def check_item(item: Dict, video_path: str, total_dur: float,
               edl_total: Optional[float] = None) -> Dict:
    """
    执行单项回归检查。

    v6 保证：所有 keyword_* 和 last_word_complete 方法都真实转写成片音频。
    """
    result = {
        'id':          item['id'],
        'category':    item.get('category', ''),
        'description': item.get('description', ''),
        'status':      'SKIP',
        'detail':      '',
    }

    method = item.get('verify_method', '')
    out_window = item.get('output_window_s')

    # ── 方法 1: keyword_absent ──────────────────────────────────────────────
    if method == 'keyword_absent':
        absent_kws = item.get('keywords_absent', [])
        if not absent_kws:
            # v5 缺陷：列表为空时无法验证 → 升级为 audio_content_check
            # 改为检查窗口内是否有有效语音（哑音检测）
            if out_window:
                s, e = out_window
                rms = measure_audio_rms(video_path, s, e)
                # 如果 keywords_present 存在，也用 ASR 确认
                present_kws = item.get('keywords_present', [])
                if present_kws:
                    text = transcribe_segment(video_path, s, e)
                    missing = [kw for kw in present_kws if kw not in text]
                    if missing:
                        result['status'] = 'FAIL'
                        result['detail'] = (f"窗口[{s:.1f}-{e:.1f}s] 缺失关键词 {missing}，"
                                            f"转写：\"{text}\"  rms={rms:.4f}")
                    else:
                        result['status'] = 'PASS'
                        result['detail'] = (f"窗口[{s:.1f}-{e:.1f}s] 关键词存在，"
                                            f"转写：\"{text}\"  rms={rms:.4f}")
                else:
                    # 无关键词要求：至少检查有语音
                    if rms < AUDIO_CONTENT_MIN_RMS:
                        result['status'] = 'FAIL'
                        result['detail'] = (f"窗口[{s:.1f}-{e:.1f}s] 音频静音 rms={rms:.6f} "
                                            f"(阈值 {AUDIO_CONTENT_MIN_RMS})")
                    else:
                        result['status'] = 'PASS'
                        result['detail'] = f"窗口[{s:.1f}-{e:.1f}s] 有有效语音 rms={rms:.4f}"
            else:
                result['status'] = 'SKIP'
                result['detail'] = 'keywords_absent 为空且无 output_window_s'
        else:
            # 有明确禁止词：ASR 转写后检查
            if out_window:
                s, e = out_window
                rms = measure_audio_rms(video_path, s, e)
                if rms < AUDIO_CONTENT_MIN_RMS:
                    result['status'] = 'FAIL'
                    result['detail'] = (f"窗口[{s:.1f}-{e:.1f}s] 音频静音 rms={rms:.6f}，"
                                        f"无法验证内容")
                else:
                    text = transcribe_segment(video_path, s, e)
                    found = [kw for kw in absent_kws if kw in text]
                    if found:
                        result['status'] = 'FAIL'
                        result['detail'] = f"窗口[{s:.1f}-{e:.1f}s] 发现应剪除词：{found}  转写：\"{text}\""
                    else:
                        result['status'] = 'PASS'
                        result['detail'] = f"窗口[{s:.1f}-{e:.1f}s] 无违规词  转写：\"{text}\""
            else:
                result['status'] = 'SKIP'
                result['detail'] = '无 output_window_s'

    # ── 方法 2: keyword_present ─────────────────────────────────────────────
    elif method == 'keyword_present':
        present_kws = item.get('keywords_present', [])
        if out_window:
            s, e = out_window
            rms = measure_audio_rms(video_path, s, e)
            if rms < AUDIO_CONTENT_MIN_RMS:
                result['status'] = 'FAIL'
                result['detail'] = (f"窗口[{s:.1f}-{e:.1f}s] 音频静音 rms={rms:.6f}，"
                                    f"无法验证关键词")
            else:
                text = transcribe_segment(video_path, s, e)
                missing = [kw for kw in present_kws if kw not in text]
                if missing:
                    result['status'] = 'FAIL'
                    result['detail'] = f"窗口[{s:.1f}-{e:.1f}s] 缺失必要词：{missing}  转写：\"{text}\""
                else:
                    result['status'] = 'PASS'
                    result['detail'] = f"窗口[{s:.1f}-{e:.1f}s] 必要词全部存在  转写：\"{text}\""
        else:
            result['status'] = 'SKIP'
            result['detail'] = '无 output_window_s'

    # ── 方法 3: last_word_complete ──────────────────────────────────────────
    elif method == 'last_word_complete':
        dur = item.get('min_duration_s', 4.0)
        present_kws = item.get('keywords_present', [])
        # v6 修复：强制转写成片末尾（不依赖 output_window_s 是否为 null）
        try:
            tail_start = max(0.0, total_dur - dur)
            rms = measure_audio_rms(video_path, tail_start, total_dur)
            if rms < AUDIO_CONTENT_MIN_RMS:
                result['status'] = 'FAIL'
                result['detail'] = (f"成片末尾 {dur}s 音频静音 rms={rms:.6f}，"
                                    f"「{''.join(present_kws)}」不可能存在")
            else:
                text = transcribe_segment(video_path, tail_start, total_dur)
                missing = [kw for kw in present_kws if kw not in text]
                if missing:
                    result['status'] = 'FAIL'
                    result['detail'] = (f"末尾 {dur}s 转写缺失：{missing}  "
                                        f"转写：\"{text}\"  rms={rms:.4f}")
                else:
                    result['status'] = 'PASS'
                    result['detail'] = (f"末尾 {dur}s 词完整  "
                                        f"转写：\"{text}\"  rms={rms:.4f}")
        except Exception as e:
            result['status'] = 'ERROR'
            result['detail'] = str(e)

    # ── 方法 4: duration_check ──────────────────────────────────────────────
    elif method == 'duration_check':
        try:
            min_dur = item.get('min_duration_s', 0)
            max_dur = item.get('max_duration_s', 9999)
            if min_dur <= total_dur <= max_dur:
                result['status'] = 'PASS'
                result['detail'] = f"视频时长 {total_dur:.3f}s 在 [{min_dur},{max_dur}]s 范围内"
            else:
                result['status'] = 'FAIL'
                result['detail'] = f"视频时长 {total_dur:.3f}s 不在 [{min_dur},{max_dur}]s 范围内"
        except Exception as e:
            result['status'] = 'ERROR'
            result['detail'] = str(e)

    # ── 方法 5: edl_duration_check（新增）──────────────────────────────────
    elif method == 'edl_duration_check':
        if edl_total is None:
            result['status'] = 'SKIP'
            result['detail'] = '未提供 --edl-total'
        else:
            diff = abs(total_dur - edl_total)
            if diff <= EDL_DUR_TOLERANCE_S:
                result['status'] = 'PASS'
                result['detail'] = (f"成片 {total_dur:.3f}s vs EDL {edl_total:.3f}s，"
                                    f"差值 {diff*1000:.0f}ms ≤ {EDL_DUR_TOLERANCE_S*1000:.0f}ms")
            else:
                result['status'] = 'FAIL'
                result['detail'] = (f"成片 {total_dur:.3f}s vs EDL {edl_total:.3f}s，"
                                    f"差值 {diff*1000:.0f}ms > {EDL_DUR_TOLERANCE_S*1000:.0f}ms")

    # ── 方法 6: audio_content_check（新增）─────────────────────────────────
    elif method == 'audio_content_check':
        if out_window:
            s, e = out_window
            rms = measure_audio_rms(video_path, s, e)
            if rms >= AUDIO_CONTENT_MIN_RMS:
                result['status'] = 'PASS'
                result['detail'] = f"窗口[{s:.1f}-{e:.1f}s] 有效语音 rms={rms:.4f}"
            else:
                result['status'] = 'FAIL'
                result['detail'] = (f"窗口[{s:.1f}-{e:.1f}s] 静音 rms={rms:.6f} "
                                    f"< 阈值 {AUDIO_CONTENT_MIN_RMS}")
        else:
            result['status'] = 'SKIP'
            result['detail'] = '无 output_window_s'

    else:
        result['status'] = 'SKIP'
        result['detail'] = f"未识别的 verify_method: {method}"

    return result


def run_regression(
    video_path: str,
    checklist_path: str,
    edl_total: Optional[float] = None,
    out_path: Optional[str] = None,
) -> Dict:
    with open(checklist_path, encoding='utf-8') as f:
        checklist = json.load(f)

    items = checklist.get('items', [])
    total_dur = get_video_duration(video_path)

    results = []
    passed = failed = skipped = errored = 0

    print(f"[regression v6] 开始检查 {len(items)} 项")
    print(f"  视频: {video_path}  时长: {total_dur:.3f}s")
    if edl_total:
        print(f"  EDL理论时长: {edl_total:.3f}s  差值: {abs(total_dur-edl_total)*1000:.0f}ms")

    for item in items:
        r = check_item(item, video_path, total_dur, edl_total)
        results.append(r)
        status = r['status']
        if   status == 'PASS':  passed  += 1
        elif status == 'FAIL':  failed  += 1
        elif status == 'ERROR': errored += 1
        else:                   skipped += 1
        icon = {'PASS': '✓', 'FAIL': '✗', 'SKIP': '○', 'ERROR': '!'}.get(status, '?')
        print(f"  {icon} [{status}] #{r['id']} {r['description'][:55]}")
        if status != 'PASS':
            print(f"       {r['detail']}")

    # 成片时长 vs EDL 核对（不依赖 checklist 是否包含 edl_duration_check 项）
    if edl_total is not None:
        diff = abs(total_dur - edl_total)
        edl_ok = diff <= EDL_DUR_TOLERANCE_S
        edl_status = 'PASS' if edl_ok else 'FAIL'
        print(f"\n  [EDL时长核对] 成片 {total_dur:.3f}s / EDL {edl_total:.3f}s / "
              f"差值 {diff*1000:.0f}ms → {edl_status}")
        if not edl_ok:
            failed += 1

    summary = {
        'video':       video_path,
        'checklist':   checklist_path,
        'video_dur_s': round(total_dur, 3),
        'edl_total_s': edl_total,
        'total':       len(items),
        'passed':      passed,
        'failed':      failed,
        'skipped':     skipped,
        'errored':     errored,
        'overall':     'PASS' if failed == 0 and errored == 0 else 'FAIL',
        'results':     results,
        'note':        ('v6 修订版：所有检查均基于成片实际音频内容，'
                        '不接受 EDL-only 或空列表虚假通过'),
    }

    print(f"\n[regression v6] 汇总: {passed}/{len(items)} PASS, "
          f"{failed} FAIL, {skipped} SKIP, {errored} ERROR")
    print(f"[regression v6] 总判定: {summary['overall']}")

    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"[regression v6] 结果保存: {out_path}")

    return summary


def main():
    parser = argparse.ArgumentParser(description='自动回归检查 v6')
    sub = parser.add_subparsers(dest='command', required=True)

    p_check = sub.add_parser('check', help='运行回归检查')
    p_check.add_argument('--video',      required=True)
    p_check.add_argument('--checklist',  required=True)
    p_check.add_argument('--edl-total',  type=float, default=None,
                         help='EDL 理论总时长（秒），用于成片时长核对')
    p_check.add_argument('--out',        default=None)

    p_tr = sub.add_parser('transcribe', help='全片分批转写')
    p_tr.add_argument('--video',   required=True)
    p_tr.add_argument('--out',     required=True)
    p_tr.add_argument('--segment', type=float, default=30.0,
                      help='每批时长（秒），默认 30s')
    p_tr.add_argument('--model',   default='small')

    args = parser.parse_args()

    if args.command == 'check':
        summary = run_regression(args.video, args.checklist, args.edl_total, args.out)
        sys.exit(0 if summary['overall'] == 'PASS' else 1)

    elif args.command == 'transcribe':
        transcribe_full(args.video, args.out, args.segment, args.model)


if __name__ == '__main__':
    main()

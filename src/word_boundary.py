#!/usr/bin/env python3
"""
word_boundary.py — 词边界硬约束模块（v5 新增）

职责：
  - 对 RMS 谷值优化后的切点施加词边界硬约束
  - 候选切点不得落在任何词的 [start, end] 内部
  - 若 ±window 内无合法间隙：退回最近词的边界 + 200ms pad
  - 尾段特殊规则：最后一个保留段 end = last_word.end + 500ms
  - 首段检查：第一个词前留 ≥300ms

输入：
  word_timestamps  : List[Dict]  — medium 转写词级时间戳
  cut_time         : float       — 候选切点（秒）
  window_s         : float       — 搜索窗口半径（秒），默认 0.25s

用法（独立）：
  python3 src/word_boundary.py \
      --transcript eval/s2b_transcript_medium.json \
      --cuts output/s2_optimized_cuts.json \
      --edl output/s2_edl.json \
      --out output/s2_optimized_cuts_v5.json
"""

import argparse
import json
import sys
from typing import List, Dict, Optional, Tuple


# ── 核心：词间隙搜索 ──────────────────────────────────────────────────────────

def find_word_at(t: float, words: List[Dict]) -> Optional[Dict]:
    """返回包含时间点 t 的词；若 t 在词间则返回 None。"""
    for w in words:
        if w['start'] < t < w['end']:
            return w
    return None


def find_best_gap(
    t: float,
    words: List[Dict],
    window: float = 0.25,
) -> Optional[Tuple[float, float, float, str, str]]:
    """
    在 [t-window, t+window] 内搜索最近的词间隙。

    Returns
    -------
    (gap_mid, gap_start, gap_end, prev_word, next_word)  或 None
    """
    gaps = []
    for i in range(len(words) - 1):
        w  = words[i]
        nw = words[i + 1]
        gs, ge = w['end'], nw['start']
        if ge <= gs:
            continue
        gm = (gs + ge) / 2.0
        if t - window <= gm <= t + window:
            gaps.append((abs(gm - t), gm, gs, ge, w['word'], nw['word']))
    if gaps:
        gaps.sort()
        _, gm, gs, ge, pw, nw = gaps[0]
        return gm, gs, ge, pw, nw
    return None


def snap_to_word_boundary(
    t: float,
    words: List[Dict],
    window: float = 0.25,
    fallback_pad: float = 0.200,
    side: str = 'any',
) -> Tuple[float, str]:
    """
    将切点 t 对齐到最近词间隙。

    Parameters
    ----------
    t             : 候选切点（秒）
    words         : 词级时间戳列表
    window        : 搜索窗口半径（秒）
    fallback_pad  : 无合法间隙时的补偿垫（秒）
    side          : 'start'（段开始，优先往后找间隙）|'end'（段结束，优先往前）|'any'

    Returns
    -------
    (adjusted_t, log_msg)
    """
    # 1. 检查 t 是否已在词间
    w = find_word_at(t, words)
    if w is None:
        return t, f"OK（{t:.3f}s 已在词间）"

    # 2. 在 ±window 内找最近间隙
    result = find_best_gap(t, words, window)
    if result:
        gm, gs, ge, pw, nw = result
        return round(gm, 6), (
            f"SNAP {t:.3f}→{gm:.3f}s "
            f"(在词\"{w['word']}\"[{w['start']:.3f}-{w['end']:.3f}]内, "
            f"对齐到间隙[{gs:.3f}-{ge:.3f}]\"{pw}\"|\"{nw}\")"
        )

    # 3. 无合法间隙：回退到词边界 + pad
    if side == 'start':
        adjusted = w['end'] + fallback_pad
        log = (f"FALLBACK {t:.3f}→{adjusted:.3f}s "
               f"(无间隙, word_end={w['end']:.3f}+{fallback_pad:.3f}s pad)")
    elif side == 'end':
        adjusted = w['start'] - fallback_pad
        log = (f"FALLBACK {t:.3f}→{adjusted:.3f}s "
               f"(无间隙, word_start={w['start']:.3f}-{fallback_pad:.3f}s pad)")
    else:
        # 就近
        if abs(t - w['start']) <= abs(t - w['end']):
            adjusted = w['start'] - fallback_pad
        else:
            adjusted = w['end'] + fallback_pad
        log = (f"FALLBACK {t:.3f}→{adjusted:.3f}s "
               f"(无间隙, word [{w['start']:.3f}-{w['end']:.3f}])")

    return round(adjusted, 6), log


# ── 全量扫描 + 修正 ────────────────────────────────────────────────────────────

def apply_word_boundary_constraints(
    edl: Dict,
    optimized_cuts: List[Dict],
    words: List[Dict],
    window: float = 0.25,
    fallback_pad: float = 0.200,
    last_seg_tail_pad: float = 0.500,
    first_seg_head_min: float = 0.300,
) -> Tuple[List[Dict], List[str]]:
    """
    对所有保留段的 opt_start/opt_end 施加词边界硬约束。

    特殊规则：
      - 首段：opt_start 至第一个词的距离 ≥ first_seg_head_min（默认 300ms）
      - 尾段：opt_end = max(opt_end, last_word.end + last_seg_tail_pad)

    Returns
    -------
    (new_cuts, log_lines)
    """
    opt_map    = {o['id']: o for o in optimized_cuts}
    keep_segs  = [s for s in edl['segments'] if s.get('keep')]
    log_lines  = []
    new_cuts   = []

    for i, seg in enumerate(keep_segs):
        sid = seg['id']
        if sid in opt_map:
            raw_start = opt_map[sid]['opt_start']
            raw_end   = opt_map[sid]['opt_end']
        else:
            raw_start = seg.get('start_s', 0.0)
            raw_end   = seg.get('end_s', 0.0)

        new_start = raw_start
        new_end   = raw_end

        # ── 首段：head pad 检查 ──────────────────────────────────────────────
        if i == 0:
            seg_words = [w for w in words if w['start'] >= new_start]
            if seg_words:
                first_w = min(seg_words, key=lambda w: w['start'])
                gap = first_w['start'] - new_start
                if gap < first_seg_head_min:
                    old_start = new_start
                    new_start = first_w['start'] - first_seg_head_min
                    if new_start < 0:
                        new_start = 0.0
                    log_lines.append(
                        f"  id={sid} start(FIRST): {old_start:.3f}→{new_start:.3f}s "
                        f"(首词\"{first_w['word']}\"@{first_w['start']:.3f}s, head_pad={gap*1000:.0f}ms→{first_seg_head_min*1000:.0f}ms)"
                    )
        else:
            # 词边界对齐（段开始）
            new_start, log = snap_to_word_boundary(new_start, words, window, fallback_pad, 'start')
            if 'SNAP' in log or 'FALLBACK' in log:
                log_lines.append(f"  id={sid} start: {log}")

        # ── 尾段：tail pad 规则 ──────────────────────────────────────────────
        if i == len(keep_segs) - 1:
            seg_words = [w for w in words if w['start'] >= new_start - 1.0]
            if seg_words:
                last_w = max(seg_words, key=lambda w: w['end'])
                forced_end = last_w['end'] + last_seg_tail_pad
                if forced_end > new_end:
                    log_lines.append(
                        f"  id={sid} end(LAST): {new_end:.3f}→{forced_end:.3f}s "
                        f"(last_word\"{last_w['word']}\"@{last_w['end']:.3f}s +{last_seg_tail_pad*1000:.0f}ms)"
                    )
                    new_end = forced_end
        else:
            new_end, log = snap_to_word_boundary(new_end, words, window, fallback_pad, 'end')
            if 'SNAP' in log or 'FALLBACK' in log:
                log_lines.append(f"  id={sid} end:   {log}")

        # ── 保证最小时长 50ms ────────────────────────────────────────────────
        if new_end - new_start < 0.05:
            new_end = new_start + 0.05

        new_cuts.append({
            'id':        sid,
            'opt_start': round(new_start, 6),
            'opt_end':   round(new_end,   6),
        })

    # ── 修复相邻段重叠 ─────────────────────────────────────────────────────
    for j in range(1, len(new_cuts)):
        prev = new_cuts[j - 1]
        cur  = new_cuts[j]
        if cur['opt_start'] < prev['opt_end']:
            old = cur['opt_start']
            cur['opt_start'] = round(prev['opt_end'] + 0.001, 6)
            if cur['opt_end'] < cur['opt_start']:
                cur['opt_end'] = cur['opt_start'] + 0.05
            log_lines.append(
                f"  id={cur['id']} start: overlap fix {old:.3f}→{cur['opt_start']:.3f}s"
            )

    return new_cuts, log_lines


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="词边界硬约束：将 optimized_cuts 的切点对齐到词间隙"
    )
    parser.add_argument('--transcript',  required=True, help='medium 转写 JSON（词级时间戳）')
    parser.add_argument('--cuts',        required=True, help='optimized_cuts JSON 输入')
    parser.add_argument('--edl',         required=True, help='EDL JSON（用于确定 keep 顺序）')
    parser.add_argument('--out',         required=True, help='输出 corrected_cuts JSON')
    parser.add_argument('--window',      type=float, default=0.25,  help='搜索窗口半径（秒）')
    parser.add_argument('--fallback-pad',type=float, default=0.200, help='无间隙时的回退垫（秒）')
    args = parser.parse_args()

    with open(args.transcript, encoding='utf-8') as f:
        transcript = json.load(f)
    with open(args.cuts, encoding='utf-8') as f:
        opt_cuts = json.load(f)
    with open(args.edl, encoding='utf-8') as f:
        edl = json.load(f)

    words = transcript['words']
    new_cuts, log_lines = apply_word_boundary_constraints(
        edl, opt_cuts, words,
        window=args.window,
        fallback_pad=args.fallback_pad,
    )

    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(new_cuts, f, ensure_ascii=False, indent=2)

    print(f"[word_boundary] 词边界扫描完成：{len(log_lines)} 处违规已修正")
    for line in log_lines:
        print(line)
    print(f"[word_boundary] 输出: {args.out} ({len(new_cuts)} 段)")


if __name__ == '__main__':
    main()

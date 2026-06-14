#!/usr/bin/env python3
"""
ng_detect.py — LLM语义NG检测 → 重建EDL

原理：
  人工（LLM）分析转写文本，识别假起步/NG重拍/口吃重复，
  标注需要剔除的时间窗口（NG_WINDOWS），
  再从word级时间戳重新做停顿分割，生成干净的EDL。

用法：
  python3 src/ng_detect.py \
      --transcript output/t1_transcript.json \
      --out output/t1_edl_v3.json \
      [--pause 0.8] [--pad 0.15]
"""

import argparse
import json
import sys


# ═══════════════════════════════════════════════════════
#  LLM语义分析结果 — NG时间窗口
#  每条: (start_s, end_s, reason)
#  窗口内的词语从转写中剔除，不进入最终剪辑
# ═══════════════════════════════════════════════════════
NG_WINDOWS = [
    # ── test1.MP4 SRN901抗衰突破 ──────────────────────
    (
        28.14, 42.06,
        "[seg004] NG重拍: SRN901句第一遍说完'延长33%'(28.14s)后重拍，"
        "34.62s说'中胃的剩余寿命'（'中胃'是错词，应为'中位'）。"
        "整段删除，让[seg003]'延长33%'直接接[seg005]'衰弱进展缓解70%'。"
    ),
    (
        88.60, 91.64,
        "[seg026-027] 假起步: '那比如最有名的/就要帕雷米'——"
        "'帕雷米'是'雷帕梅素'的错读。"
        "[seg028]'比如最有名的雷帕梅素'是正确完整版，从91.64s接起。"
    ),
    (
        134.38, 150.94,
        "[seg047-053] 多次假起步: 连续4次尝试介绍'TAT2/环黄芪醇'均失败："
        "'这个就是关于TAT2环环其' → '这就是关于一个' → '就是关于TAT2啊' → "
        "'一个叫做环环其醇 它是一个端粒酶修复药' → '当年有个化合物啊/叫做TAT2'。"
        "[seg054]'当年有款化合物啊 它叫做TAT2 也就是环环其纯'是正确完整版，150.94s接起。"
    ),
    (
        160.36, 168.96,
        "[seg056-057] 假起步: '就是我们发现了原来端粒跟端粒酶影响着我们人的这个'——"
        "句子挂在'这个'未完。再试'就是我们发现啊'又未完。"
        "[seg058]'就是我们发现啊 端粒和端粒酶啊'接[seg059]'是影响…寿命…关键元素'是完整版，168.96s接起。"
    ),
    (
        168.96, 183.71,
        "[seg007整体] 语义级重复: seg007(168-175s)'端粒和端粒酶是影响哺乳动物寿命的关键元素'"
        "与seg008开头(183-189s)'这个发现指出了端粒和端粒酶在哺乳动物里面扮演了重要角色'语义完全重叠。"
        "删除seg007，直接从183.71s的完整版接起，避免同一信息重复说两遍。"
    ),
    (
        18.94, 21.74,
        "[词级异常] ASR词'的'时间戳18.94-21.74s = 2.8s，"
        "正常字长应<0.3s，说明说话人在'他们的'后有2.8s停顿/卡顿/重说，"
        "但ASR将其压缩为一个词。剔除这段异常，让'他们的'直接接'SRN901'。"
    ),
    (
        222.22, 225.08,
        "[seg073] 假起步: '那这些端 端粒激活酶'(222.22-224.00s)说错/卡顿，"
        "紧接[seg074]'那这类的端粒酶激活产品呢'(225.08s)是正确完整版。"
        "删除假起步，从225.08s接起。"
    ),
    # [seg082] 时间戳与[seg081]完全重叠，ASR双检测同一段音频，
    # 不开窗口——两者在音频中本是同一句话，不存在真正重复；
    # 且开窗会切断[seg081]句子"这个就是药物研发里面一个巨大的坑"。
    # (240.00, 241.50, "已注释掉"),
    (
        299.72, 300.00,
        "[seg119] NG重拍起始: '我这个天霸在之前介绍我这个东西'说错，"
        "立即重拍为[seg120]'天霸在之前介绍过这个东西'(300.00s)，"
        "剔除NG起始280ms。"
    ),
    (
        315.95, 328.42,
        "[seg126-133] 结尾假起步连串: '好/好/好/听到这/那一定是真爱粉了/"
        "今天我也是久违的来到了(切断)/好/好'——全部NG。"
        "[seg134]'那听到这肯定是真爱粉了'(328.42s)是正确完整版的结尾。"
    ),
]


def build_edl(transcript_path: str, out_path: str, pause_thresh=0.8, pad=0.15):
    with open(transcript_path, encoding="utf-8") as f:
        tr = json.load(f)

    words = tr.get("words", [])
    if not words:
        print("ERROR: 转写中没有词级时间戳", file=sys.stderr)
        sys.exit(1)

    print(f"[ng] 原始词数: {len(words)}")
    print(f"[ng] NG窗口数: {len(NG_WINDOWS)}")

    # ── 1. 过滤NG词 ──────────────────────────────────
    def in_ng(word):
        mid = (word["start"] + word["end"]) / 2
        for s, e, _ in NG_WINDOWS:
            if s <= mid < e:
                return True
        return False

    clean = [w for w in words if not in_ng(w)]
    print(f"[ng] 过滤后词数: {len(clean)} (剔除 {len(words)-len(clean)} 个词)")

    if not clean:
        print("ERROR: 过滤后没有词", file=sys.stderr)
        sys.exit(1)

    # ── 2. 停顿分割 ───────────────────────────────────
    segments = []
    seg_words = [clean[0]]

    for w in clean[1:]:
        gap = w["start"] - seg_words[-1]["end"]
        if gap >= pause_thresh:
            _flush(segments, seg_words, pad)
            seg_words = [w]
        else:
            seg_words.append(w)
    _flush(segments, seg_words, pad)

    # ── 3. 过滤过短片段 ───────────────────────────────
    segments = [s for s in segments if s["end_s"] - s["start_s"] >= 0.3]

    # 重新编号
    for i, s in enumerate(segments):
        s["id"] = i + 1

    total_s = sum(s["end_s"] - s["start_s"] for s in segments)
    print(f"[ng] EDL片段数: {len(segments)}, 总时长: {total_s:.1f}s")
    print()

    for s in segments:
        dur = s["end_s"] - s["start_s"]
        print(f"  [{s['id']:3d}] {s['start_s']:7.2f}-{s['end_s']:7.2f} ({dur:5.2f}s) {s['text'][:55]}")

    # ── 4. 保存 ───────────────────────────────────────
    edl = {
        "version": "1.0",
        "source": "reference/test1.MP4",
        "generated_by": "ng_detect.py (LLM语义NG检测)",
        "ng_windows": [
            {"start_s": s, "end_s": e, "reason": r}
            for s, e, r in NG_WINDOWS
        ],
        "segments": segments,
        "meta": {
            "keep_count": len(segments),
            "total_keep_s": round(total_s, 2),
            "ng_window_count": len(NG_WINDOWS),
            "words_removed": len(words) - len(clean),
        },
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(edl, f, ensure_ascii=False, indent=2)

    print(f"\n[ng] 保存 → {out_path}")
    return edl


def _flush(segments, seg_words, pad):
    st = max(0.0, round(seg_words[0]["start"] - pad, 3))
    et = round(seg_words[-1]["end"] + pad, 3)
    text = "".join(w["word"] for w in seg_words)
    segments.append({
        "id": len(segments) + 1,
        "start_s": st,
        "end_s": et,
        "keep": True,
        "text": text,
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LLM语义NG检测 → EDL")
    parser.add_argument("--transcript", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--pause", type=float, default=0.8)
    parser.add_argument("--pad", type=float, default=0.15)
    args = parser.parse_args()

    build_edl(args.transcript, args.out, args.pause, args.pad)

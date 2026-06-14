#!/usr/bin/env python3
"""
ng_detect.py — LLM 语义 NG 检测 → 重建 EDL

设计原则：
  本脚本不调任何外部 API，不引入额外费用。
  NG 窗口由运行本 skill 的 Agent（LLM）在对话中分析后写出，
  本脚本只做"执行"角色：读入 NG JSON → 过滤词列表 → 重建 EDL。

两种模式：

  A) manual — Agent 已分析并输出 ng_windows.json，本脚本读入执行
     python3 src/ng_detect.py manual \
         --transcript output/transcript.json \
         --ng-json    output/ng_windows.json \
         --out        output/edl_ng.json \
         [--source reference/原素材.MP4]

  B) prompt — 输出供 Agent 分析的 prompt 文本（Agent 读后在对话中给出 JSON）
     python3 src/ng_detect.py prompt \
         --transcript output/transcript.json \
         --script     materials/scripts/定稿.md \
         [--max-words 1500]

  Agent 工作流：
    1. 运行 `prompt` 模式，得到转写词表 + 定稿对照
    2. Agent（本 Claude 会话）阅读并找出所有 NG 区间
    3. Agent 将结果写入 output/ng_windows.json（格式见下）
    4. 运行 `manual` 模式，生成 EDL

  ng_windows.json 格式：
  [
    {"start_s": 28.14, "end_s": 42.06, "reason": "NG重拍：说错延长33%后重来"},
    {"start_s": 88.60, "end_s": 91.64, "reason": "假起步：帕雷米→雷帕梅素"},
    ...
  ]
"""

import argparse
import json
import os
import sys
import textwrap


# ═══════════════════════════════════════════════════════════════
#  Prompt 生成（供 Agent 在对话中分析）
# ═══════════════════════════════════════════════════════════════

ANALYSIS_PROMPT = textwrap.dedent("""\
请对比【定稿逐字稿】与【ASR 转写词表】，找出所有 NG 区间。

NG 类型（需标注）：
  - NG重拍：说到一半重头再来（有明显重拍意图）
  - 假起步：说了几个字就停顿重说
  - 口吃重复：同一词/字连续重复
  - 语义重复：同一句意思在前后完整说了两遍（删先保后）
  - 吊句：句子未完成就切断

不应删除：
  - 故意重复（强调修辞）
  - 停顿后继续同一句子（组织语言）
  - 定稿中明确包含的重复结构

输出格式（只输出 JSON 数组，不加任何解释）：
[
  {{"start_s": <float>, "end_s": <float>, "reason": "<简洁中文说明>"}},
  ...
]

精度要求：
  - start_s = NG 片段第一个词的 start 时间
  - end_s   = 正确版本开头词的 start 时间（即删到干净为止）
  - 精确到 0.01s

若无任何 NG，输出 []

分析完成后，将 JSON 写入 output/ng_windows.json，再运行：
  python3 src/ng_detect.py manual \\
      --transcript output/transcript.json \\
      --ng-json output/ng_windows.json \\
      --out output/edl_ng.json

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【定稿逐字稿】
{script}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【ASR 转写词表（格式：start_s  end_s  word）】
{word_table}
""")


def build_word_table(words: list, max_words: int = 1500) -> str:
    lines = []
    for w in words[:max_words]:
        s = w.get("start", 0)
        e = w.get("end", 0)
        word = w.get("word", "").strip()
        lines.append(f"{s:8.2f}  {e:8.2f}  {word}")
    if len(words) > max_words:
        lines.append(f"... (共 {len(words)} 词，此处截断至前 {max_words} 词；"
                     f"若需分析后半段，用 --offset {max_words})")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
#  EDL 构建
# ═══════════════════════════════════════════════════════════════

def build_edl_from_ng(words: list, ng_windows: list,
                      source: str, out_path: str,
                      pause_thresh: float = 0.8, pad: float = 0.15) -> dict:
    print(f"[ng] 原始词数: {len(words)}")
    print(f"[ng] NG窗口数: {len(ng_windows)}")

    def in_ng(word):
        mid = (word["start"] + word["end"]) / 2
        return any(w["start_s"] <= mid < w["end_s"] for w in ng_windows)

    clean = [w for w in words if not in_ng(w)]
    removed = len(words) - len(clean)
    print(f"[ng] 过滤后词数: {len(clean)} (剔除 {removed} 个词)")

    if not clean:
        print("ERROR: 过滤后没有任何词", file=sys.stderr)
        sys.exit(1)

    segments = []

    def flush(buf):
        st = max(0.0, round(buf[0]["start"] - pad, 3))
        et = round(buf[-1]["end"] + pad, 3)
        text = "".join(w["word"] for w in buf)
        segments.append({
            "id": len(segments) + 1,
            "start_s": st, "end_s": et,
            "keep": True, "decided_by": "llm",
            "text": text,
        })

    buf = [clean[0]]
    for w in clean[1:]:
        gap = w["start"] - buf[-1]["end"]
        if gap >= pause_thresh:
            flush(buf); buf = [w]
        else:
            buf.append(w)
    flush(buf)

    segments = [s for s in segments if s["end_s"] - s["start_s"] >= 0.3]
    for i, s in enumerate(segments):
        s["id"] = i + 1

    total_s = sum(s["end_s"] - s["start_s"] for s in segments)
    print(f"[ng] EDL片段数: {len(segments)}, 总时长: {total_s:.1f}s\n")

    for s in segments:
        dur = s["end_s"] - s["start_s"]
        print(f"  [{s['id']:3d}] {s['start_s']:7.2f}-{s['end_s']:7.2f} "
              f"({dur:5.2f}s) {s['text'][:55]}")

    edl = {
        "version": "2.0",
        "source": source,
        "generated_by": "ng_detect.py (Agent 语义 NG 检测)",
        "ng_windows": ng_windows,
        "segments": segments,
        "meta": {
            "keep_count": len(segments),
            "total_keep_s": round(total_s, 2),
            "ng_window_count": len(ng_windows),
            "words_removed": removed,
        },
    }

    if out_path:
        dirpath = os.path.dirname(out_path)
        if dirpath:
            os.makedirs(dirpath, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(edl, f, ensure_ascii=False, indent=2)
        print(f"\n[ng] EDL → {out_path}")

    return edl


# ═══════════════════════════════════════════════════════════════
#  子命令
# ═══════════════════════════════════════════════════════════════

def cmd_prompt(args):
    """输出供 Agent 分析的 prompt"""
    with open(args.transcript, encoding="utf-8") as f:
        tr = json.load(f)
    words = tr.get("words", [])
    if not words:
        print("ERROR: 转写中无词级时间戳", file=sys.stderr)
        sys.exit(1)

    script_text = ""
    if args.script:
        with open(args.script, encoding="utf-8") as f:
            script_text = f.read()

    word_table = build_word_table(words, args.max_words)
    print(ANALYSIS_PROMPT.format(script=script_text, word_table=word_table))


def cmd_manual(args):
    """读取 Agent 输出的 ng_windows.json，构建 EDL"""
    with open(args.transcript, encoding="utf-8") as f:
        tr = json.load(f)
    words = tr.get("words", [])
    if not words:
        print("ERROR: 转写中无词级时间戳", file=sys.stderr)
        sys.exit(1)

    if args.ng_json == "-":
        # 从 stdin 读（方便 pipe）
        ng_windows = json.load(sys.stdin)
    else:
        with open(args.ng_json, encoding="utf-8") as f:
            ng_windows = json.load(f)

    if not isinstance(ng_windows, list):
        print("ERROR: ng_windows.json 必须是数组", file=sys.stderr)
        sys.exit(1)

    source = args.source or args.transcript.replace("_transcript.json", ".MP4")
    build_edl_from_ng(words, ng_windows, source, args.out, args.pause, args.pad)


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(
        description="ng_detect.py — Agent 语义 NG 检测 → 重建 EDL（不调外部 API）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        典型工作流：
          # Step 1：输出分析 prompt，Agent 在对话中阅读并标注 NG 窗口
          python3 src/ng_detect.py prompt \\
              --transcript output/transcript.json \\
              --script materials/scripts/定稿.md

          # Step 2：Agent 将 NG JSON 写入文件（或由 Claude 直接写文件）
          #   → output/ng_windows.json

          # Step 3：重建 EDL
          python3 src/ng_detect.py manual \\
              --transcript output/transcript.json \\
              --ng-json output/ng_windows.json \\
              --out output/edl_ng.json
        """),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # prompt
    pp = sub.add_parser("prompt", help="输出供 Agent 分析的 prompt（不调 API）")
    pp.add_argument("--transcript", required=True, help="转写 JSON（含 words）")
    pp.add_argument("--script", default="", help="飞书定稿文本文件（可选）")
    pp.add_argument("--max-words", type=int, default=1500,
                    help="词表截断上限（默认 1500，防 context 超长）")
    pp.set_defaults(func=cmd_prompt)

    # manual
    pm = sub.add_parser("manual", help="读取 ng_windows.json，重建 EDL")
    pm.add_argument("--transcript", required=True)
    pm.add_argument("--ng-json", required=True,
                    help="NG 窗口 JSON 文件路径，或 '-' 从 stdin 读")
    pm.add_argument("--out", required=True, help="输出 EDL JSON 路径")
    pm.add_argument("--source", default="", help="素材路径（写入 EDL meta）")
    pm.add_argument("--pause", type=float, default=0.8, help="停顿分割阈值（秒）")
    pm.add_argument("--pad", type=float, default=0.15, help="切点前后留白（秒）")
    pm.set_defaults(func=cmd_manual)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

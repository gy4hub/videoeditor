#!/usr/bin/env python3
"""
ng_detect.py — LLM 语义 NG 检测 → 重建 EDL  (S3-1)

两种运行模式：

  A) 自动模式（调 Claude API 检测，推荐）
     python3 src/ng_detect.py auto \
         --transcript output/t1_transcript.json \
         --script     materials/scripts/定稿.md \
         --out        output/t1_edl_ng.json \
         [--api-key $ANTHROPIC_API_KEY] [--model claude-haiku-4-5-20251001]

  B) 手工模式（NG 窗口写入 JSON 文件，由 Claude 在对话中生成后调用）
     python3 src/ng_detect.py manual \
         --transcript output/t1_transcript.json \
         --ng-json    output/ng_windows.json \
         --out        output/t1_edl_ng.json

  ng_windows.json 格式：
  [
    {"start_s": 28.14, "end_s": 42.06, "reason": "NG重拍"},
    ...
  ]

  C) 只输出供 Claude 分析的 prompt（不调 API，便于复制到对话框手工运行）
     python3 src/ng_detect.py prompt \
         --transcript output/t1_transcript.json \
         --script     materials/scripts/定稿.md

设计原则：
  - temperature=0 保证同输入两次结果相同（确定性）
  - 输出 NG 窗口 JSON（供 manual 模式直接读入，也可存档）
  - 过滤精度：NG 窗口内的词语按中心点判断 (mid = (start+end)/2)
"""

import argparse
import json
import os
import sys
import textwrap


# ═══════════════════════════════════════════════════════════════
#  LLM Prompt
# ═══════════════════════════════════════════════════════════════

SYSTEM_PROMPT = textwrap.dedent("""\
你是一位专业的视频粗剪助理。任务：对比主播的【实际录音转写】与【定稿逐字稿】，
找出所有需要剔除的 NG 片段（假起步、NG重拍、口吃重复、语义重复、明显错词/吊句）。

输出规则（严格遵守）：
1. 只输出 JSON 数组，不加任何解释文字、代码块标记。
2. 每条记录格式：{"start_s": <float>, "end_s": <float>, "reason": "<简洁中文原因>"}
3. 时间戳来自转写中的词级时间戳，精确到 0.01s。
4. end_s 取"好的那一遍"开始前的最后一个词的 end 时间（即删到干净为止）。
5. 若无任何 NG，输出空数组 []。
6. 不要删除任何属于定稿内容的正确段落，宁可少删不要误删。
""")

USER_PROMPT_TEMPLATE = textwrap.dedent("""\
【定稿逐字稿】
{script}

【ASR 转写（词级时间戳片段，每行格式：start_s end_s word）】
{word_table}

请分析并输出 NG 窗口 JSON 数组：
""")


def build_word_table(words: list, max_words: int = 2000) -> str:
    """把词列表格式化为对人和 LLM 都易读的表格（限制长度防 context 溢出）"""
    lines = []
    for w in words[:max_words]:
        s = w.get("start", 0)
        e = w.get("end", 0)
        word = w.get("word", "").strip()
        lines.append(f"{s:8.2f}  {e:8.2f}  {word}")
    if len(words) > max_words:
        lines.append(f"... (省略 {len(words)-max_words} 词，超出 {max_words} 词上限)")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
#  API 调用
# ═══════════════════════════════════════════════════════════════

def call_claude_api(prompt: str, system: str, model: str, api_key: str) -> str:
    """调用 Anthropic Messages API，返回文本内容"""
    try:
        import anthropic
    except ImportError:
        print("[ng] 缺少 anthropic 库，正在安装...", file=sys.stderr)
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "anthropic",
                               "--break-system-packages", "-q"])
        import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model,
        max_tokens=4096,
        temperature=0,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


def parse_ng_json(text: str) -> list:
    """从 LLM 响应中提取 JSON 数组"""
    text = text.strip()
    # 去掉可能的 ```json ... ``` 包裹
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(l for l in lines if not l.startswith("```"))
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        # 尝试找第一个 [ ... ] 块
        import re
        m = re.search(r"\[.*?\]", text, re.DOTALL)
        if m:
            result = json.loads(m.group())
        else:
            raise ValueError(f"无法解析 LLM 输出为 JSON：\n{text[:300]}")
    if not isinstance(result, list):
        raise ValueError(f"LLM 输出不是数组：{type(result)}")
    return result


# ═══════════════════════════════════════════════════════════════
#  EDL 构建（与原 build_edl 逻辑一致）
# ═══════════════════════════════════════════════════════════════

def build_edl_from_ng(words: list, ng_windows: list,
                      source: str, out_path: str,
                      pause_thresh: float = 0.8, pad: float = 0.15) -> dict:
    """
    根据 NG 窗口过滤词列表，重新做停顿分割，生成 EDL JSON。
    """
    print(f"[ng] 原始词数: {len(words)}")
    print(f"[ng] NG窗口数: {len(ng_windows)}")

    def in_ng(word):
        mid = (word["start"] + word["end"]) / 2
        for w in ng_windows:
            if w["start_s"] <= mid < w["end_s"]:
                return True
        return False

    clean = [w for w in words if not in_ng(w)]
    removed = len(words) - len(clean)
    print(f"[ng] 过滤后词数: {len(clean)} (剔除 {removed} 个词)")

    if not clean:
        print("ERROR: 过滤后没有任何词", file=sys.stderr)
        sys.exit(1)

    # 停顿分割
    segments = []

    def flush(seg_words):
        st = max(0.0, round(seg_words[0]["start"] - pad, 3))
        et = round(seg_words[-1]["end"] + pad, 3)
        text = "".join(w["word"] for w in seg_words)
        segments.append({
            "id": len(segments) + 1,
            "start_s": st,
            "end_s": et,
            "keep": True,
            "decided_by": "llm",
            "text": text,
        })

    buf = [clean[0]]
    for w in clean[1:]:
        gap = w["start"] - buf[-1]["end"]
        if gap >= pause_thresh:
            flush(buf)
            buf = [w]
        else:
            buf.append(w)
    flush(buf)

    # 过滤过短片段
    segments = [s for s in segments if s["end_s"] - s["start_s"] >= 0.3]
    for i, s in enumerate(segments):
        s["id"] = i + 1

    total_s = sum(s["end_s"] - s["start_s"] for s in segments)
    print(f"[ng] EDL片段数: {len(segments)}, 总时长: {total_s:.1f}s\n")

    for s in segments:
        dur = s["end_s"] - s["start_s"]
        print(f"  [{s['id']:3d}] {s['start_s']:7.2f}-{s['end_s']:7.2f} ({dur:5.2f}s) "
              f"{s['text'][:55]}")

    edl = {
        "version": "2.0",
        "source": source,
        "generated_by": "ng_detect.py v2 (LLM 语义 NG 自动检测)",
        "ng_windows": ng_windows,
        "segments": segments,
        "meta": {
            "keep_count": len(segments),
            "total_keep_s": round(total_s, 2),
            "ng_window_count": len(ng_windows),
            "words_removed": removed,
        },
    }

    os.makedirs(os.path.dirname(out_path), exist_ok=True) if os.path.dirname(out_path) else None
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(edl, f, ensure_ascii=False, indent=2)
    print(f"\n[ng] EDL 已保存 → {out_path}")
    return edl


# ═══════════════════════════════════════════════════════════════
#  子命令
# ═══════════════════════════════════════════════════════════════

def cmd_auto(args):
    """模式 A：调 Claude API 自动检测"""
    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: 需要 --api-key 或环境变量 ANTHROPIC_API_KEY", file=sys.stderr)
        sys.exit(1)

    with open(args.transcript, encoding="utf-8") as f:
        tr = json.load(f)
    words = tr.get("words", [])
    if not words:
        print("ERROR: 转写中没有词级时间戳", file=sys.stderr)
        sys.exit(1)

    with open(args.script, encoding="utf-8") as f:
        script_text = f.read()

    word_table = build_word_table(words)
    prompt = USER_PROMPT_TEMPLATE.format(script=script_text, word_table=word_table)

    print(f"[ng] 调用 Claude API (model={args.model}) ...")
    raw = call_claude_api(prompt, SYSTEM_PROMPT, args.model, api_key)

    # 保存原始响应供审计
    raw_path = args.out.replace(".json", "_llm_raw.txt")
    with open(raw_path, "w", encoding="utf-8") as f:
        f.write(raw)
    print(f"[ng] LLM 原始响应 → {raw_path}")

    ng_windows = parse_ng_json(raw)
    print(f"[ng] 解析到 {len(ng_windows)} 个 NG 窗口")
    for w in ng_windows:
        print(f"  {w['start_s']:.2f}-{w['end_s']:.2f}  {w['reason'][:60]}")

    # 同时保存 NG JSON 供 manual 模式复用
    ng_json_path = args.out.replace(".json", "_ng_windows.json")
    with open(ng_json_path, "w", encoding="utf-8") as f:
        json.dump(ng_windows, f, ensure_ascii=False, indent=2)
    print(f"[ng] NG 窗口 JSON → {ng_json_path}")

    source = args.source or args.transcript.replace("_transcript.json", ".MP4")
    build_edl_from_ng(words, ng_windows, source, args.out, args.pause, args.pad)


def cmd_manual(args):
    """模式 B：读取已有 NG JSON 构建 EDL"""
    with open(args.transcript, encoding="utf-8") as f:
        tr = json.load(f)
    words = tr.get("words", [])

    with open(args.ng_json, encoding="utf-8") as f:
        ng_windows = json.load(f)

    source = args.source or args.transcript.replace("_transcript.json", ".MP4")
    build_edl_from_ng(words, ng_windows, source, args.out, args.pause, args.pad)


def cmd_prompt(args):
    """模式 C：只打印 prompt，不调 API"""
    with open(args.transcript, encoding="utf-8") as f:
        tr = json.load(f)
    words = tr.get("words", [])

    with open(args.script, encoding="utf-8") as f:
        script_text = f.read()

    word_table = build_word_table(words)
    prompt = USER_PROMPT_TEMPLATE.format(script=script_text, word_table=word_table)

    print("=" * 60)
    print("SYSTEM:")
    print(SYSTEM_PROMPT)
    print("=" * 60)
    print("USER:")
    print(prompt)
    print("=" * 60)
    print("(把以上内容粘贴到 Claude 对话框，将返回的 JSON 保存为 ng_windows.json，")
    print(" 再用 manual 模式：python3 src/ng_detect.py manual --ng-json ... --transcript ... --out ...)")


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(
        description="ng_detect.py v2 — LLM 语义 NG 检测 → 重建 EDL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        使用示例：
          # 自动模式（推荐）
          python3 src/ng_detect.py auto \\
              --transcript output/t1_transcript.json \\
              --script materials/scripts/定稿.md \\
              --out output/t1_edl_ng.json

          # 手工模式（Claude 对话框生成 ng_windows.json 后）
          python3 src/ng_detect.py manual \\
              --transcript output/t1_transcript.json \\
              --ng-json output/ng_windows.json \\
              --out output/t1_edl_ng.json

          # 只输出 prompt（复制到对话框）
          python3 src/ng_detect.py prompt \\
              --transcript output/t1_transcript.json \\
              --script materials/scripts/定稿.md
        """),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # ── auto ──────────────────────────────────────────────────
    pa = sub.add_parser("auto", help="调 Claude API 自动检测 NG 并生成 EDL")
    pa.add_argument("--transcript", required=True, help="转写 JSON（含 words 词级时间戳）")
    pa.add_argument("--script", required=True, help="飞书定稿逐字稿文本文件")
    pa.add_argument("--out", required=True, help="输出 EDL JSON 路径")
    pa.add_argument("--source", default="", help="素材路径（写入 EDL，可选）")
    pa.add_argument("--api-key", default="", help="Anthropic API Key（可用环境变量 ANTHROPIC_API_KEY）")
    pa.add_argument("--model", default="claude-haiku-4-5-20251001",
                    help="Claude 模型（默认 claude-haiku-4-5-20251001，便宜快）")
    pa.add_argument("--pause", type=float, default=0.8, help="停顿分割阈值（秒，默认 0.8）")
    pa.add_argument("--pad", type=float, default=0.15, help="切点前后留白（秒，默认 0.15）")

    # ── manual ────────────────────────────────────────────────
    pm = sub.add_parser("manual", help="读取已有 NG JSON 构建 EDL")
    pm.add_argument("--transcript", required=True)
    pm.add_argument("--ng-json", required=True, help="NG 窗口 JSON 文件")
    pm.add_argument("--out", required=True)
    pm.add_argument("--source", default="")
    pm.add_argument("--pause", type=float, default=0.8)
    pm.add_argument("--pad", type=float, default=0.15)

    # ── prompt ────────────────────────────────────────────────
    pp = sub.add_parser("prompt", help="只打印分析 prompt，不调 API")
    pp.add_argument("--transcript", required=True)
    pp.add_argument("--script", required=True)

    args = p.parse_args()
    {"auto": cmd_auto, "manual": cmd_manual, "prompt": cmd_prompt}[args.cmd](args)


if __name__ == "__main__":
    main()

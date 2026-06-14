#!/usr/bin/env python3
"""
subtitle.py — 字幕生成与烧录管线  (S3-4/S3-5)

功能：
  A) generate：飞书定稿 + ASR 词级时间戳 → 中英双语 SRT
  B) burn    ：SRT → ffmpeg 金陵体烧录（中12pt/英8pt/白/60%阴影）
  C) preview ：截图指定时间点的字幕效果（用于验收）

用法：
  # 生成 SRT
  python3 src/subtitle.py generate \\
      --transcript output/t1_transcript.json \\
      --script     materials/scripts/定稿.md \\
      --edl        output/t1_edl_snapped.json \\
      --out        output/t1_subtitle.srt \\
      [--api-key   $ANTHROPIC_API_KEY]

  # 烧录字幕到视频
  python3 src/subtitle.py burn \\
      --video output/roughcut_hd.mp4 \\
      --srt   output/t1_subtitle.srt \\
      --out   output/roughcut_hd_sub.mp4 \\
      [--font 金陵体] [--cn-size 12] [--en-size 8]

  # 截图验收（t=30s 的字幕效果）
  python3 src/subtitle.py preview \\
      --video output/roughcut_hd_sub.mp4 \\
      --time 30 --out output/subtitle_preview_30s.png

SRT 时间轴策略：
  每行定稿句子对齐到 ASR 词级时间戳：
  - 找定稿句中关键词（长度≥2字）在转写中最早匹配的词
  - 句子 start = 该词的 start_s
  - 句子 end   = 下一句 start - 0.05s（避免闪烁）
  - 若匹配不到，从上一句 end 顺延

字幕规范：
  - 中文行：金陵体（或备用微软雅黑/PingFang）、字号12、白色、60%黑色阴影
  - 英文行：同字体、字号8、白色、60%黑色阴影
  - 位置：底部居中，留边 30px
  - 每屏显示一行中文 + 一行英文
"""

import argparse
import json
import os
import re
import subprocess
import sys
import textwrap

# ═══════════════════════════════════════════════════════════════
#  字体配置（按优先级尝试）
# ═══════════════════════════════════════════════════════════════

FONT_CANDIDATES = [
    "金陵体",
    "STSong",               # macOS 宋体
    "PingFang SC",          # macOS 苹方
    "Microsoft YaHei",      # Windows 微软雅黑
    "Noto Sans CJK SC",     # Linux
    "Arial Unicode MS",
]

# 字幕样式（ASS 格式）
ASS_STYLE_TEMPLATE = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Chinese,{font},54,&H00FFFFFF,&H000000FF,&H00000000,&HAA000000,0,0,0,0,100,100,0,0,1,2,3,2,30,30,30,1
Style: English,{font},38,&H00FFFFFF,&H000000FF,&H00000000,&HAA000000,0,0,0,0,100,100,0,0,1,2,3,2,30,30,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# 注：字号说明（PlayRes 1920x1080 下的 ASS pt）
# Chinese: 54 → 渲染约 12pt @ 1080p
# English: 38 → 渲染约 8pt @ 1080p
# BackColour: &HAA000000 = 60% 不透明黑色背景阴影


# ═══════════════════════════════════════════════════════════════
#  SRT 生成
# ═══════════════════════════════════════════════════════════════

def parse_script_lines(script_path: str) -> list[str]:
    """读取飞书定稿，提取非空非标题行作为字幕单元"""
    with open(script_path, encoding="utf-8") as f:
        raw = f.read()
    lines = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # 跳过 Markdown 标题
        if line.startswith("#"):
            continue
        # 跳过分隔线
        if re.match(r"^[-=─]{3,}$", line):
            continue
        lines.append(line)
    return lines


def align_script_to_words(script_lines: list[str], words: list[dict]) -> list[dict]:
    """
    将定稿句对齐到 ASR 词级时间戳，返回带时间的字幕条目。
    策略：滑动窗口，找句子关键词（≥2字中文）在词列表中的位置。
    """
    entries = []
    word_texts = [w.get("word", "").strip() for w in words]
    word_starts = [w.get("start", 0.0) for w in words]
    word_ends = [w.get("end", 0.0) for w in words]

    cursor = 0  # 词列表游标（单调递增，防止倒退匹配）

    for sent in script_lines:
        # 提取关键词（≥2字中文片段）
        keywords = re.findall(r"[一-鿿]{2,}", sent)
        matched_idx = None

        for kw in keywords:
            # 在 cursor 之后找最早出现的词
            for j in range(cursor, len(word_texts)):
                # 检查从 j 开始是否有词含 kw 的子串（分散匹配）
                window_text = "".join(word_texts[j:j+10])
                if kw in window_text:
                    matched_idx = j
                    break
            if matched_idx is not None:
                break

        if matched_idx is not None:
            start_s = word_starts[matched_idx]
            cursor = matched_idx + 1
        else:
            # 匹配不到：从上一条的 end 顺延 0.1s
            start_s = (entries[-1]["end_s"] + 0.1) if entries else 0.0

        entries.append({
            "text": sent,
            "start_s": start_s,
            "end_s": None,  # 后面填充
        })

    # 填充 end_s（下一句 start - 0.05s，最后一句 + 3s）
    for i, e in enumerate(entries):
        if i + 1 < len(entries):
            e["end_s"] = max(e["start_s"] + 0.5, entries[i + 1]["start_s"] - 0.05)
        else:
            e["end_s"] = e["start_s"] + 3.0

    return entries


def translate_to_english(lines: list[str], api_key: str = "",
                          model: str = "claude-haiku-4-5-20251001") -> list[str]:
    """调 Claude API 批量翻译中文→英文字幕"""
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("[subtitle] 无 API Key，跳过英译（英文行留空）", file=sys.stderr)
        return [""] * len(lines)

    try:
        import anthropic
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "anthropic",
                               "--break-system-packages", "-q"])
        import anthropic

    client = anthropic.Anthropic(api_key=api_key)

    # 批量翻译（每批 20 句，控制 token）
    batch_size = 20
    translations = []

    for i in range(0, len(lines), batch_size):
        batch = lines[i:i+batch_size]
        numbered = "\n".join(f"{j+1}. {t}" for j, t in enumerate(batch))
        prompt = (
            "将以下中文视频字幕逐句翻译为简洁自然的英文。\n"
            "规则：①保持序号②一句一行③不加解释④医学/保健术语直译或保留拼音\n\n"
            f"{numbered}\n\n英文（保留序号）："
        )
        msg = client.messages.create(
            model=model, max_tokens=2048, temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        # 解析"N. text"格式
        for line in raw.splitlines():
            m = re.match(r"^\d+\.\s+(.*)", line)
            if m:
                translations.append(m.group(1).strip())

    # 补齐（如果 API 漏行）
    while len(translations) < len(lines):
        translations.append("")

    return translations[:len(lines)]


def secs_to_srt_time(s: float) -> str:
    """秒数 → SRT 时间格式 HH:MM:SS,mmm"""
    s = max(0.0, s)
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(s % 60)
    ms = int(round((s - int(s)) * 1000))
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def secs_to_ass_time(s: float) -> str:
    """秒数 → ASS 时间格式 H:MM:SS.cs（厘秒）"""
    s = max(0.0, s)
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(s % 60)
    cs = int(round((s - int(s)) * 100))
    return f"{h}:{m:02d}:{sec:02d}.{cs:02d}"


def write_bilingual_srt(entries: list[dict], translations: list[str], out_path: str):
    """写双语 SRT 文件（中文在上，英文在下）"""
    os.makedirs(os.path.dirname(out_path), exist_ok=True) if os.path.dirname(out_path) else None
    lines = []
    for i, (e, en) in enumerate(zip(entries, translations)):
        idx = i + 1
        start = secs_to_srt_time(e["start_s"])
        end = secs_to_srt_time(e["end_s"])
        cn = e["text"]
        lines.append(f"{idx}")
        lines.append(f"{start} --> {end}")
        lines.append(cn)
        if en:
            lines.append(en)
        lines.append("")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[subtitle] SRT → {out_path} ({len(entries)} 条)")


def write_bilingual_ass(entries: list[dict], translations: list[str],
                        out_path: str, font: str = "PingFang SC"):
    """写双语 ASS 文件（支持独立样式：Chinese 和 English）"""
    os.makedirs(os.path.dirname(out_path), exist_ok=True) if os.path.dirname(out_path) else None
    header = ASS_STYLE_TEMPLATE.format(font=font)
    events = []
    for e, en in zip(entries, translations):
        st = secs_to_ass_time(e["start_s"])
        et = secs_to_ass_time(e["end_s"])
        cn = e["text"].replace(",", "，").replace("\n", " ")
        # 中文行
        events.append(f"Dialogue: 0,{st},{et},Chinese,,0,0,0,,{cn}")
        # 英文行（用 \N 在下方）
        if en:
            en_clean = en.replace(",", " ").replace("\n", " ")
            events.append(f"Dialogue: 0,{st},{et},English,,0,0,0,,{en_clean}")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(header)
        f.write("\n".join(events) + "\n")
    print(f"[subtitle] ASS → {out_path} ({len(entries)} 条)")


# ═══════════════════════════════════════════════════════════════
#  ffmpeg 字幕烧录
# ═══════════════════════════════════════════════════════════════

def detect_font() -> str:
    """在系统中找可用中文字体"""
    try:
        r = subprocess.run(["fc-list", ":lang=zh", "family"],
                           capture_output=True, text=True, timeout=10)
        available = r.stdout.lower()
        for font in FONT_CANDIDATES:
            if font.lower() in available:
                return font
    except Exception:
        pass
    return FONT_CANDIDATES[-1]  # 兜底


def burn_subtitles(video: str, srt_path: str, out_path: str,
                   font: str = "", cn_size: int = 54, en_size: int = 38,
                   crf: int = 20, preset: str = "veryfast") -> bool:
    """
    用 ffmpeg subtitles 滤镜烧录 SRT。
    字幕样式通过 force_style 参数注入（金陵体/白色/60%阴影）。
    """
    if not font:
        font = detect_font()
    print(f"[subtitle] 使用字体: {font}")

    # force_style 语法（SRT 烧录）
    # BackColour: &H99000000 ≈ 60% 不透明黑色背景
    force_style = (
        f"FontName={font},FontSize={cn_size},"
        "PrimaryColour=&H00FFFFFF,"
        "BackColour=&H99000000,"
        "BorderStyle=3,Outline=0,Shadow=0,"
        "Alignment=2,MarginV=30"
    )

    # 使用绝对路径并转义
    srt_abs = os.path.abspath(srt_path).replace("\\", "/").replace(":", "\\:")
    vf = f"subtitles='{srt_abs}':force_style='{force_style}'"

    cmd = [
        "ffmpeg", "-y",
        "-i", video,
        "-vf", vf,
        "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
        "-c:a", "copy",
        "-movflags", "+faststart",
        out_path,
    ]
    print(f"[subtitle] ffmpeg 烧录字幕 → {out_path}")
    try:
        r = subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError:
        print("[subtitle] ERROR: ffmpeg 烧录失败", file=sys.stderr)
        return False


def burn_subtitles_ass(video: str, ass_path: str, out_path: str,
                       crf: int = 20, preset: str = "veryfast") -> bool:
    """用 ASS 文件烧录（比 SRT force_style 更精确，支持双样式）"""
    ass_abs = os.path.abspath(ass_path).replace("\\", "/").replace(":", "\\:")
    cmd = [
        "ffmpeg", "-y",
        "-i", video,
        "-vf", f"ass='{ass_abs}'",
        "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
        "-c:a", "copy",
        "-movflags", "+faststart",
        out_path,
    ]
    print(f"[subtitle] ffmpeg ASS 烧录 → {out_path}")
    try:
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError:
        print("[subtitle] ERROR: ffmpeg ASS 烧录失败", file=sys.stderr)
        return False


# ═══════════════════════════════════════════════════════════════
#  子命令
# ═══════════════════════════════════════════════════════════════

def cmd_generate(args):
    """生成双语 SRT/ASS"""
    with open(args.transcript, encoding="utf-8") as f:
        tr = json.load(f)
    words = tr.get("words", [])
    if not words:
        print("ERROR: 转写中无词级时间戳", file=sys.stderr)
        sys.exit(1)

    # 如果有 EDL，过滤掉被删除区间的词
    if args.edl and os.path.exists(args.edl):
        with open(args.edl, encoding="utf-8") as f:
            edl = json.load(f)
        keep_segs = [(s["start_s"], s["end_s"]) for s in edl.get("segments", [])
                     if s.get("keep", True)]
        def in_keep(w):
            mid = (w["start"] + w["end"]) / 2
            return any(st <= mid < et for st, et in keep_segs)
        words = [w for w in words if in_keep(w)]
        print(f"[subtitle] EDL 过滤后词数: {len(words)}")

    script_lines = parse_script_lines(args.script)
    print(f"[subtitle] 定稿句数: {len(script_lines)}")

    entries = align_script_to_words(script_lines, words)

    # 英文翻译
    cn_texts = [e["text"] for e in entries]
    if not args.no_translate:
        print("[subtitle] 调用 Claude API 英译...")
        translations = translate_to_english(cn_texts, args.api_key, args.model)
    else:
        translations = [""] * len(cn_texts)

    # 输出 SRT
    write_bilingual_srt(entries, translations, args.out)

    # 同时输出 ASS（更好的双样式支持）
    if args.ass:
        ass_path = args.out.replace(".srt", ".ass")
        font = detect_font()
        write_bilingual_ass(entries, translations, ass_path, font)
        print(f"[subtitle] ASS 格式 → {ass_path}")


def cmd_burn(args):
    """烧录字幕到视频"""
    out_path = args.out or args.video.replace(".mp4", "_sub.mp4")

    # 优先用 ASS（更精确）
    ass_path = args.srt.replace(".srt", ".ass")
    if os.path.exists(ass_path):
        ok = burn_subtitles_ass(args.video, ass_path, out_path, args.crf, args.preset)
    else:
        ok = burn_subtitles(args.video, args.srt, out_path,
                            args.font, args.cn_size, args.en_size,
                            args.crf, args.preset)
    if not ok:
        sys.exit(1)
    print(f"[subtitle] 成品 → {out_path}")


def cmd_preview(args):
    """截图指定时间点的字幕效果"""
    out_path = args.out or f"subtitle_preview_{int(args.time)}s.png"
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(args.time),
        "-i", args.video,
        "-frames:v", "1",
        "-q:v", "2",
        out_path,
    ]
    subprocess.run(cmd, check=True)
    print(f"[subtitle] 预览截图 → {out_path}")


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(description="subtitle.py — 字幕生成与烧录管线")
    sub = p.add_subparsers(dest="cmd", required=True)

    # generate
    pg = sub.add_parser("generate", help="生成双语 SRT")
    pg.add_argument("--transcript", required=True, help="转写 JSON（含 words）")
    pg.add_argument("--script", required=True, help="飞书定稿文本文件")
    pg.add_argument("--edl", default="", help="EDL JSON（可选，用于过滤被删区间的词）")
    pg.add_argument("--out", required=True, help="输出 SRT 路径")
    pg.add_argument("--api-key", default="", help="Anthropic API Key")
    pg.add_argument("--model", default="claude-haiku-4-5-20251001")
    pg.add_argument("--no-translate", action="store_true", help="不生成英文翻译")
    pg.add_argument("--ass", action="store_true", help="同时输出 ASS 格式")
    pg.set_defaults(func=cmd_generate)

    # burn
    pb = sub.add_parser("burn", help="烧录 SRT/ASS 字幕到视频")
    pb.add_argument("--video", required=True)
    pb.add_argument("--srt", required=True, help="SRT 文件（自动检测同名 .ass）")
    pb.add_argument("--out", default="", help="输出视频路径")
    pb.add_argument("--font", default="", help="字体名（留空自动检测）")
    pb.add_argument("--cn-size", type=int, default=54, help="中文 ASS 字号（默认54=12pt@1080p）")
    pb.add_argument("--en-size", type=int, default=38, help="英文 ASS 字号（默认38=8pt@1080p）")
    pb.add_argument("--crf", type=int, default=20)
    pb.add_argument("--preset", default="veryfast")
    pb.set_defaults(func=cmd_burn)

    # preview
    pp = sub.add_parser("preview", help="截图字幕效果")
    pp.add_argument("--video", required=True)
    pp.add_argument("--time", type=float, default=30.0)
    pp.add_argument("--out", default="")
    pp.set_defaults(func=cmd_preview)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

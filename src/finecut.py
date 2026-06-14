#!/usr/bin/env python3
"""
finecut.py — 精剪管线：HyperFrames 动画/图表 + B-roll 插入  (Sprint 4)

架构：
  roughcut_hd.mp4  +  finecut_spec.json  →  finecut.mp4

finecut_spec.json 由 Agent（本 Claude 会话）分析 EDL + 定稿后输出，格式：
  {
    "source_video": "output/roughcut_hd.mp4",
    "overlays": [
      {
        "id": "chart_srn901",
        "type": "chart_bar",          // chart_bar | chart_stat | text_highlight | broll
        "insert_mode": "insert",      // insert = 插入（替换对应时段 A-roll 视频，音频继续）
                                      // cutaway = 切到 B-roll（视频+音频全切换）
        "at_s": 28.0,                 // 在 roughcut_hd.mp4 的哪个时间点插入
        "duration_s": 5.0,            // 插入时长（秒）
        "vars": {                     // 传给 HyperFrames --variables 的参数
          "title": "SRN901延长中位寿命",
          "unit": "%",
          "bars": [
            {"label": "对照组",  "value": 100, "color": "#888"},
            {"label": "SRN901", "value": 133, "color": "#4a9eff"}
          ]
        }
      },
      {
        "id": "broll_lab",
        "type": "broll",
        "insert_mode": "cutaway",
        "at_s": 65.0,
        "duration_s": 4.0,
        "vars": {
          "video": "reference/broll_lab.mp4",  // B-roll 文件路径
          "lower_third": "实验室场景模拟"        // 可选：下方说明文字
        }
      }
    ]
  }

insert_mode 说明：
  insert  —— 动画/图表段落：A-roll 视频被替换，但 A-roll 音频继续播放（配音不中断）
  cutaway —— B-roll 切换：视频和音频全部切换到 B-roll

用法：
  # 生成 finecut_spec.json（Agent 分析 EDL + 定稿后写入）
  # 然后运行：
  python3 src/finecut.py run \\
      --spec   output/finecut_spec.json \\
      --outdir output/finecut/ \\
      [--hf-dir skills/hyperframes-test]

  # 只生成 HyperFrames HTML（不渲染，用于在 Mac 手工检查）
  python3 src/finecut.py generate-html \\
      --spec   output/finecut_spec.json \\
      --outdir output/finecut/

注意：
  HyperFrames render 需要 Chrome（Linux ARM64 沙箱不支持）。
  在 Mac 上运行本脚本，或用 generate-html 模式生成 HTML 后手工渲染。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time

SRC_DIR     = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SRC_DIR)
HF_DIR_DEFAULT = os.path.join(PROJECT_DIR, "skills", "hyperframes-test")

# 模板文件名映射
TEMPLATE_MAP = {
    "chart_bar":      "compositions/chart-bar.html",
    "chart_stat":     "compositions/chart-stat.html",
    "text_highlight": "compositions/text-highlight.html",
    "broll":          None,  # B-roll 不用 HyperFrames 模板
}


# ═══════════════════════════════════════════════════════════════
#  工具函数
# ═══════════════════════════════════════════════════════════════

def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run(label: str, cmd: list, check: bool = True, timeout: int = 600) -> bool:
    log(f"▶ {label}")
    t0 = time.time()
    try:
        subprocess.run(cmd, check=check, timeout=timeout)
        log(f"✅ {label} ({time.time()-t0:.1f}s)")
        return True
    except subprocess.CalledProcessError as e:
        log(f"❌ {label} 失败 (exit {e.returncode})")
        return False
    except subprocess.TimeoutExpired:
        log(f"❌ {label} 超时")
        return False


def get_duration(video: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", video],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(r.stdout)["format"]["duration"])


# ═══════════════════════════════════════════════════════════════
#  HyperFrames 渲染（insert 类型）
# ═══════════════════════════════════════════════════════════════

def render_hf_segment(overlay: dict, hf_dir: str, outdir: str) -> str | None:
    """
    渲染单个 HyperFrames 动画段落，返回 MP4 路径。
    在临时目录创建 HyperFrames 项目，注入 variables，执行 render。
    """
    oid = overlay["id"]
    otype = overlay["type"]
    template_rel = TEMPLATE_MAP.get(otype)
    if not template_rel:
        log(f"  [hf] 类型 {otype} 无 HyperFrames 模板，跳过")
        return None

    template_path = os.path.join(hf_dir, template_rel)
    if not os.path.exists(template_path):
        log(f"  [hf] 模板不存在: {template_path}")
        return None

    # 创建临时 HyperFrames 项目
    tmpdir = tempfile.mkdtemp(prefix=f"hf_{oid}_")
    try:
        # 写 index.html（模板内容）
        shutil.copy(template_path, os.path.join(tmpdir, "index.html"))

        # 写 meta.json
        with open(os.path.join(tmpdir, "meta.json"), "w") as f:
            json.dump({"id": oid, "name": oid}, f)

        # 写 hyperframes.json
        with open(os.path.join(tmpdir, "hyperframes.json"), "w") as f:
            json.dump({
                "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
                "registry": "https://raw.githubusercontent.com/heygen-com/hyperframes/main/registry",
                "paths": {"blocks": "compositions", "components": "compositions/components", "assets": "assets"},
            }, f)

        # 变量（注入 duration）
        vars_dict = dict(overlay.get("vars", {}))
        vars_dict.setdefault("duration", overlay.get("duration_s", 5))

        out_mp4 = os.path.join(outdir, f"{oid}.mp4")

        # hyperframes CLI 路径
        hf_bin = os.path.join(hf_dir, "node_modules", ".bin", "hyperframes")
        if not os.path.exists(hf_bin):
            # fallback: npx
            hf_bin = None

        cmd = (
            [hf_bin, "render", tmpdir] if hf_bin
            else ["npx", "--prefix", hf_dir, "hyperframes", "render", tmpdir]
        )
        cmd += [
            "--output", out_mp4,
            "--fps", "30",
            "--quality", "standard",
            "--no-browser-gpu",
            "--variables", json.dumps(vars_dict, ensure_ascii=False),
        ]

        ok = run(f"HyperFrames render [{oid}]", cmd, timeout=300)
        return out_mp4 if ok and os.path.exists(out_mp4) else None

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════
#  ffmpeg 操作
# ═══════════════════════════════════════════════════════════════

def build_finecut(source_video: str, overlays_sorted: list, outdir: str, out_path: str) -> bool:
    """
    按时间顺序拼接 source_video 与各个 overlay/cutaway 片段。

    策略：
      把 source_video 切成若干段，在 at_s 处插入 overlay MP4（或 B-roll），
      最后 concat 全部片段。

      insert 模式：音频取 source_video（narration 不中断），视频取 overlay MP4
      cutaway 模式：视频+音频全取 B-roll（或用 source_video 静音视频 + B-roll 音频）
    """
    src_dur = get_duration(source_video)
    log(f"[finecut] 原视频时长: {src_dur:.2f}s, overlay 数: {len(overlays_sorted)}")

    # 构建"切点"列表：(start_s, end_s, type, path, mode)
    # type = 'source' | 'overlay'
    segments = []
    cursor = 0.0

    for ov in overlays_sorted:
        at    = ov["at_s"]
        dur   = ov["duration_s"]
        mode  = ov.get("insert_mode", "insert")
        mp4   = ov.get("_rendered_path", "")

        if at > cursor:
            segments.append(("source", cursor, at))  # source 段
        if mp4 and os.path.exists(mp4):
            segments.append((mode, at, at + dur, mp4))  # overlay 段
        elif mode == "broll" and ov.get("vars", {}).get("video"):
            broll_path = ov["vars"]["video"]
            if os.path.exists(broll_path):
                segments.append(("cutaway", at, at + dur, broll_path))
            else:
                log(f"  WARNING: B-roll 文件不存在: {broll_path}，用 source 替代")
                segments.append(("source", at, at + dur))
        else:
            log(f"  WARNING: overlay [{ov['id']}] 无有效 MP4，用 source 替代")
            segments.append(("source", at, at + dur))

        cursor = at + dur

    if cursor < src_dur:
        segments.append(("source", cursor, src_dur))

    log(f"[finecut] 生成 {len(segments)} 个拼接段")

    # 生成中间片段文件
    seg_files = []
    for i, seg in enumerate(segments):
        seg_out = os.path.join(outdir, f"seg_{i:03d}.mp4")
        stype = seg[0]

        if stype == "source":
            _, ss, et = seg
            ok = run(f"seg {i:03d} source {ss:.2f}-{et:.2f}s", [
                "ffmpeg", "-y",
                "-ss", str(ss), "-to", str(et),
                "-i", source_video,
                "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
                "-c:a", "aac", "-b:a", "192k",
                seg_out,
            ])

        elif stype == "insert":
            _, ss, et, overlay_mp4 = seg
            # 视频来自 overlay，音频来自 source
            ok = run(f"seg {i:03d} insert [{os.path.basename(overlay_mp4)}]", [
                "ffmpeg", "-y",
                "-ss", str(ss), "-to", str(et), "-i", source_video,   # 音频源
                "-i", overlay_mp4,                                      # 视频源
                "-map", "1:v:0", "-map", "0:a:0",
                "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
                "-c:a", "aac", "-b:a", "192k",
                seg_out,
            ])

        elif stype == "cutaway":
            _, ss, et, broll_mp4 = seg
            broll_dur = et - ss
            ok = run(f"seg {i:03d} cutaway [{os.path.basename(broll_mp4)}]", [
                "ffmpeg", "-y",
                "-ss", "0", "-t", str(broll_dur),
                "-i", broll_mp4,
                "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
                "-c:a", "aac", "-b:a", "192k",
                seg_out,
            ])
        else:
            continue

        if ok and os.path.exists(seg_out):
            seg_files.append(seg_out)
        else:
            log(f"  WARNING: seg {i:03d} 生成失败，跳过")

    if not seg_files:
        log("ERROR: 所有片段生成失败")
        return False

    # 生成 concat list
    concat_list = os.path.join(outdir, "concat_list.txt")
    with open(concat_list, "w") as f:
        for sf in seg_files:
            f.write(f"file '{sf}'\n")

    ok = run("ffmpeg concat 合并", [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", concat_list,
        "-c", "copy",
        "-movflags", "+faststart",
        out_path,
    ])

    if ok:
        final_dur = get_duration(out_path)
        log(f"[finecut] 成片: {out_path} ({final_dur:.2f}s)")
    return ok


# ═══════════════════════════════════════════════════════════════
#  子命令
# ═══════════════════════════════════════════════════════════════

def cmd_run(args):
    with open(args.spec, encoding="utf-8") as f:
        spec = json.load(f)

    source_video = spec.get("source_video", "")
    if not os.path.exists(source_video):
        log(f"ERROR: source_video 不存在: {source_video}")
        sys.exit(1)

    overlays = spec.get("overlays", [])
    overlays_sorted = sorted(overlays, key=lambda x: x["at_s"])

    outdir = args.outdir
    os.makedirs(outdir, exist_ok=True)

    hf_dir = args.hf_dir or HF_DIR_DEFAULT

    # 渲染 HyperFrames 段落
    for ov in overlays_sorted:
        otype = ov.get("type", "")
        if otype == "broll":
            continue  # B-roll 不需要 HyperFrames 渲染

        log(f"\n── overlay [{ov['id']}] type={otype} at={ov['at_s']}s ──")
        mp4 = render_hf_segment(ov, hf_dir, outdir)
        ov["_rendered_path"] = mp4 or ""

    # 构建精剪成片
    out_path = os.path.join(outdir, "finecut.mp4")
    ok = build_finecut(source_video, overlays_sorted, outdir, out_path)
    if not ok:
        sys.exit(1)

    log(f"\n=== 精剪完成 → {out_path} ===")


def cmd_generate_html(args):
    """只生成 HTML 文件（不渲染），用于手工预览或调试"""
    with open(args.spec, encoding="utf-8") as f:
        spec = json.load(f)

    outdir = args.outdir
    os.makedirs(outdir, exist_ok=True)
    hf_dir = args.hf_dir or HF_DIR_DEFAULT

    for ov in spec.get("overlays", []):
        otype = ov.get("type", "")
        template_rel = TEMPLATE_MAP.get(otype)
        if not template_rel:
            continue

        template_src = os.path.join(hf_dir, template_rel)
        if not os.path.exists(template_src):
            log(f"模板不存在: {template_src}")
            continue

        with open(template_src, encoding="utf-8") as f:
            html = f.read()

        # 把 vars 注入到 data-composition-variables
        vars_dict = dict(ov.get("vars", {}))
        vars_dict.setdefault("duration", ov.get("duration_s", 5))
        vars_json = json.dumps(vars_dict, ensure_ascii=False)
        html = html.replace(
            "data-composition-variables='",
            f"data-composition-variables='{vars_json}",
            1,
        )

        out_html = os.path.join(outdir, f"{ov['id']}.html")
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(html)
        log(f"HTML → {out_html}")

    # 生成启动 preview 的 .command
    preview_cmd_path = os.path.join(outdir, "preview.command")
    lines = ["#!/bin/bash", f"cd \"{os.path.abspath(outdir)}\"", ""]
    for ov in spec.get("overlays", []):
        otype = ov.get("type", "")
        if not TEMPLATE_MAP.get(otype):
            continue
        lines.append(f"# {ov['id']} at={ov['at_s']}s")
        lines.append(f"open {ov['id']}.html")
    lines.append("read -p '按回车关闭...'")
    with open(preview_cmd_path, "w") as f:
        f.write("\n".join(lines))
    os.chmod(preview_cmd_path, 0o755)
    log(f"预览脚本 → {preview_cmd_path}（双击运行）")

    log("\n生成完毕。在 Mac 上渲染命令：")
    log(f"  cd {os.path.abspath(outdir)}")
    for ov in spec.get("overlays", []):
        if not TEMPLATE_MAP.get(ov.get("type", "")):
            continue
        vars_dict = dict(ov.get("vars", {}))
        vars_dict.setdefault("duration", ov.get("duration_s", 5))
        print(f"  npx --prefix {hf_dir} hyperframes render {ov['id']}.html "
              f"--output {ov['id']}.mp4 "
              f"--variables '{json.dumps(vars_dict, ensure_ascii=False)}'")


def cmd_schema(args):
    """打印 finecut_spec.json 的格式说明"""
    print(textwrap.dedent("""\
    finecut_spec.json — 精剪规格（Agent 分析 EDL + 定稿后输出）

    {
      "source_video": "output/roughcut_hd.mp4",
      "overlays": [

        // ── 柱状图 ─────────────────────────────────────────────
        {
          "id":          "chart_srn901_lifespan",    // 唯一 ID（用于输出文件名）
          "type":        "chart_bar",                // chart_bar | chart_stat | text_highlight | broll
          "insert_mode": "insert",                   // insert = 替换视频/保留音频; cutaway = 全切
          "at_s":        28.0,                       // 在 roughcut 中插入的时间点
          "duration_s":  5.0,                        // 插入时长（秒）
          "vars": {
            "title": "SRN901延长中位寿命",
            "unit": "%",
            "bars": [
              {"label": "对照组",  "value": 100, "color": "#888888"},
              {"label": "SRN901", "value": 133, "color": "#4a9eff"}
            ]
          }
        },

        // ── 大数字强调 ─────────────────────────────────────────
        {
          "id":          "stat_70pct_debility",
          "type":        "chart_stat",
          "insert_mode": "insert",
          "at_s":        52.0,
          "duration_s":  4.0,
          "vars": {
            "number":   "70%",
            "label":    "衰弱进展缓解",
            "sublabel": "SRN901 三期临床",
            "color":    "#52e5a0"
          }
        },

        // ── 关键词强调 ─────────────────────────────────────────
        {
          "id":          "text_telomerase",
          "type":        "text_highlight",
          "insert_mode": "insert",
          "at_s":        88.0,
          "duration_s":  3.0,
          "vars": {
            "lines":   ["端粒酶", "激活"],
            "accent":  "#4a9eff",
            "caption": "端粒和端粒酶是影响哺乳动物寿命的关键元素"
          }
        },

        // ── B-roll 切换 ────────────────────────────────────────
        {
          "id":          "broll_lab_scene",
          "type":        "broll",
          "insert_mode": "cutaway",
          "at_s":        130.0,
          "duration_s":  4.0,
          "vars": {
            "video":       "reference/broll_lab.mp4",  // B-roll 素材路径
            "lower_third": "实验室场景"                  // 可选下方说明（暂未实现字幕层）
          }
        }
      ]
    }

    type 说明：
      chart_bar      → compositions/chart-bar.html     柱状对比图（2-6根柱子，GSAP入场）
      chart_stat     → compositions/chart-stat.html    大数字强调卡（弹入动画）
      text_highlight → compositions/text-highlight.html 关键词/句飞入
      broll          → 直接 ffmpeg 切换，无需 HyperFrames

    insert_mode 说明：
      insert  → 动画/图表：视频换成动画，A-roll 音频（配音）继续播放
      cutaway → B-roll：视频+音频全切换到 B-roll 素材

    提示：
      at_s 参考 edl_snapped.json 中各 segment 的 start_s/end_s，
      选择适合插入动画的停顿或语义完整点。
    """))


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(
        description="finecut.py — HyperFrames 动画/图表 + B-roll 精剪管线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # run
    pr = sub.add_parser("run", help="全流程：渲染 HyperFrames + 拼接成片")
    pr.add_argument("--spec",   required=True, help="finecut_spec.json 路径")
    pr.add_argument("--outdir", default="output/finecut/")
    pr.add_argument("--hf-dir", default="", help="hyperframes-test 目录（默认自动查找）")
    pr.set_defaults(func=cmd_run)

    # generate-html
    pg = sub.add_parser("generate-html", help="只生成 HTML 预览文件（不渲染）")
    pg.add_argument("--spec",   required=True)
    pg.add_argument("--outdir", default="output/finecut_html/")
    pg.add_argument("--hf-dir", default="")
    pg.set_defaults(func=cmd_generate_html)

    # schema
    ps = sub.add_parser("schema", help="打印 finecut_spec.json 格式说明")
    ps.set_defaults(func=cmd_schema)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

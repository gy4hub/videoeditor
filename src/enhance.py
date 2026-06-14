#!/usr/bin/env python3
"""
enhance.py — FR-8 高清化滤镜（三档）

实现：
  基础档 (basic)    : hqdn3d 降噪 + unsharp 锐化
  增强档 (enhanced) : 基础档 + eq 对比度/饱和度/亮度微调
  添爸档 (tianbaba) : colorlevels 去灰 + 饱和度 + 锐化（对标剪映高清增强/去灰/去雾效果）

用法：
  # 基础档（默认）
  python3 src/enhance.py apply \
      --input output/t1_roughcut_v6.mp4 \
      --out output/t1_roughcut_v6_basic.mp4

  # 添爸档（推荐，对标 SRN901 参考成片）
  python3 src/enhance.py apply \
      --input output/t1_roughcut_v6.mp4 \
      --out output/t1_roughcut_v6_hd.mp4 \
      --grade tianbaba

  # 批量处理目录中的样段（raw → basic + enhanced + tianbaba）
  python3 src/enhance.py batch \
      --batch output/filter_samples/ \
      --pattern "*_raw.mp4"

  # 列出所有档位
  python3 src/enhance.py presets

沙箱注：Real-ESRGAN 超分档在 CPU 沙箱内不可用（推理极慢）。
        本机接入方式：pip install realesrgan，调用 RealESRGANer API，
        或直接用官方 CLI：realesrgan-ncnn-vulkan -i input.mp4 -o output.mp4 -n realesrgan-x4plus

滤镜参数说明：
  hqdn3d=luma_spatial:chroma_spatial:luma_tmp:chroma_tmp
    默认 3:3:6:6 — 中等时域/空域降噪，保留人像细节，适合 1080p 手持直播拍摄
  unsharp=luma_msize_x:luma_msize_y:luma_amount:chroma_msize_x:chroma_msize_y:chroma_amount
    默认 5:5:0.8:5:5:0.0 — 亮度通道 0.8 锐化量（较保守，避免过锐），色度不锐化
  eq=contrast:saturation:brightness
    增强档 contrast=1.08:saturation=1.15:brightness=0.02 — 轻微提高对比度和色彩鲜艳度
  colorlevels=rimin:gimin:bimin:romax:gomax:bomax
    添爸档 — 抬升黑场（去灰去雾核心），压缩白场，配合饱和度+35%还原通透感
"""

import argparse
import glob
import os
import subprocess
import sys
import time
from typing import Optional


# ─── 默认滤镜参数 ────────────────────────────────────────────────────────────

FILTER_PRESETS = {
    "basic": {
        "description": "hqdn3d 降噪 + unsharp 锐化",
        "vf": "hqdn3d=3:3:6:6,unsharp=5:5:0.8:5:5:0.0",
        "hqdn3d": {"luma_spatial": 3, "chroma_spatial": 3, "luma_tmp": 6, "chroma_tmp": 6},
        "unsharp": {"luma_msize_x": 5, "luma_msize_y": 5, "luma_amount": 0.8,
                    "chroma_msize_x": 5, "chroma_msize_y": 5, "chroma_amount": 0.0},
        "note": "适合作为默认档；人像清晰度提升明显，无色彩偏移"
    },
    "enhanced": {
        "description": "hqdn3d + unsharp + eq 对比度/饱和度微调",
        "vf": "hqdn3d=3:3:6:6,unsharp=5:5:0.8:5:5:0.0,eq=contrast=1.08:saturation=1.15:brightness=0.02",
        "hqdn3d": {"luma_spatial": 3, "chroma_spatial": 3, "luma_tmp": 6, "chroma_tmp": 6},
        "unsharp": {"luma_msize_x": 5, "luma_msize_y": 5, "luma_amount": 0.8,
                    "chroma_msize_x": 5, "chroma_msize_y": 5, "chroma_amount": 0.0},
        "eq": {"contrast": 1.08, "saturation": 1.15, "brightness": 0.02},
        "note": "适合欠曝或色调偏灰的素材；对比度/饱和度微调 +8%/+15%，亮度 +2%"
    },
    "tianbaba": {
        "description": "colorlevels 去灰 + eq 饱和度 + unsharp 锐化（对标剪映高清增强/去灰/去雾）",
        # 核心思路：
        #   1. hqdn3d=2:2:4:4   轻降噪（比 basic 轻，保留更多皮肤/背景纹理）
        #   2. colorlevels       抬升黑场（rimin/gimin/bimin=0.04/0.04/0.02）
        #                        轻压白场（romax/gomax/bomax=0.97/0.97/0.98）
        #                        → 去掉灰雾感，等效剪映"去灰/去雾"效果
        #   3. eq=saturation     +35% 饱和度，还原户外绿植/蓝天通透感
        #      brightness        +1% 补偿黑场抬升导致的轻微变暗
        #   4. unsharp=1.2       略强于 basic 的锐化，等效"高清增强"细节提升
        # 对标素材：SRN901抗衰突破.mp4（剪映处理后成片）
        "vf": (
            "hqdn3d=2:2:4:4,"
            "colorlevels=rimin=0.04:gimin=0.04:bimin=0.02"
            ":romax=0.97:gomax=0.97:bomax=0.98,"
            "eq=saturation=1.35:brightness=0.01,"
            "unsharp=5:5:1.2:5:5:0.0"
        ),
        "colorlevels": {
            "rimin": 0.04, "gimin": 0.04, "bimin": 0.02,
            "romax": 0.97, "gomax": 0.97, "bomax": 0.98,
        },
        "eq": {"saturation": 1.35, "brightness": 0.01},
        "unsharp": {"luma_msize_x": 5, "luma_msize_y": 5, "luma_amount": 1.2,
                    "chroma_msize_x": 5, "chroma_msize_y": 5, "chroma_amount": 0.0},
        "note": (
            "推荐用于添爸室外自然光场景（公园/户外口播）。"
            "黑场抬升 4% 去除灰雾，饱和度 +35% 还原绿植/天空通透感，"
            "锐化 1.2x 提升人像细节。A/B 对比：对标 SRN901抗衰突破.mp4。"
        ),
    },
    # Real-ESRGAN 超分档（本机可选，沙箱不可用）
    # "realesrgan": {
    #     "description": "Real-ESRGAN x4 超分（4倍超分辨率，慢，仅本机 GPU）",
    #     "note": "需安装: pip install realesrgan basicsr; 或 realesrgan-ncnn-vulkan CLI",
    #     "cli_example": "realesrgan-ncnn-vulkan -i input.mp4 -o output.mp4 -n realesrgan-x4plus",
    # }
}

# 默认档位（roughcut.py --enhance 使用）
DEFAULT_GRADE = "tianbaba"


# ─── 核心渲染函数 ────────────────────────────────────────────────────────────

def apply_filter(
    input_path: str,
    output_path: str,
    grade: str = "basic",
    crf: int = 20,
    preset: str = "veryfast",
    custom_vf: Optional[str] = None,
) -> dict:
    """
    对输入视频应用高清化滤镜并输出。

    Parameters
    ----------
    input_path  : 输入视频路径
    output_path : 输出视频路径
    grade       : 'basic' | 'enhanced'（预设档）
    crf         : H.264 CRF（默认 20，与粗剪 v3 一致）
    preset      : H.264 preset（默认 veryfast）
    custom_vf   : 自定义 ffmpeg -vf 字符串（覆盖 grade 预设）

    Returns
    -------
    dict: {'input', 'output', 'grade', 'vf', 'elapsed_s', 'input_size_mb', 'output_size_mb'}
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"输入文件不存在: {input_path}")

    if grade not in FILTER_PRESETS and custom_vf is None:
        raise ValueError(f"未知档位: {grade}，可选: {list(FILTER_PRESETS.keys())}")

    vf = custom_vf if custom_vf else FILTER_PRESETS[grade]["vf"]
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-c:a", "copy",
        output_path,
    ]

    print(f"[enhance] 滤镜渲染 [{grade}] → {output_path}", file=sys.stderr)
    print(f"[enhance]   vf: {vf}", file=sys.stderr)
    t0 = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = round(time.time() - t0, 2)

    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg 滤镜失败 (exit {result.returncode}):\n{result.stderr[-1500:]}"
        )

    input_mb = round(os.path.getsize(input_path) / 1024 / 1024, 2)
    output_mb = round(os.path.getsize(output_path) / 1024 / 1024, 2)
    print(f"[enhance] 完成: {elapsed}s, {input_mb}MB → {output_mb}MB", file=sys.stderr)

    return {
        "input": input_path,
        "output": output_path,
        "grade": grade,
        "vf": vf,
        "elapsed_s": elapsed,
        "input_size_mb": input_mb,
        "output_size_mb": output_mb,
    }


def probe_duration(path: str) -> float:
    """用 ffprobe 获取时长。"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=10,
        )
        return float(r.stdout.strip())
    except Exception:
        return -1.0


# ─── CLI ────────────────────────────────────────────────────────────────────

def cmd_single(args):
    result = apply_filter(
        input_path=args.input,
        output_path=args.out,
        grade=args.grade,
        crf=args.crf,
        preset=args.preset,
        custom_vf=args.vf,
    )
    print(f"[enhance] 结果: {result}")


def cmd_batch(args):
    """批量处理：对目录内匹配文件分别渲染 basic / enhanced / tianbaba 三档。"""
    pattern = os.path.join(args.batch, args.pattern)
    files = sorted(glob.glob(pattern))
    if not files:
        print(f"[enhance] 未找到匹配文件: {pattern}", file=sys.stderr)
        return

    grades = args.grades if args.grades else ["basic", "enhanced", "tianbaba"]
    print(f"[enhance] 批量处理 {len(files)} 个文件 → {grades}", file=sys.stderr)
    for f in files:
        base, ext = os.path.splitext(f)
        # 替换 _raw 为 _<grade>（若无 _raw 则加后缀）
        if base.endswith("_raw"):
            stem = base[:-4]
        else:
            stem = base
        for grade in grades:
            out = f"{stem}_{grade}{ext}"
            if os.path.isfile(out):
                print(f"[enhance]   跳过已存在: {out}", file=sys.stderr)
                continue
            apply_filter(f, out, grade=grade, crf=args.crf, preset=args.preset)


def cmd_list_presets(args):
    """列出所有预设档及参数说明。"""
    print(f"可用滤镜档位（默认: {DEFAULT_GRADE}）:")
    for name, preset in FILTER_PRESETS.items():
        marker = " ★" if name == DEFAULT_GRADE else ""
        print(f"\n  [{name}]{marker}")
        print(f"    描述: {preset['description']}")
        print(f"    vf:   {preset['vf']}")
        print(f"    说明: {preset['note']}")
    print("\n  [realesrgan]（本机可选，沙箱不可用）")
    print("    描述: Real-ESRGAN x4 超分辨率（4× 放大，需 GPU）")
    print("    安装: pip install realesrgan basicsr")
    print("    CLI:  realesrgan-ncnn-vulkan -i input.mp4 -o output.mp4 -n realesrgan-x4plus")


def main():
    parser = argparse.ArgumentParser(
        description="enhance.py — FR-8 高清化滤镜（basic / enhanced 两档）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # apply 子命令（单文件）
    p_apply = sub.add_parser("apply", help="对单个视频应用滤镜")
    p_apply.add_argument("--input", "-i", required=True, help="输入视频路径")
    p_apply.add_argument("--out", "-o", required=True, help="输出视频路径")
    p_apply.add_argument("--grade", "-g", default=DEFAULT_GRADE,
                         choices=list(FILTER_PRESETS.keys()),
                         help=f"滤镜档位（默认 {DEFAULT_GRADE}）")
    p_apply.add_argument("--vf", help="自定义 ffmpeg -vf 字符串（覆盖 --grade）")
    p_apply.add_argument("--crf", type=int, default=20, help="H.264 CRF（默认 20）")
    p_apply.add_argument("--preset", default="veryfast", help="H.264 preset（默认 veryfast）")

    # batch 子命令
    p_batch = sub.add_parser("batch", help="批量处理目录内文件")
    p_batch.add_argument("--batch", "-b", required=True, help="目录路径")
    p_batch.add_argument("--pattern", "-p", default="*_raw.mp4",
                         help="文件匹配模式（默认 *_raw.mp4）")
    p_batch.add_argument("--grades", nargs="+",
                         choices=list(FILTER_PRESETS.keys()),
                         help="要渲染的档位列表（默认全部三档）")
    p_batch.add_argument("--crf", type=int, default=20, help="H.264 CRF（默认 20）")
    p_batch.add_argument("--preset", default="veryfast", help="H.264 preset（默认 veryfast）")

    # presets 子命令
    sub.add_parser("presets", help="列出所有预设档位及参数说明")

    args = parser.parse_args()

    if args.command == "apply":
        cmd_single(args)
    elif args.command == "batch":
        cmd_batch(args)
    elif args.command == "presets":
        cmd_list_presets(args)


if __name__ == "__main__":
    main()

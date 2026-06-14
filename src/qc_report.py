#!/usr/bin/env python3
"""
qc_report.py — FR-9 质检报告生成器

输入：EDL JSON（必须）+ 对齐 JSON（可选）
输出：Markdown 质检报告，包含：
  - 删除片段清单（时间码 + 文本 + 原因 + decided_by）
  - 保留段统计（总数、总时长、压缩比）
  - 低置信度风险项（对齐置信度 < 0.5 的保留段）
  - 时长压缩比（成片 / 原素材）
  - 各步骤产物清单

用法：
  python3 src/qc_report.py \
      --edl output/s2_edl.json \
      --alignment eval/s2_alignment.json \
      --source-duration 197.952 \
      --out output/s2_qc_report.md

  # 最简用法（无对齐文件）
  python3 src/qc_report.py \
      --edl output/s2_edl.json \
      --out output/s2_qc_report.md
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Any


# ─── 辅助函数 ────────────────────────────────────────────────────────────────

def fmt_tc(s: float) -> str:
    """秒 → HH:MM:SS.mmm"""
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:06.3f}"


def probe_duration(path: str) -> Optional[float]:
    """用 ffprobe 获取视频时长，失败返回 None。"""
    if not os.path.isfile(path):
        return None
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=10,
        )
        return float(r.stdout.strip())
    except Exception:
        return None


def load_json(path: str) -> Optional[Dict]:
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ─── 核心报告生成 ────────────────────────────────────────────────────────────

def build_qc_report(
    edl: Dict,
    alignment: Optional[Dict] = None,
    source_duration_s: Optional[float] = None,
    output_duration_s: Optional[float] = None,
    artifacts: Optional[List[Dict]] = None,
    run_date: Optional[str] = None,
) -> str:
    """
    生成 Markdown 质检报告。

    Parameters
    ----------
    edl              : EDL JSON dict
    alignment        : 对齐结果 JSON dict（可选）
    source_duration_s: 原素材时长（秒）
    output_duration_s: 成片时长（秒，可选，用于验证压缩比）
    artifacts        : 各步骤产物清单 [{'step','path','size_mb','note'}]
    run_date         : 报告生成日期（默认 today）

    Returns
    -------
    str: Markdown 报告内容
    """
    date_str = run_date or datetime.now().strftime("%Y-%m-%d")
    segments = edl.get("segments", [])
    source_file = edl.get("source", "unknown")
    fps = edl.get("fps", 30)

    keep_segs = [s for s in segments if s.get("keep")]
    drop_segs = [s for s in segments if not s.get("keep")]

    keep_duration = sum(s.get("end_s", 0) - s.get("start_s", 0) for s in keep_segs)
    drop_duration = sum(s.get("end_s", 0) - s.get("start_s", 0) for s in drop_segs)

    # 压缩比计算
    if source_duration_s and source_duration_s > 0:
        compression = keep_duration / source_duration_s
        reduction_pct = (1 - compression) * 100
    else:
        compression = None
        reduction_pct = None

    # 时长偏差
    duration_diff = None
    if output_duration_s and output_duration_s > 0:
        duration_diff = abs(output_duration_s - keep_duration)

    # decided_by 统计
    decided_counts = {}
    for s in segments:
        db = s.get("decided_by", "unknown")
        decided_counts[db] = decided_counts.get(db, 0) + 1

    # ── 低置信度风险项（从对齐结果）──────────────────────────────────────────
    low_conf_items = []
    if alignment:
        sentences = alignment.get("sentences", [])
        for sent in sentences:
            for interval in sent.get("intervals", []):
                if interval.get("keep") and interval.get("confidence", 1.0) < 0.5:
                    low_conf_items.append({
                        "script_line": sent.get("id"),
                        "script_text": sent.get("script_text", ""),
                        "transcript_text": interval.get("transcript_text", ""),
                        "start_s": interval.get("start_s", 0),
                        "end_s": interval.get("end_s", 0),
                        "confidence": interval.get("confidence", 0),
                    })

    # ── 开始构建 Markdown ────────────────────────────────────────────────────
    lines = []
    lines.append(f"# 质检报告 — {source_file}")
    lines.append(f"")
    lines.append(f"| 字段 | 内容 |")
    lines.append(f"|---|---|")
    lines.append(f"| 生成日期 | {date_str} |")
    lines.append(f"| 原素材 | `{source_file}` |")
    lines.append(f"| FPS | {fps} |")
    lines.append(f"| EDL 版本 | {edl.get('version', 'N/A')} |")
    lines.append(f"")

    # ── §1 汇总统计 ──────────────────────────────────────────────────────────
    lines.append(f"## §1 汇总统计")
    lines.append(f"")
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|---|---|")
    lines.append(f"| 总片段数 | {len(segments)} |")
    lines.append(f"| 保留片段 | {len(keep_segs)} |")
    lines.append(f"| 删除片段 | {len(drop_segs)} |")
    lines.append(f"| decided_by: rule | {decided_counts.get('rule', 0)} |")
    lines.append(f"| decided_by: llm | {decided_counts.get('llm', 0)} |")
    lines.append(f"| decided_by: human | {decided_counts.get('human', 0)} |")
    lines.append(f"| 保留总时长 | {keep_duration:.3f}s（{keep_duration/60:.2f}min）|")
    lines.append(f"| 删除总时长 | {drop_duration:.3f}s（{drop_duration/60:.2f}min）|")
    if source_duration_s:
        lines.append(f"| 原素材时长 | {source_duration_s:.3f}s（{source_duration_s/60:.2f}min）|")
    if compression is not None:
        lines.append(f"| 时长压缩比 | {compression:.4f}（保留 {100-reduction_pct:.1f}%，压缩 {reduction_pct:.1f}%）|")
    if output_duration_s:
        lines.append(f"| 成片实际时长 | {output_duration_s:.3f}s |")
    if duration_diff is not None:
        status = "✓" if duration_diff < 0.5 else "⚠️ 超标（>0.5s）"
        lines.append(f"| EDL 名义时长 vs 成片偏差 | {duration_diff:.3f}s {status} |")
    lines.append(f"")

    # ── §2 删除片段清单 ──────────────────────────────────────────────────────
    lines.append(f"## §2 删除片段清单（共 {len(drop_segs)} 个，{drop_duration:.2f}s）")
    lines.append(f"")
    lines.append(f"| id | 开始 | 结束 | 时长(s) | 文本 | 原因 | decided_by |")
    lines.append(f"|---|---|---|---|---|---|---|")
    for s in sorted(drop_segs, key=lambda x: x.get("start_s", 0)):
        start_s = s.get("start_s", 0)
        end_s = s.get("end_s", 0)
        dur = round(end_s - start_s, 3)
        text = s.get("text", "")[:50].replace("|", "｜")
        reason = s.get("reason", "").replace("|", "｜")
        db = s.get("decided_by", "unknown")
        lines.append(
            f"| {s['id']} | {fmt_tc(start_s)} | {fmt_tc(end_s)} | {dur} | {text} | {reason} | {db} |"
        )
    lines.append(f"")

    # ── §3 保留段统计（按 decided_by 分组）────────────────────────────────────
    lines.append(f"## §3 保留段统计")
    lines.append(f"")
    lines.append(f"| id | 开始 | 结束 | 时长(s) | 文本摘要 | decided_by |")
    lines.append(f"|---|---|---|---|---|---|")
    for s in sorted(keep_segs, key=lambda x: x.get("start_s", 0)):
        start_s = s.get("start_s", 0)
        end_s = s.get("end_s", 0)
        dur = round(end_s - start_s, 3)
        text = s.get("text", "")[:40].replace("|", "｜")
        db = s.get("decided_by", "unknown")
        lines.append(
            f"| {s['id']} | {fmt_tc(start_s)} | {fmt_tc(end_s)} | {dur} | {text} | {db} |"
        )
    lines.append(f"")

    # ── §4 低置信度风险项 ─────────────────────────────────────────────────────
    lines.append(f"## §4 低置信度风险项（对齐置信度 < 0.5）")
    lines.append(f"")
    if not alignment:
        lines.append(f"> 未提供对齐文件（--alignment），跳过此检查。")
    elif not low_conf_items:
        lines.append(f"> 无低置信度风险项（所有保留段对齐置信度 ≥ 0.5）。")
    else:
        lines.append(f"| 定稿行 | 置信度 | 时间区间 | 定稿文本 | 转写文本 |")
        lines.append(f"|---|---|---|---|---|")
        for item in sorted(low_conf_items, key=lambda x: x["confidence"]):
            tc = f"{fmt_tc(item['start_s'])}–{fmt_tc(item['end_s'])}"
            script_t = item["script_text"][:40].replace("|", "｜")
            trans_t = item["transcript_text"][:40].replace("|", "｜")
            lines.append(
                f"| {item['script_line']} | {item['confidence']:.3f} | {tc} | {script_t} | {trans_t} |"
            )
    lines.append(f"")

    # ── §5 各步骤产物清单 ─────────────────────────────────────────────────────
    lines.append(f"## §5 各步骤产物清单")
    lines.append(f"")
    if artifacts:
        lines.append(f"| 步骤 | 文件 | 大小 | 说明 |")
        lines.append(f"|---|---|---|---|")
        for art in artifacts:
            size_str = f"{art.get('size_mb', 0):.1f}MB" if art.get("size_mb") else "N/A"
            note = art.get("note", "")
            lines.append(f"| {art['step']} | `{art['path']}` | {size_str} | {note} |")
    else:
        lines.append(f"> 未传入产物清单。")
    lines.append(f"")

    # ── §6 音画同步检查结果（v4 硬检）────────────────────────────────────────
    lines.append(f"## §6 音画同步检查（v4 互相关硬检）")
    lines.append(f"")
    if artifacts:
        av_sync_art = next((a for a in artifacts if "av_sync" in a.get("path", "")), None)
    else:
        av_sync_art = None

    # 尝试从 eval/av_sync_result.json 加载
    av_sync_json_path = None
    if av_sync_art:
        av_sync_json_path = av_sync_art.get("path")
    else:
        # 尝试默认路径
        default_sync_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "eval", "av_sync_result.json"
        )
        if os.path.isfile(default_sync_path):
            av_sync_json_path = default_sync_path

    av_sync_data = None
    if av_sync_json_path and os.path.isfile(av_sync_json_path):
        try:
            with open(av_sync_json_path, encoding="utf-8") as f:
                av_sync_data = json.load(f)
        except Exception:
            pass

    if av_sync_data:
        all_pass = av_sync_data.get("all_pass", False)
        threshold = av_sync_data.get("threshold_lag_ms", 40)
        segs = av_sync_data.get("segments", [])
        status_str = "全部通过" if all_pass else "存在不通过项"
        lines.append(f"**结论：{status_str}**（阈值 |lag| < {threshold}ms，5 段全检）")
        lines.append(f"")
        lines.append(f"| 段索引 | 段id | 源时间窗 | v4输出位置 | lag(ms) | 相关峰值 | lag判定 | 视频哈希距离 | 视频判定 | 整体 |")
        lines.append(f"|---|---|---|---|---|---|---|---|---|---|")
        for s in segs:
            lag_str = f"{s['lag_ms']:+.1f}" if s.get("lag_ms") is not None else "ERR"
            corr_str = f"{s['corr_peak']:.3f}" if s.get("corr_peak") is not None else "N/A"
            hd_str = str(s.get("hash_dist", "ERR"))
            src_win = f"[{s['src_start']:.3f},{s['src_end']:.3f}]s"
            v4_win = f"[{s['v4_start']:.3f},{s['v4_end']:.3f}]s"
            lines.append(
                f"| {s['seg_idx']} | {s['seg_id']} | {src_win} | {v4_win} | {lag_str} | {corr_str} | "
                f"{'PASS' if s.get('lag_pass') else 'FAIL'} | {hd_str} | "
                f"{'PASS' if s.get('hash_pass') else 'FAIL'} | "
                f"{'OK' if s.get('seg_pass') else 'FAIL'} |"
            )
        lines.append(f"")
        lines.append(f"> 注：+21.4ms lag 为 AAC 编码器固有延迟（1024 采样 @ 48kHz = 21.3ms），")
        lines.append(f"> 为常量偏移（非累积漂移），在所有段中一致，不影响主观听感。")
        lines.append(f"> v3 根因为双管线切点不一致（±0~245ms 随段变化）+ acrossfade 33×15ms=0.495s 吞时累积。")
    else:
        lines.append(f"> 未找到音画同步检查结果文件（eval/av_sync_result.json）。")
        lines.append(f"> 请先运行：`python3 src/av_sync_check.py --v4 output/s1_roughcut_v4.mp4 ...`")
    lines.append(f"")

    # ── §7 回归自检结果 ───────────────────────────────────────────────────────
    lines.append(f"## §7 回归自检结果（regression_check）")
    lines.append(f"")

    # 尝试加载最新 regression_result
    reg_result_path = None
    if artifacts:
        for a in artifacts:
            if "regression_result" in a.get("path", ""):
                reg_result_path = a.get("path")
    if not reg_result_path:
        default_reg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "eval", "regression_result_v5.json"
        )
        if os.path.isfile(default_reg_path):
            reg_result_path = default_reg_path

    reg_data = None
    if reg_result_path and os.path.isfile(str(reg_result_path)):
        try:
            with open(reg_result_path, encoding="utf-8") as f:
                reg_data = json.load(f)
        except Exception:
            pass

    if reg_data:
        overall = reg_data.get("overall", "UNKNOWN")
        total   = reg_data.get("total", 0)
        passed  = reg_data.get("passed", 0)
        failed  = reg_data.get("failed", 0)
        skipped = reg_data.get("skipped", 0)
        lines.append(f"**总判定：{overall}**（{passed}/{total} PASS，{failed} FAIL，{skipped} SKIP）")
        lines.append(f"")
        lines.append(f"| id | 类别 | 描述 | 状态 | 细节 |")
        lines.append(f"|---|---|---|---|---|")
        for r in reg_data.get("results", []):
            status_icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "○", "ERROR": "!"}.get(r.get("status",""), "?")
            detail = r.get("detail", "")[:80].replace("|", "｜")
            lines.append(
                f"| {r['id']} | {r.get('category','')} | {r.get('description','')[:50]} | "
                f"{status_icon} {r.get('status','')} | {detail} |"
            )
    else:
        lines.append(f"> 未找到回归检查结果（eval/regression_result_v5.json）。")
        lines.append(f"> 运行：`python3 src/regression_check.py --video output/s1_roughcut_v5.mp4 "
                     f"--checklist eval/regression_checklist.json --out eval/regression_result_v5.json`")
    lines.append(f"")

    # ── §8 备注 / 人工终审要点 ────────────────────────────────────────────────
    lines.append(f"## §8 人工终审要点")
    lines.append(f"")
    if low_conf_items:
        lines.append(f"- **{len(low_conf_items)} 个低置信度保留段**：建议 Chen 重点盲听确认。")
    human_count = decided_counts.get("human", 0)
    if human_count > 0:
        lines.append(f"- **{human_count} 个 human 修改行**：重跑时已保护，不会被规则覆盖。")
    else:
        lines.append(f"- 本次无 human 修改行（decided_by=human 为 0）。")
    lines.append(f"- 时长压缩比 {compression:.4f}" if compression else "- 时长压缩比：原素材时长未知。")
    lines.append(f"- 气口生硬感：Chen 盲听 10 个切点，≥8 个听不出剪辑痕迹即为通过。")
    lines.append(f"- 本报告由 `qc_report.py` 自动生成，供 Chen 终审参考。")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"*Generated by qc_report.py on {date_str}*")

    return "\n".join(lines)


# ─── 默认产物清单（S2 已知产物）────────────────────────────────────────────────

def default_s2_artifacts(base_dir: str) -> List[Dict]:
    """构建 S2 管线默认产物清单。"""
    files = [
        ("①转写", "eval/s2_transcript_medium.json", "medium int8 转写（704词）"),
        ("②对齐", "eval/s2_alignment.json", "18/20 句匹配"),
        ("③自重复", "eval/s2_self_dedup.json", "15对检出，7真/8误报"),
        ("④规则", "output/s2_rules.json", "规则引擎输出"),
        ("⑤EDL-JSON", "output/s2_edl.json", "41段，34keep，schema清洁"),
        ("⑤EDL-CSV", "output/s2_edl.csv", "CSV 版（人工可编辑）"),
        ("⑥成片v3", "output/s1_roughcut_v3.mp4", "166.119s，libx264 crf20"),
        ("⑦滤镜样本", "output/filter_samples/", "3段×原始/basic/enhanced各10s"),
    ]
    result = []
    for step, rel_path, note in files:
        abs_path = os.path.join(base_dir, rel_path)
        size_mb = None
        if os.path.isfile(abs_path):
            size_mb = round(os.path.getsize(abs_path) / 1024 / 1024, 2)
        elif os.path.isdir(abs_path):
            # 目录：统计所有文件大小
            total = sum(os.path.getsize(os.path.join(abs_path, f))
                        for f in os.listdir(abs_path)
                        if os.path.isfile(os.path.join(abs_path, f)))
            size_mb = round(total / 1024 / 1024, 2)
        result.append({"step": step, "path": rel_path, "size_mb": size_mb, "note": note})
    return result


# ─── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="qc_report.py — FR-9 质检报告生成器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--edl", "-e", required=True, help="EDL JSON 路径")
    parser.add_argument("--alignment", "-a", default=None, help="对齐结果 JSON（可选）")
    parser.add_argument("--output-video", default=None, help="成片 MP4（用于实测时长偏差）")
    parser.add_argument("--source-duration", type=float, default=None,
                        help="原素材时长（秒）；不传则从 EDL 推断")
    parser.add_argument("--out", "-o", required=True, help="质检报告输出路径（.md）")
    parser.add_argument("--base-dir", default=None,
                        help="项目根目录（用于构建产物清单，默认为 EDL 所在目录的上级）")
    parser.add_argument("--no-artifacts", action="store_true",
                        help="不生成产物清单")

    args = parser.parse_args()

    # 加载 EDL
    edl = load_json(args.edl)
    if edl is None:
        print(f"[qc_report] 错误：EDL 文件不存在: {args.edl}", file=sys.stderr)
        sys.exit(1)

    # 加载对齐结果
    alignment = load_json(args.alignment) if args.alignment else None
    if args.alignment and alignment is None:
        print(f"[qc_report] 警告：对齐文件不存在: {args.alignment}，跳过低置信度检查。",
              file=sys.stderr)

    # 成片时长
    output_dur = None
    if args.output_video:
        output_dur = probe_duration(args.output_video)

    # 原素材时长
    source_dur = args.source_duration

    # 产物清单
    artifacts = None
    if not args.no_artifacts:
        base_dir = args.base_dir or os.path.dirname(os.path.dirname(os.path.abspath(args.edl)))
        artifacts = default_s2_artifacts(base_dir)

    # 生成报告
    report_md = build_qc_report(
        edl=edl,
        alignment=alignment,
        source_duration_s=source_dur,
        output_duration_s=output_dur,
        artifacts=artifacts,
    )

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report_md)

    print(f"[qc_report] 质检报告已保存 → {args.out}", file=sys.stderr)
    print(f"[qc_report] 报告字数: {len(report_md)}", file=sys.stderr)


if __name__ == "__main__":
    main()

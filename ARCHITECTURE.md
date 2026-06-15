# 架构与文件指南（现行主路径 vs 废弃件）

> 目的：让接手者一眼分清「现在该用哪个」，避免在废弃文件上浪费时间。
> 配套：需求见 `docs/01_PRD_视频剪辑自动化管线.md`，迭代见 `docs/02_Scrum_Sprint规划.md`。

## 粗剪（rough-cut）— 现行主路径

入口 skill：`skills/rough-cut/SKILL.md`，调用以下 `src/` 脚本：

| 脚本 | 职责 |
|---|---|
| `src/transcribe.py` | faster-whisper 本地转写，词级时间戳 |
| `src/ng_detect.py` | LLM NG / 重复检测（temperature=0） |
| `src/pipeline.py` | 粗剪全流程统一 CLI（ASR→NG→切割→滤镜→输出+QC） |
| `src/snap_cuts.py` | RMS 能量谷值切点 + crossfade 气口平滑 |
| `src/subtitle.py` | 定稿→中英双语 SRT + 烧录 |
| `src/enhance.py` | tianbaba 高清化滤镜 preset |
| `src/qc_report.py` | 质检报告 |

## 精剪（fine-cut）— 现行主路径

入口 skill：`skills/finecut/SKILL.md`。精剪由 Agent 直接调用 HyperFrames CLI + ffmpeg 完成。
HyperFrames 模板见 `skills/hyperframes-test/compositions/`（chart-bar / chart-stat / text-highlight）。
分镜模板见 `skills/video-spec-templates/`。

⚠️ HyperFrames 渲染必须在 **Mac 本机**执行（ARM64 沙箱无 Chrome）。Agent 只负责写 spec，不渲染。

## 已废弃 / 非主路径（勿在其上扩展）

| 文件 | 状态 | 说明 |
|---|---|---|
| `src/finecut.py` | **DEPRECATED** | 精剪逻辑已移入 `skills/finecut/SKILL.md`，文件头已标注 |
| `src/roughcut.py` | **过时** | 旧 align+rules 路径；S2 Retro 决定改走 LLM 语义管线，新 story 不再扩展此路径 |
| `src/align.py` / `src/rules.py` / `src/edl.py` / `src/edl_v2.py` / `src/cut.py` | 旧路径配套 | 服务于旧 `roughcut.py`，主路径不使用；保留仅作历史参照，依赖前请先确认 |

> 本表由 AVM Goal G1（2026-06-15）建立。删除废弃本体需 Chen 单独确认。

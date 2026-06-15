#!/bin/bash
# Sprint 3 代码提交脚本
# 双击运行，或 bash sprint3_commit.command

cd "/Users/gchyang/文档/synchronize/On going job/2026OMC/Projects/videoeditor"

echo "=== Sprint 3 commit ==="
git add \
    src/ng_detect.py \
    src/pipeline.py \
    src/subtitle.py \
    skills/rough-cut/SKILL.md \
    docs/02_Scrum_Sprint规划.md

git status --short

git commit -m "feat(sprint3): pipeline.py + ng_detect v2 + subtitle.py + SKILL 更新

S3-1 ng_detect.py v2 — LLM 语义 NG 自动化
  * 新增 auto 模式：调 Claude API (temperature=0) 自动检测 NG 窗口
  * 新增 prompt 模式：输出分析 prompt 供手工粘贴到对话框
  * manual 模式：读取 ng_windows.json 重建 EDL（原有逻辑保留）
  * 去掉 hardcoded test1 NG windows，改为通用架构

S3-2 pipeline.py — 粗剪全流程统一 CLI
  * 一条命令串联：转写→NG检测→切点吸附→渲染→滤镜→字幕→QC
  * 断点续跑（产物已存在则跳过对应步骤）
  * ng-mode: auto/manual/skip 三种模式
  * ffmpeg_concat_edl() 原生 filter_complex 精确重编码

S3-4/S3-5 subtitle.py — 字幕生成与烧录管线
  * generate: 定稿→ASR词级对齐→双语SRT/ASS
  * burn: ffmpeg 烧录（金陵体→STSong→PingFang降级）
    中文54pt/英文38pt/白色/60%黑色阴影/底部居中
  * preview: 截图指定时间点字幕效果
  * 调 Claude Haiku API 批量中→英翻译（temperature=0）

S3-6 SKILL.md v2 — 粗剪 skill 打包
  * 完整反映新架构（弃用 align+rules 旧路径）
  * 字幕规范、NG 判断规范、微调回路全部更新

docs: Sprint 2 retro + Sprint 3/4/5 backlog 更新"

echo ""
echo "=== git push ==="
git push origin main

echo ""
echo "=== 完成 ==="
echo "https://github.com/gy4hub/videoeditor"
read -p "按回车关闭..."

#!/bin/bash
cd "/Users/gchyang/文档/synchronize/On going job/2026OMC/Projects/videoeditor"

echo "=== Sprint 3 重构 commit ==="
git add src/ng_detect.py src/pipeline.py src/subtitle.py skills/rough-cut/SKILL.md

git commit -m "refactor: 去掉外部 API 调用，改为 Agent 内联推理

动机：外部 API 调用重复付费，Agent 本身即 LLM，无需额外调用。

ng_detect.py:
  - 删除 auto 模式（调 Anthropic API）
  - 保留 prompt 模式（输出分析 prompt 供 Agent 在对话中阅读）
  - 保留 manual 模式（读 ng_windows.json 执行 EDL 重建）
  - Agent 工作流：prompt → Agent 标注 JSON → manual → EDL

subtitle.py:
  - 删除 translate_to_english() API 调用
  - 新增 align 子命令：生成中文字幕 JSON 供 Agent 翻译
  - generate 改为：--cn-json + --en-json 或 --bilingual 双语 JSON
  - Agent 工作流：align → Agent 翻译 → generate → burn

pipeline.py:
  - 删除 --ng-mode auto/manual/skip，改为 --ng-json（有则用，无则基础 EDL）
  - 删除 --api-key / --ng-model 参数
  - Step 6 字幕改为 --srt 参数（由 subtitle.py 工作流预先生成）

SKILL.md:
  - 工作流全部更新为 Agent 内联推理版本
  - 字幕章节拆分为 align → 翻译 → generate → burn 四步"

git push origin main
echo "=== 完成 ==="
read -p "按回车关闭..."

#!/bin/bash
cd "/Users/gchyang/文档/synchronize/On going job/2026OMC/Projects/videoeditor"

# 清理所有残留 lock 文件
find .git -name "*.lock" -delete
echo "Locks cleared"

echo "=== Sprint 4 commit ==="

git add \
  src/finecut.py \
  "skills/hyperframes-test/compositions/chart-bar.html" \
  "skills/hyperframes-test/compositions/chart-stat.html" \
  "skills/hyperframes-test/compositions/text-highlight.html" \
  "skills/finecut/SKILL.md" \
  "docs/02_Scrum_Sprint规划.md"

git commit -m "refactor(sprint4): finecut.py 废弃，精剪逻辑移入 SKILL.md

finecut.py 标为废弃（3行注释占位）。
新增 skills/finecut/SKILL.md：
  - 组件目录（chart-bar/chart-stat/text-highlight）约束 LLM 选型
  - Agent 直接调 HyperFrames CLI + ffmpeg，无 Python 中间层
  - insert/cutaway 两种拼接命令模板
  遵循 Karpathy 原则：LLM 决策，脚本执行，skill 约束发散"

git push origin main
echo "=== 完成 ==="

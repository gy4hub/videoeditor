#!/bin/bash
BASE="/Users/gchyang/文档/synchronize/On going job/2026OMC/Projects/videoeditor"
HF="$BASE/skills/hyperframes-test/node_modules/.bin/hyperframes"
PROJ="$BASE/skills/hyperframes-test"
LOG="$BASE/output/hf_test_log.txt"

cd "$BASE"
mkdir -p output/finecut

echo "Node: $(node --version)" > "$LOG"
echo "HF: $("$HF" --version 2>&1)" >> "$LOG"
echo "" >> "$LOG"
echo "=== render output ===" >> "$LOG"

"$HF" render "$PROJ" \
  --composition compositions/chart-stat.html \
  --output output/finecut/test_C.mp4 \
  --variables '{"number":"90%","label":"小鼠与人类基因相似度","sublabel":"但人类复杂度远高于小鼠","color":"#f59e0b","duration":4}' >> "$LOG" 2>&1

echo "" >> "$LOG"
ls -lh output/finecut/test_C.mp4 >> "$LOG" 2>&1

cat "$LOG"
echo "=== 日志已写入 output/hf_test_log.txt ==="

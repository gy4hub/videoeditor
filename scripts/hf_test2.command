#!/bin/bash
BASE="/Users/gchyang/文档/synchronize/On going job/2026OMC/Projects/videoeditor"
HF="$BASE/skills/hyperframes-test/node_modules/.bin/hyperframes"
PROJ="$BASE/skills/hyperframes-test"

cd "$BASE"

echo "Node: $(node --version)"
echo "=== HyperFrames 测试 (inner sharp 已删除) ==="

echo ""
echo "[1] CLI 版本..."
"$HF" --version 2>&1

echo ""
echo "[2] 渲染 chart-stat (4秒)..."
mkdir -p output/finecut

"$HF" render "$PROJ" \
  --composition compositions/chart-stat.html \
  --output output/finecut/test_C.mp4 \
  --variables '{"number":"90%","label":"小鼠与人类基因相似度","sublabel":"但人类复杂度远高于小鼠","color":"#f59e0b","duration":4}' 2>&1

echo ""
if ls -lh output/finecut/test_C.mp4 2>/dev/null; then
  echo "✅ chart-stat 渲染成功！"
else
  echo "❌ 失败"
fi

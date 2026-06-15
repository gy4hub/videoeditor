#!/bin/bash
BASE="/Users/gchyang/文档/synchronize/On going job/2026OMC/Projects/videoeditor/skills/hyperframes-test"
INNER="$BASE/node_modules/hyperframes/node_modules/sharp"

echo "=== 修复 hyperframes 内置 sharp ==="
echo "Node: $(node --version)"

echo ""
echo "[1] 删除内置 sharp (0.34.5)..."
rm -rf "$INNER"
echo "删除完成"

echo ""
echo "[2] 验证 hyperframes 现在使用外层 sharp..."
cd "$BASE"
node -e "
const hfPkg = require('./node_modules/hyperframes/node_modules/sharp/package.json');
console.log('inner sharp still exists:', hfPkg.version);
" 2>/dev/null && echo "警告：内层 sharp 仍存在" || echo "✅ 内层 sharp 已删除，将使用外层 sharp@0.35.1"

echo ""
echo "[3] 测试 hyperframes CLI..."
HF="$BASE/node_modules/.bin/hyperframes"
$HF --version 2>&1

echo ""
echo "[4] 测试渲染..."
cd "/Users/gchyang/文档/synchronize/On going job/2026OMC/Projects/videoeditor"
mkdir -p output/finecut

$HF render skills/hyperframes-test/test-composition \
  --output /tmp/hf_test_out.mp4 \
  --variables '{"number":"90%","label":"小鼠与人类基因相似度","sublabel":"测试","color":"#f59e0b","duration":2}' 2>&1

echo ""
if ls -lh /tmp/hf_test_out.mp4 2>/dev/null; then
  echo "✅ HyperFrames 修复成功！"
else
  echo "❌ 仍然失败"
fi

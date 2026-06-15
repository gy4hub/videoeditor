#!/bin/bash
cd "/Users/gchyang/文档/synchronize/On going job/2026OMC/Projects/videoeditor/skills/hyperframes-test"
echo "=== 重建 sharp native 模块 (Node.js v22) ==="

echo "Node 版本: $(node --version)"
echo "npm 版本: $(npm --version)"

echo ""
echo "[1] 重建 sharp..."
npm rebuild sharp 2>&1

echo ""
echo "[2] 如果 rebuild 失败，尝试重装..."
# 只有在 rebuild 失败时才重装
if [ $? -ne 0 ]; then
  npm install --force 2>&1 | tail -20
fi

echo ""
echo "[3] 验证 HyperFrames..."
cd "/Users/gchyang/文档/synchronize/On going job/2026OMC/Projects/videoeditor"
HF=skills/hyperframes-test/node_modules/.bin/hyperframes
$HF --version 2>&1

echo ""
echo "[4] 测试渲染..."
$HF render skills/hyperframes-test \
  --composition compositions/chart-stat.html \
  --output /tmp/hf_test_out.mp4 \
  --variables '{"number":"33%","label":"测试","sublabel":"HyperFrames验证","color":"#4a9eff","duration":2}' 2>&1

ls -lh /tmp/hf_test_out.mp4 2>/dev/null && echo "✅ 渲染成功" || echo "❌ 渲染失败"

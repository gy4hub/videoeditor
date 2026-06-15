#!/bin/bash
cd "/Users/gchyang/文档/synchronize/On going job/2026OMC/Projects/videoeditor/skills/hyperframes-test"
echo "=== 修复 sharp @ Node.js v22 ==="
echo "Node: $(node --version)"

# sharp >= 0.32 支持 Node.js 22，升级到最新版
echo "[1] 升级 sharp..."
npm install sharp@latest 2>&1 | tail -10

echo ""
echo "[2] 验证..."
node -e "const s=require('sharp'); console.log('sharp OK:', s.versions)" 2>&1

echo ""
echo "[3] 测试 HyperFrames 渲染..."
cd "/Users/gchyang/文档/synchronize/On going job/2026OMC/Projects/videoeditor"
HF=skills/hyperframes-test/node_modules/.bin/hyperframes

$HF render skills/hyperframes-test \
  --composition compositions/chart-stat.html \
  --output /tmp/hf_test_out.mp4 \
  --variables '{"number":"33%","label":"寿命延长","sublabel":"SRN901 vs 对照组","color":"#4a9eff","duration":2}' 2>&1

echo ""
if ls -lh /tmp/hf_test_out.mp4 2>/dev/null; then
  echo "✅ HyperFrames 修复成功！"
else
  echo "❌ 仍然失败，检查 Puppeteer/Chrome..."
  node -e "const pp=require('./skills/hyperframes-test/node_modules/puppeteer'); console.log('puppeteer:', pp.executablePath())" 2>&1
fi

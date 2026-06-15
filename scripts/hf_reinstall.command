#!/bin/bash
cd "/Users/gchyang/文档/synchronize/On going job/2026OMC/Projects/videoeditor/skills/hyperframes-test"
echo "Node: $(node --version)"
echo "=== 重装 node_modules ==="

# 删掉旧的，重新装（自动拉取 Node v22 对应的 prebuilt binary）
rm -rf node_modules package-lock.json
npm install 2>&1 | tail -20

echo ""
echo "=== 验证 sharp ==="
node -e "const s=require('sharp'); console.log('✅ sharp', s.versions.sharp)" 2>&1

echo ""
echo "=== 测试 HyperFrames 渲染 ==="
cd "/Users/gchyang/文档/synchronize/On going job/2026OMC/Projects/videoeditor"
HF=skills/hyperframes-test/node_modules/.bin/hyperframes

$HF render skills/hyperframes-test \
  --composition compositions/chart-stat.html \
  --output output/finecut/insert_C.mp4 \
  --variables '{"number":"90%","label":"小鼠与人类基因相似度","sublabel":"但人类复杂度远高于小鼠","color":"#f59e0b","duration":4}' 2>&1

echo ""
if ls -lh output/finecut/insert_C.mp4 2>/dev/null; then
  echo "✅ HyperFrames 可用！"
else
  echo "❌ 仍失败"
fi

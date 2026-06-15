#!/bin/bash
cd "/Users/gchyang/文档/synchronize/On going job/2026OMC/Projects/videoeditor"
echo "=== HyperFrames 可用性测试 ==="

HF=skills/hyperframes-test/node_modules/.bin/hyperframes
PROJ=skills/hyperframes-test

echo "[1] 检查 HF 可执行："
ls -la $HF 2>&1

echo ""
echo "[2] HF 版本："
$HF --version 2>&1

echo ""
echo "[3] 尝试渲染 chart-stat (10秒超时)..."
timeout 30 $HF render $PROJ \
  --composition compositions/chart-stat.html \
  --output /tmp/hf_test_out.mp4 \
  --variables '{"number":"33%","label":"测试","sublabel":"HyperFrames验证","color":"#4a9eff","duration":2}' 2>&1

echo ""
echo "[4] 输出文件检查："
ls -lh /tmp/hf_test_out.mp4 2>/dev/null || echo "文件不存在"

#!/bin/bash
cd "/Users/gchyang/文档/synchronize/On going job/2026OMC/Projects/videoeditor"
echo "=== test1 粗剪 pipeline ==="

python3 src/pipeline.py run \
  --media reference/test1.MP4 \
  --transcript output/t1_transcript.json \
  --ng-json output/ng_windows.json \
  --outdir output

echo ""
echo "=== 完成 ==="
echo "输出: output/roughcut_hd.mp4"

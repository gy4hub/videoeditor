#!/bin/bash
cd "/Users/gchyang/文档/synchronize/On going job/2026OMC/Projects/videoeditor"
echo "=== test1 粗剪 v7 (清除缓存重跑) ==="

# 删除旧的 roughcut 缓存文件，强制重新渲染
rm -f output/roughcut.mp4
rm -f output/roughcut_hd.mp4

# 重跑 pipeline（已有 transcript + edl_ng + edl_snapped，只跑 concat + qc）
python3 src/pipeline.py run \
  --media reference/test1.MP4 \
  --transcript output/t1_transcript.json \
  --ng-json output/ng_windows.json \
  --outdir output \
  --no-enhance

echo ""
echo "=== 完成 ==="
ls -lh output/roughcut.mp4 2>/dev/null || echo "ERROR: roughcut.mp4 未生成"

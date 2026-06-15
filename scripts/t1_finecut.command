#!/bin/bash
cd "/Users/gchyang/文档/synchronize/On going job/2026OMC/Projects/videoeditor"
echo "=== test1 精剪：HyperFrames 渲染 + ffmpeg 拼接 ==="

HF=skills/hyperframes-test/node_modules/.bin/hyperframes
PROJ=skills/hyperframes-test
SRC=output/roughcut.mp4
OUT=output/finecut

mkdir -p "$OUT"

# ───────────────────────────────────────────────
# 渲染 A: chart-bar — SRN901核心数据 (插入 ~20s，时长5s)
# ───────────────────────────────────────────────
echo "[1/3] 渲染 chart-bar..."
$HF render $PROJ \
  --composition compositions/chart-bar.html \
  --output $OUT/insert_A.mp4 \
  --variables '{"title":"SRN901核心数据","unit":"%","duration":5,"bars":[{"label":"中位寿命延长","value":33,"color":"#4a9eff"},{"label":"衰弱进展缓解","value":70,"color":"#52e5a0"}]}'

# ───────────────────────────────────────────────
# 渲染 B: text-highlight — TAT2 概念 (插入 ~104.5s，时长3s)
# ───────────────────────────────────────────────
echo "[2/3] 渲染 text-highlight..."
$HF render $PROJ \
  --composition compositions/text-highlight.html \
  --output $OUT/insert_B.mp4 \
  --variables '{"lines":["TAT2","环黄芪醇"],"accent":"#4a9eff","caption":"端粒酶激活剂，2000年代明星化合物","duration":3}'

# ───────────────────────────────────────────────
# 渲染 C: chart-stat — 90%基因相似 (插入 ~166.5s，时长4s)
# ───────────────────────────────────────────────
echo "[3/3] 渲染 chart-stat..."
$HF render $PROJ \
  --composition compositions/chart-stat.html \
  --output $OUT/insert_C.mp4 \
  --variables '{"number":"90%","label":"小鼠与人类基因相似度","sublabel":"但人类复杂度远高于小鼠","color":"#f59e0b","duration":4}'

echo ""
echo "=== HyperFrames 渲染完成，开始 ffmpeg 拼接 ==="

# ───────────────────────────────────────────────
# 拼接 A: insert @ 20s，时长5s
# 视频换动画，音频保留粗剪配音 (insert模式)
# ───────────────────────────────────────────────
AT_A=20
DUR_A=5

echo "[A] 拼接 chart-bar @ ${AT_A}s..."
ffmpeg -y -ss 0 -to $AT_A -i $SRC -c:v libx264 -crf 20 -preset veryfast -c:a aac $OUT/A_before.mp4
ffmpeg -y \
  -ss $AT_A -to $((AT_A + DUR_A)) -i $SRC \
  -i $OUT/insert_A.mp4 \
  -map 1:v:0 -map 0:a:0 \
  -c:v libx264 -crf 20 -preset veryfast -c:a aac \
  $OUT/A_overlay.mp4
ffmpeg -y -ss $((AT_A + DUR_A)) -i $SRC -c:v libx264 -crf 20 -preset veryfast -c:a aac $OUT/A_after.mp4

printf "file 'A_before.mp4'\nfile 'A_overlay.mp4'\nfile 'A_after.mp4'\n" > $OUT/A_list.txt
ffmpeg -y -f concat -safe 0 -i $OUT/A_list.txt -c copy $OUT/stage_A.mp4

# ───────────────────────────────────────────────
# 拼接 B: insert @ 109.5s (104.5+5 因为A插入了5s)
# ───────────────────────────────────────────────
AT_B=109
DUR_B=3

echo "[B] 拼接 text-highlight @ ${AT_B}s..."
ffmpeg -y -ss 0 -to $AT_B -i $OUT/stage_A.mp4 -c:v libx264 -crf 20 -preset veryfast -c:a aac $OUT/B_before.mp4
ffmpeg -y \
  -ss $AT_B -to $((AT_B + DUR_B)) -i $OUT/stage_A.mp4 \
  -i $OUT/insert_B.mp4 \
  -map 1:v:0 -map 0:a:0 \
  -c:v libx264 -crf 20 -preset veryfast -c:a aac \
  $OUT/B_overlay.mp4
ffmpeg -y -ss $((AT_B + DUR_B)) -i $OUT/stage_A.mp4 -c:v libx264 -crf 20 -preset veryfast -c:a aac $OUT/B_after.mp4

printf "file 'B_before.mp4'\nfile 'B_overlay.mp4'\nfile 'B_after.mp4'\n" > $OUT/B_list.txt
ffmpeg -y -f concat -safe 0 -i $OUT/B_list.txt -c copy $OUT/stage_B.mp4

# ───────────────────────────────────────────────
# 拼接 C: insert @ 179.5s (166.5+5+3=174.5 → 约175s)
# ───────────────────────────────────────────────
AT_C=174
DUR_C=4

echo "[C] 拼接 chart-stat @ ${AT_C}s..."
ffmpeg -y -ss 0 -to $AT_C -i $OUT/stage_B.mp4 -c:v libx264 -crf 20 -preset veryfast -c:a aac $OUT/C_before.mp4
ffmpeg -y \
  -ss $AT_C -to $((AT_C + DUR_C)) -i $OUT/stage_B.mp4 \
  -i $OUT/insert_C.mp4 \
  -map 1:v:0 -map 0:a:0 \
  -c:v libx264 -crf 20 -preset veryfast -c:a aac \
  $OUT/C_overlay.mp4
ffmpeg -y -ss $((AT_C + DUR_C)) -i $OUT/stage_B.mp4 -c:v libx264 -crf 20 -preset veryfast -c:a aac $OUT/C_after.mp4

printf "file 'C_before.mp4'\nfile 'C_overlay.mp4'\nfile 'C_after.mp4'\n" > $OUT/C_list.txt
ffmpeg -y -f concat -safe 0 -i $OUT/C_list.txt -c copy $OUT/stage_C.mp4

# ───────────────────────────────────────────────
# 最终输出
# ───────────────────────────────────────────────
cp $OUT/stage_C.mp4 output/finecut.mp4

echo ""
echo "=== 完成 ==="
ls -lh output/finecut.mp4
echo "预期总时长: ~$(echo '255 + 5 + 3 + 4' | bc)s (粗剪255s + 3个动画12s)"

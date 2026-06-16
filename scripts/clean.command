#!/bin/bash
# clean — 清理视频制作的过程文件，回收磁盘。Mac 双击运行。
# 默认 DRY-RUN（只列出、不删）。确认无误后改 DRYRUN=0 再跑，或运行时带 --force。
# 保留：最终成片（*_finecut.mp4、roughcut.mp4）、转写 json、spec json、源片(reference/)。

cd "/Users/gchyang/文档/synchronize/On going job/2026OMC/Projects/videoeditor" || exit 1

DRYRUN=1
[ "$1" = "--force" ] && DRYRUN=0

echo "=== 清理过程文件 ($([ $DRYRUN = 1 ] && echo '预演 DRY-RUN，不删除' || echo '实删 --force')) ==="

# 中间产物匹配规则（grep -E）。保留最终成片与转写/spec。
PATTERNS='_v[0-9]|_preview|_precise|_fix|_verify_|/seg_|/stage_|/[ABC]_(before|after|overlay)|_src_(mid|tail)|/insert_[ABC]'

# 1) output/ 下匹配中间产物的 mp4
MATCHED=$(find output -type f -name "*.mp4" 2>/dev/null | grep -E "$PATTERNS")
# 2) output/finecut 下的抽帧目录
FRAMES=$(find output -type d \( -name frames -o -name vframes -o -name eframes \) 2>/dev/null)
# 3) finecut 渲染项目残留（软链/生成的 index.html/拷贝的 aroll）
RENDER_LEFT=$(ls skills/finecut/render_project/index.html skills/finecut/render_project/aroll.mp4 2>/dev/null)

TOTAL=0
list_and_size() {
  for f in "$@"; do
    [ -e "$f" ] || continue
    sz=$(du -sk "$f" 2>/dev/null | cut -f1)
    TOTAL=$((TOTAL + sz))
    printf "  %6s MB  %s\n" "$((sz/1024))" "$f"
  done
}

echo "[中间 mp4]"; list_and_size $MATCHED
echo "[抽帧目录]"; list_and_size $FRAMES
echo "[渲染残留]"; list_and_size $RENDER_LEFT

echo ""
echo "可回收合计：约 $((TOTAL/1024)) MB"

if [ $DRYRUN = 1 ]; then
  echo ""
  echo ">> 这是预演，未删除任何文件。"
  echo ">> 确认无误后：在终端运行  bash scripts/clean.command --force"
  exit 0
fi

echo ""
echo "删除中..."
for f in $MATCHED $RENDER_LEFT; do rm -f "$f"; done
for d in $FRAMES; do rm -rf "$d"; done
echo "完成。当前 output/ 占用：$(du -sh output/ 2>/dev/null | cut -f1)"

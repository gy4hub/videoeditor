#!/bin/bash
# finecut 精剪渲染（磨砂叠加层范式）— Mac 双击运行
# 用法：改下面 SPEC / AROLL / TOTAL / OUT 四个变量，或直接双击跑牛初乳示例。
# 渲染需 Chrome（chrome-headless-shell），ffmpeg 8+。

cd "/Users/gchyang/文档/synchronize/On going job/2026OMC/Projects/videoeditor" || exit 1

# ── 改这里 ───────────────────────────────────────────────
SPEC="skills/finecut/examples/niuchuru_spec.json"
AROLL="reference/粗剪_牛初乳老树开心花.mp4"
TOTAL=154.45                       # A-roll 总秒数（ffprobe 可查）
OUT="output/finecut/niuchuru_finecut.mp4"
# ─────────────────────────────────────────────────────────

echo "=== finecut 精剪渲染 ==="
echo "spec : $SPEC"
echo "aroll: $AROLL"
echo ""

# 1) 校验 spec
echo "[1/2] 校验 finecut-spec..."
python3 -c "import sys; sys.path.insert(0,'.'); from skills.finecut.spec import load_spec, validate
errs = validate(load_spec('$SPEC'))
print('  校验失败：' + '; '.join(errs)) or sys.exit(1) if errs else print('  OK ✓')" || { echo 'spec 校验未通过，已中止'; exit 1; }

# 2) 渲染（A-roll 视频轨 + 磨砂叠加层，单 composition，一次渲染）
echo "[2/2] 渲染中（整片需几分钟，Chrome 逐帧）..."
python3 skills/finecut/finecut.py render --spec "$SPEC" --aroll "$AROLL" --total "$TOTAL" --out "$OUT"

echo ""
echo "=== 完成 ==="
ls -lh "$OUT" 2>/dev/null || echo "未生成输出，请看上方报错"
echo "（抽帧验收：ffmpeg -ss <插入时刻> -i $OUT -frames:v 1 frame.png）"

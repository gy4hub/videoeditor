# Mac 一键 Runbook — G2 关闭 Sprint 4

> 在 **Mac 本机**执行（沙箱无 Chrome，渲染必须在这里跑）。
> 全程在仓库根目录。预计 10–15 分钟。

## 0. 一次性准备

```bash
cd <仓库根目录>
cd skills/hyperframes-test
npm install                      # 若 node_modules 缺失（已 gitignore）
node_modules/.bin/hyperframes doctor   # 确认 Chrome + ffmpeg 就绪
```

`doctor` 若报缺 Chrome：`node_modules/.bin/hyperframes browser install`。

## 1. S4-6 — 三个模板各渲一段（验证无报错）

```bash
cd skills/hyperframes-test
HF=node_modules/.bin/hyperframes
mkdir -p ../../output/finecut

# lint 必须先 0 error（沙箱已修，复核一次）
$HF lint .

# chart-bar
$HF render . --composition compositions/chart-bar.html \
  --output ../../output/finecut/_test_bar.mp4 \
  --variables '{"title":"对比","unit":"%","duration":5,"bars":[{"label":"A","value":100,"color":"#888"},{"label":"B","value":133,"color":"#4a9eff"}]}'

# chart-stat
$HF render . --composition compositions/chart-stat.html \
  --output ../../output/finecut/_test_stat.mp4 \
  --variables '{"number":"70%","label":"衰弱进展缓解","sublabel":"SRN901三期临床","color":"#52e5a0","duration":4}'

# text-highlight
$HF render . --composition compositions/text-highlight.html \
  --output ../../output/finecut/_test_text.mp4 \
  --variables '{"lines":["端粒酶","激活"],"accent":"#4a9eff","caption":"端粒是细胞衰老的关键","duration":3}'
```

**✅ 通过标准**：三条命令无报错，`output/finecut/_test_*.mp4` 能播放，动画正常。
**关注点**：图表里的中文字（标题/添爸说）是否字体正常 —— lint 警告过 CJK 字体会回退。若看着别扭，记下来反馈，决定是否补 woff2。

想快速看效果而不渲完整 MP4：`$HF preview .`（开 studio）或 `$HF snapshot . --composition compositions/chart-bar.html`（出关键帧 PNG）。

## 2. S4-7 — 端到端精剪一段真实成片

前提：`output/roughcut_hd.mp4`（或现有 `output/roughcut.mp4`）存在。
按 `skills/finecut/SKILL.md` 的「执行命令」节：选 1–2 个真实数据点 → 渲染对应模板 → ffmpeg insert 拼回粗剪。

最小验证（插一个 chart-stat 到第 `<at_s>` 秒）：

```bash
# 渲染（用真实数据替换）
$HF render . --composition compositions/chart-stat.html \
  --output ../../output/finecut/d1.mp4 \
  --variables '{"number":"...","label":"...","sublabel":"...","color":"#52e5a0","duration":4}'

# 回到根目录做 insert（参数见 SKILL.md「拼接进粗剪」）
cd ../..
# ① 前段 ② 动画段(视频换HF/音频留配音) ③ 后段 ④ concat
```

**✅ 通过标准**：`output/finecut/finecut.mp4` 出片，插入点在停顿处、音画不断、动画正常。

## 3. 收口（验收通过后）

把结果回报给 Claude，由 Claude：
- 在 `docs/02_Scrum_Sprint规划.md` 把 S4-6 / S4-7 标 ✅、S4 整体标 ✅；
- 在 `Goal-G2.md` 补 Verification Evidence（doctor 输出、成片路径）；
- 决定 CJK 字体警告处置（接受 / 开补字体子任务）。

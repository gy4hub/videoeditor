---
name: finecut
description: 精剪 — 在粗剪 A-roll 里插入 HyperFrames 动画和 B-roll。当用户说"加图表""加动画""精剪""加 B-roll""数据可视化"时使用。
---

# Finecut Skill

## 你的任务

读粗剪 EDL (`output/edl_snapped.json`) 和定稿脚本，决定在哪里插什么，然后直接执行渲染和拼接。

---

## 可用组件（3 个）

### `chart-bar` — 柱状对比图
**何时用**：讲到两个以上数值对比（"A 比 B 高 33%"、"三组数据"）
**何时不用**：单个数字（→ chart-stat）、纯文字强调（→ text-highlight）
**变量**：`title`（图表标题）、`unit`（单位，可空）、`bars`（数组，每项含 `label/value/color`）、`duration`（秒）

### `chart-stat` — 大数字强调
**何时用**：一个有冲击力的数字独立成镜（"70%"、"133%"、"10倍"）
**何时不用**：有多组数据（→ chart-bar）
**变量**：`number`（主数字含单位）、`label`（一句话说明）、`sublabel`（来源/备注）、`color`（accent色）、`duration`

### `text-highlight` — 关键词飞入
**何时用**：抛出新名词/概念时（"端粒酶"、"mTOR 通路"），或段落转场前的停顿
**何时不用**：有数字（→ chart-stat 或 chart-bar）
**变量**：`lines`（数组，1-2行文字）、`accent`（竖线颜色）、`caption`（底部小字，可空）、`duration`

---

## 执行命令

### 渲染动画（在 Mac 本机执行，沙箱无 Chrome）

```bash
HF=skills/hyperframes-test/node_modules/.bin/hyperframes
PROJ=skills/hyperframes-test

# chart-bar
$HF render $PROJ \
  --composition compositions/chart-bar.html \
  --output output/finecut/<id>.mp4 \
  --variables '{"title":"...","unit":"%","duration":5,"bars":[{"label":"A","value":100,"color":"#888"},{"label":"B","value":133,"color":"#4a9eff"}]}'

# chart-stat
$HF render $PROJ \
  --composition compositions/chart-stat.html \
  --output output/finecut/<id>.mp4 \
  --variables '{"number":"70%","label":"衰弱进展缓解","sublabel":"SRN901三期临床","color":"#52e5a0","duration":4}'

# text-highlight
$HF render $PROJ \
  --composition compositions/text-highlight.html \
  --output output/finecut/<id>.mp4 \
  --variables '{"lines":["端粒酶","激活"],"accent":"#4a9eff","caption":"端粒是细胞衰老的关键","duration":3}'
```

### 拼接进粗剪（insert 模式 — 视频换动画，配音不断）

```bash
# ① 切前段
ffmpeg -y -ss 0 -to <at_s> -i output/roughcut_hd.mp4 -c:v libx264 -crf 20 -preset veryfast -c:a aac output/finecut/seg_before.mp4

# ② 动画段：视频用 HyperFrames 渲染结果，音频用 roughcut 配音
ffmpeg -y \
  -ss <at_s> -to <at_s+dur> -i output/roughcut_hd.mp4 \
  -i output/finecut/<id>.mp4 \
  -map 1:v:0 -map 0:a:0 \
  -c:v libx264 -crf 20 -preset veryfast -c:a aac \
  output/finecut/seg_overlay.mp4

# ③ 切后段
ffmpeg -y -ss <at_s+dur> -i output/roughcut_hd.mp4 -c:v libx264 -crf 20 -preset veryfast -c:a aac output/finecut/seg_after.mp4

# ④ concat
printf "file 'seg_before.mp4'\nfile 'seg_overlay.mp4'\nfile 'seg_after.mp4'\n" > output/finecut/list.txt
ffmpeg -y -f concat -safe 0 -i output/finecut/list.txt -c copy output/finecut/finecut.mp4
```

### B-roll cutaway（视频+音频全切）

```bash
ffmpeg -y -ss 0 -t <dur> -i reference/<broll>.mp4 \
  -c:v libx264 -crf 20 -preset veryfast -c:a aac \
  output/finecut/seg_broll.mp4
```

---

## 决策规则

**插入点选择**：
- 看 EDL 的 segment 边界，找停顿 ≥ 0.5s 的缝隙
- 优先选"刚说完一个数据"之后的位置
- B-roll 选"讲到场景/动作/实验"的时候

**时长**：
- `chart-bar` 推荐 4-6s
- `chart-stat` 推荐 3-4s  
- `text-highlight` 推荐 2.5-3.5s
- B-roll 推荐 3-5s

**禁止**：
- 不在句子中间切
- 不捏造数据，所有数值来自定稿脚本或已知公开数据
- 同一段落不插超过 1 个动画

---

## 输出

成片：`output/finecut/finecut.mp4`

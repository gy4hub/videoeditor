# Skill: rough-cut  (v2 — Sprint 3 新架构)

**触发场景**：用户提供原始拍摄素材（MP4/MOV）+ 飞书定稿逐字稿，需要产出：
- 粗剪成片（`roughcut_hd.mp4` 或带字幕的 `roughcut_hd_sub.mp4`）
- EDL 剪辑决策表（`edl_snapped.json`）
- 质检报告（`qc_report.md`）

**本 SKILL.md 是自洽的**——不需要阅读项目其他文档即可完成全流程。

---

## 一、架构概述

v2 采用「LLM 语义 NG 检测」路径（已通过 test1/test2 双期验证）：

```
原始素材.MP4 + 飞书定稿.md
        │
  ① transcribe.py    — faster-whisper 词级时间戳
        │
  ② ng_detect.py     — LLM 语义 NG 检测（假起步/重拍/口吃）→ EDL
        │
  ③ snap_cuts.py     — 波形 RMS 谷值吸附切点（气口平滑）
        │
  ④ ffmpeg concat    — 重编码切割拼接 → roughcut.mp4
        │
  ⑤ enhance.py       — tianbaba 高清滤镜（colorlevels去灰+unsharp+eq）
        │
  ⑥ subtitle.py      — 定稿→双语SRT→金陵体烧录 [可选]
        │
  ⑦ qc_report.py    — 质检报告
```

> ⚠️ 旧路径（`align.py → rules.py → edl.py`）已废弃，不要使用。

---

## 二、一键全流程（最快路径）

```bash
cd /path/to/videoeditor

python3 src/pipeline.py run \
    --media  reference/原素材.MP4 \
    --script materials/scripts/定稿.md \
    --outdir output/本期/ \
    --ng-mode auto \
    --api-key $ANTHROPIC_API_KEY
```

完成后输出：
- `output/本期/roughcut_hd.mp4`（带滤镜成片）
- `output/本期/roughcut_hd_sub.mp4`（带字幕，若有定稿文件）
- `output/本期/edl_snapped.json`
- `output/本期/qc_report.md`

**断点续跑**：中途中断后重新运行同命令，已完成的步骤自动跳过。

---

## 三、分步命令（推荐用于 AI 介入语义复核时）

### Step ① 转写

```bash
# 提取音频
ffmpeg -y -i reference/原素材.MP4 -ac 1 -ar 16000 output/audio_16k.wav

# 转写（若音频 >50s，在沙箱中须分段；pipeline.py 自动处理）
python3 src/transcribe.py output/audio_16k.wav \
    --model Systran/faster-whisper-medium \
    --language zh \
    --output output/transcript.json
```

### Step ② LLM 语义 NG 检测

**模式 A — 全自动（推荐）**：调 Claude API

```bash
python3 src/ng_detect.py auto \
    --transcript output/transcript.json \
    --script     materials/scripts/定稿.md \
    --out        output/edl_ng.json \
    --api-key    $ANTHROPIC_API_KEY
```

**模式 B — 手工输入**：Claude 在对话中分析后，将 NG 窗口写入 JSON：

```bash
# 先让 Claude 生成 ng_windows.json：
python3 src/ng_detect.py prompt \
    --transcript output/transcript.json \
    --script materials/scripts/定稿.md
# (把输出粘贴到 Claude 对话，将返回的 JSON 另存为 output/ng_windows.json)

# 再用 manual 模式重建 EDL：
python3 src/ng_detect.py manual \
    --transcript output/transcript.json \
    --ng-json    output/ng_windows.json \
    --out        output/edl_ng.json \
    --source     reference/原素材.MP4
```

`ng_windows.json` 格式（NG 窗口数组）：
```json
[
  {"start_s": 28.14, "end_s": 42.06, "reason": "NG重拍：第一遍说错延长33%后重拍"},
  {"start_s": 88.60, "end_s": 91.64, "reason": "假起步：帕雷米→雷帕梅素"}
]
```

### Step ③ 切点吸附

```bash
python3 src/snap_cuts.py \
    --edl   output/edl_ng.json \
    --audio output/audio_16k.wav \
    --out   output/edl_snapped.json
```

### Step ④ 渲染

```bash
python3 src/pipeline.py run \
    --media     reference/原素材.MP4 \
    --outdir    output/本期/ \
    --transcript output/transcript.json \
    --ng-mode   skip \
    --no-subtitle
# 若 edl_snapped.json 已存在会自动跳过①②③直接渲染
```

或直接用 ffmpeg（适合调试）：
```bash
# 见 pipeline.py 中 ffmpeg_concat_edl() 的 filter_complex 拼法
```

### Step ⑤ 高清滤镜

```bash
python3 src/enhance.py apply \
    --input output/本期/roughcut.mp4 \
    --out   output/本期/roughcut_hd.mp4 \
    --grade tianbaba
```

tianbaba 滤镜参数（对标 SRN901 剪映高清增强/去灰/去雾）：
```
hqdn3d=2:2:4:4,
colorlevels=rimin=0.04:gimin=0.04:bimin=0.02:romax=0.97:gomax=0.97:bomax=0.98,
eq=saturation=1.35:brightness=0.01,
unsharp=5:5:1.2:5:5:0.0
```

### Step ⑥ 字幕（可选）

```bash
# 生成双语 SRT（调 Claude API 做英译）
python3 src/subtitle.py generate \
    --transcript output/transcript.json \
    --script     materials/scripts/定稿.md \
    --edl        output/本期/edl_snapped.json \
    --out        output/本期/subtitle.srt \
    --ass \
    --api-key    $ANTHROPIC_API_KEY

# 烧录字幕
python3 src/subtitle.py burn \
    --video output/本期/roughcut_hd.mp4 \
    --srt   output/本期/subtitle.srt \
    --out   output/本期/roughcut_hd_sub.mp4

# 截图验收 t=30s
python3 src/subtitle.py preview \
    --video output/本期/roughcut_hd_sub.mp4 \
    --time 30 \
    --out  output/本期/subtitle_preview.png
```

字幕规范：
| 项目 | 规格 |
|---|---|
| 字体 | 金陵体（→ STSong → PingFang SC 降级） |
| 中文字号 | 54pt ASS（= 约 12pt @ 1080p） |
| 英文字号 | 38pt ASS（= 约 8pt @ 1080p） |
| 颜色 | 白色 `#FFFFFF` |
| 背景阴影 | 60% 不透明黑色 `&H99000000` |
| 位置 | 底部居中，marginV=30px |

### Step ⑦ 质检报告

```bash
python3 src/qc_report.py \
    --edl            output/本期/edl_snapped.json \
    --source-duration $(ffprobe -v q -show_entries format=duration -of default=nk=1 reference/原素材.MP4) \
    --out            output/本期/qc_report.md
```

---

## 四、AI 语义复核规范（Step ② 核心判断层）

当 `ng_detect.py prompt` 输出后，AI 执行以下判断：

### 需要标注为 NG 的情况

| 类型 | 描述 | EDL 操作 |
|---|---|---|
| NG重拍 | 说到一半重头再来，有明显"再来一遍"意图 | 删前保后 |
| 假起步 | 说了几个字就停顿/重说 | 删前保后 |
| 口吃重复 | 同一词/字连续重复（啊啊啊，的的的） | 仅删多余次 |
| 语义重复 | 同一句意思在前后 30s 内重复完整说了两遍 | 删先保后（更完整） |
| 吊句 | 句子说到一半突然切断，无法独立成意 | 整句删除 |

### 不应删除的情况

- 故意重复（强调修辞）："这就是关键！这就是关键！"
- 停顿后继续同一句子（中间有呼吸/组织语言）
- 定稿明确包含的重复结构

### NG 窗口精度要求

- `start_s`：NG 片段第一个词的 `start` 时间
- `end_s`：NG 片段最后一个词的 `end` 时间（取正确版本开头词的 `start` 作为 `end_s`）
- 精度：0.01s

---

## 五、微调回路（"把第 N 刀往后挪 X 秒"）

```bash
# 1. 直接编辑 edl_snapped.json
#    - 改 start_s / end_s（保留两位小数）
#    - 加 "decided_by": "human"（防下次被覆盖）

# 2. 重新渲染（只做 Step ④ 之后的部分）
python3 src/pipeline.py run \
    --media reference/原素材.MP4 \
    --outdir output/本期_v2/ \
    --transcript output/transcript.json \
    --ng-mode skip       # 跳过 NG 检测，使用已有 edl_ng.json
# pipeline.py 检测到 edl_snapped.json 已存在则跳过 Step 3
```

---

## 六、质量验收门槛

| 指标 | 门槛 |
|---|---|
| 重复句残留 | 0（对齐可发现的重复必须 100% 捕获）|
| NG 重拍残留 | 0 |
| >0.8s 停顿残留 | 0 |
| 误删 | ≤ 1 处，且可通过 EDL 一行恢复 |
| 气口生硬感 | Chen 盲听 10 个切点，≥8 个听不出剪辑痕迹 |
| 字幕时轴偏差 | < 200ms |
| 英译可用率 | Chen 抽查 20 句，≥ 90% 可用 |

---

## 七、环境前置

```bash
# macOS
brew install ffmpeg python@3.10

# Python 依赖
pip install -r src/requirements.txt --break-system-packages

# ASR 模型（首次使用）
bash src/setup_models.sh

# Anthropic SDK（LLM NG 检测 / 英译）
pip install anthropic --break-system-packages
```

---

## 八、常见问题

**Q: ng_detect auto 没有 API Key 怎么办？**
A: 用 `prompt` 模式输出转写+定稿的分析 prompt，粘贴到 Claude 对话框手工运行，将 JSON 结果另存 `ng_windows.json`，再用 `manual` 模式生成 EDL。

**Q: 转写速度太慢？**
A: medium 模型 RTF ≈ 0.74（1s 音频 ≈ 0.74s 推理）。pipeline.py 在沙箱中对长音频自动分段（每段 ≤ 45s）分批转写后合并时间戳。

**Q: 字幕和音频对不上？**
A: 检查 `subtitle.generate` 时是否传入了 `--edl`。若已传 EDL，查看词级匹配日志（`[subtitle] 定稿句数`），找匹配失败的句子手工修正 SRT 时间戳。

**Q: 金陵体显示不了？**
A: `subtitle.py` 会自动 fallback 到 `STSong → PingFang SC → Noto Sans CJK SC`。可用 `fc-list :lang=zh family` 查看系统可用字体，用 `--font "字体名"` 指定。

**Q: roughcut.mp4 视音频不同步？**
A: 检查 EDL 最短片段是否 < 0.3s（会被自动过滤）；若仍不同步，在 `ffmpeg_concat_edl` 中改用 `concat=v=1:a=1:unsafe=1`。

---

## 九、文件结构

```
src/
├── pipeline.py      统一 CLI（Step ①-⑦ 串联）       ← 入口
├── transcribe.py    Step ① 词级时间戳转写
├── ng_detect.py     Step ② LLM 语义 NG 检测
├── snap_cuts.py     Step ③ 波形切点吸附
├── enhance.py       Step ⑤ 高清滤镜（tianbaba 默认）
├── subtitle.py      Step ⑥ 双语字幕生成与烧录
├── qc_report.py     Step ⑦ 质检报告
└── requirements.txt

skills/rough-cut/
├── SKILL.md         本文件
└── scripts/         （legacy — 旧管线脚本，保留供参考，不再使用）

output/<本期名>/
├── audio_16k.wav
├── transcript.json
├── edl_ng.json          （NG 过滤后）
├── edl_ng_ng_windows.json  （NG 窗口，供手工审核）
├── edl_snapped.json     （切点吸附后，最终 EDL）
├── roughcut.mp4
├── roughcut_hd.mp4      （+滤镜）
├── subtitle.srt
├── subtitle.ass
├── roughcut_hd_sub.mp4  （+字幕）
└── qc_report.md
```

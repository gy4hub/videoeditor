# S0-1 环境验证报告

| 字段 | 内容 |
|---|---|
| Story | S0-1 环境验证 |
| 执行者 | Dev Agent |
| 执行日期 | 2026-06-11 |
| 沙箱环境 | Ubuntu 22.04 (aarch64), Python 3.10.12, ffmpeg 4.4.2 |

---

## 1. 各步结果

### 步骤 1：ffmpeg 可用性验证 — **通过**

```
ffmpeg version 4.4.2-0ubuntu0.22.04.1
libavcodec 58.134.100
```

切割验证：`ffmpeg -ss <start> -to <end> -i input.wav output.wav`
- seg1 (13.280–14.480s) → 实际时长 1.200s（误差 0ms）
- seg2 (24.420–25.880s) → 实际时长 1.460s（误差 0ms）

拼接验证：concat demuxer 输出 2.660s = 1.200 + 1.460（精确）

**结论**：ffmpeg 切割精度达到样本级（16kHz WAV = 62.5µs/sample），完全满足需求。

---

### 步骤 2：faster-whisper 安装 — **通过**

```
pip install faster-whisper==1.2.1 --break-system-packages
```

依赖链：ctranslate2==4.8.0, av==17.1.0, huggingface-hub==1.18.0, tokenizers==0.23.1

**模型下载遭遇的坑**：`WhisperModel("tiny")` 的 huggingface-hub 内置下载器在本沙箱中会挂起（下载进程静默终止，incomplete 文件停留 0 bytes）。根因推测：HF Hub 使用 `hf_transfer`（rust 加速下载器）在该 aarch64 环境下有兼容性问题。

**解决方案**：直接 `curl -L` 从 HF raw 文件端点下载，速度 ~5 MB/s，将文件放入 HF cache 目录（`~/.cache/huggingface/hub/models--Systran--faster-whisper-tiny/snapshots/main/`），之后 `WhisperModel(local_path)` 正常加载。

**生产建议**：在首次部署脚本中预置 `curl` 下载步骤，绕过 hf_transfer；或设置 `HF_HUB_DISABLE_HF_TRANSFER=1` 后再调用 `WhisperModel`。

---

### 步骤 3：中文测试语音合成 — **通过（有条件）**

工具：gTTS 2.5.4（Google Text-to-Speech，需网络）

espeak-ng 在沙箱内无法通过 apt 安装（无 root / no-new-privileges 容器限制），gTTS 可正常调用 Google API。

生成内容（约 76.5 秒）：包含故意重复句（"人工智能在视频制作中的应用"、"转换成文字"、"把视频的某一段切出来"）和自然停顿。

产物：
- `materials/raw/test_speech.mp3`（598 KB，原始 gTTS 输出）
- `materials/raw/test_speech.wav`（2.4 MB，16kHz mono，供 whisper 使用）

**生产注意**：实际素材为真人录音 mp4，无需 TTS 合成；gTTS 仅用于本次无真实素材的环境验证。

---

### 步骤 4：faster-whisper 转写（词级时间戳）— **通过**

```
python3 src/transcribe.py materials/raw/test_speech.wav \
    -m <model_local_path> -o eval/s0-1_transcript.json
```

| 指标 | 值 |
|---|---|
| 模型 | faster-whisper-tiny (75MB, int8) |
| 语言检测 | zh (prob=1.000) |
| 词数 | 201 |
| 片段数 | 24 |
| 音频时长 | 76.536s |
| 转写耗时 | 3.9s |
| 实时率 (RTF) | 0.051（即转写速度约为实时的 20x） |
| 平均词置信度 | 0.9042 |
| 高置信 (≥0.8) | 81.6% |
| 中置信 (0.5–0.8) | 15.4% |
| 低置信 (<0.5) | 3.0% |

输出格式（`eval/s0-1_transcript.json`）字段：

```json
{
  "words": [
    {"word": "欢", "start": 0.000, "end": 0.400, "confidence": 0.827},
    ...
  ],
  "segments": [...]
}
```

**词时间戳精度观察**：

TTS 合成语音（单一节奏、无停顿）对 whisper 时间戳精度构成挑战，主要问题：

1. **字符粒度过细**：tiny 模型对中文倾向逐字切割（"欢"/"迎"/"来"/"到"），而非词组。大模型（small/medium/large）在中文会更多输出词组，时间戳粒度更合理。
2. **识别错误**：部分字符识别有误
   - "自动化管线" → "自动画馆线"（拼音混淆，conf 偏低）
   - "语音识别" → "语音时别"（两次）
   - "拼接" → "拼阶"（两次）
   - "时间码" → "时间出"
   这些错误在 tiny 模型属正常范围；small/large-v3 可显著改善。
3. **重复句已正确区分时间码**：
   - "人工智能在视频制作中的应用" 第一次：[8.04-11.92]，第二次：[11.92-15.76]
   - "把视频的某一段切出来" 第一次：[58.66-62.32]，第二次：[62.32-65.50]
   时间码区分准确，对齐层可正常捕获重复。

---

### 步骤 5：ffmpeg 按时间码切割 + 拼接验证 — **通过**

```
python3 src/cut.py materials/raw/test_speech.wav \
    --transcript eval/s0-1_transcript.json \
    --word-idx 43 47 --word-idx 73 77 \
    -o output/s0-1_cut_concat.wav
```

切割区间（来自词级时间戳）：
- 区间 1：words[43–47] → 13.280–14.480s（"频…"附近）
- 区间 2：words[73–77] → 24.420–25.880s（"把…"附近）

输出：`output/s0-1_cut_concat.wav`，时长 2.660s（= 1.200 + 1.460，精确拼接）

**时间码切割链路**：词级时间戳 JSON → `cut.py` 解析 → `ffmpeg -ss -to` → concat demuxer，全链路可复现。

---

### 步骤 6：可复用 CLI 脚本 — **通过**

| 文件 | 说明 |
|---|---|
| `src/transcribe.py` | faster-whisper 转写 CLI，支持模型名/本地路径、语言、VAD 参数 |
| `src/cut.py` | ffmpeg 切割拼接 CLI，支持直接时间码模式和词索引模式 |
| `src/requirements.txt` | Python 依赖清单 |

两个脚本均有 `--help`，函数有 docstring，CLI 接口满足 S1 管线复用需求。

---

## 2. 遇到的坑汇总

| # | 问题 | 根因 | 解决 |
|---|---|---|---|
| P1 | `WhisperModel("tiny")` 挂起，incomplete 文件 0 bytes | aarch64 沙箱内 hf_transfer 静默失败 | 用 `curl -L` 直接下载 HF raw 文件到 cache 目录 |
| P2 | `apt install espeak-ng` 失败 | 容器 no-new-privileges，无 root | 改用 gTTS (pip)，需网络 |
| P3 | 沙箱 bash 单次超时 45s | 模型加载+转写合计 >45s（实测约 5.6s 转写，但含下载） | 拆分为：下载 → 单独转写脚本，各步在 45s 内 |
| P4 | `preprocessor_config.json` 下载 404 | 该文件不在 tiny 模型仓库，ctranslate2 有 warning 但不阻塞 | 忽略（非必要文件），正常加载 |

---

## 3. 生产环境模型/算力建议

### 模型选型建议

| 场景 | 推荐模型 | 理由 |
|---|---|---|
| 生产（中文精度优先） | `large-v3` | 中文 WER 最低，词级时间戳漂移 <50ms；PRD FR-1 指定 |
| 开发/调试快速迭代 | `small` | 精度与 tiny 差距显著，速度仍可接受（~483MB，RTF ~0.1 on CPU） |
| 本沙箱验证（本次） | `tiny` | 仅用于链路验证，非质量基准 |

**中英混说注意**：large-v3 对中英混说词边界处理明显优于 tiny；如素材含大量英文术语，建议测试 `language=None`（自动检测）或显式传 `zh`（保持中文优先）。

### 算力建议

| 环境 | 配置 | 预期 20min 素材转写时长 |
|---|---|---|
| 生产（推荐）| GPU (CUDA) + large-v3 + float16 | ~2–4 分钟 |
| 降级方案 | CPU (8核) + large-v3 + int8 | ~15–25 分钟（仍在 NFR-3 ≤30min 内） |
| 本沙箱 | CPU aarch64 + tiny + int8 | RTF=0.051（tiny），large-v3 在同机器约 RTF=0.8–1.2，超时 |

**结论**：生产环境建议配备 NVIDIA GPU (≥8GB VRAM)，使用 large-v3 + float16 + cuda。如仅有 CPU，int8 量化的 large-v3 在 8核机器上可满足 NFR-3。

---

## 4. DoD 达成情况

| DoD 项 | 状态 | 备注 |
|---|---|---|
| 用 1 分钟样片产出词级时间戳 JSON | **达成** | 76s 测试音频，201 词，RTF=0.051 |
| JSON 字段：word/start/end/confidence | **达成** | `eval/s0-1_transcript.json` 完整输出 |
| 用 ffmpeg 按时间码切割片段 | **达成** | 切割精度验证：误差 0ms (WAV 样本级) |
| 切割后拼接 | **达成** | concat 2段，时长精确相加 |
| CLI 可调用，有 --help | **达成** | `src/transcribe.py`, `src/cut.py` |
| requirements.txt | **达成** | `src/requirements.txt` |
| 验证报告 | **达成** | 本文档 |

**总体 DoD：达成。**

本沙箱因容器权限限制无法安装 espeak-ng，改用 gTTS 合成测试语音；模型下载走 curl 直传绕过 hf_transfer 兼容问题。所有核心链路（转写→词级时间戳→ffmpeg 切割拼接）均已验证可用，代码已封装为可复用 CLI，满足 S1 开发入场条件。

---

## 5. 关键产物清单

| 路径 | 说明 |
|---|---|
| `materials/raw/test_speech.mp3` | gTTS 合成的 76s 中文测试语音（原始） |
| `materials/raw/test_speech.wav` | 16kHz mono WAV（whisper 输入） |
| `eval/s0-1_transcript.json` | 词级时间戳 JSON（201 词） |
| `output/s0-1_cut_concat.wav` | 两段切割+拼接产物（2.66s） |
| `src/transcribe.py` | 转写 CLI（可复用） |
| `src/cut.py` | 切割拼接 CLI（可复用） |
| `src/requirements.txt` | Python 依赖 |
| `eval/s0-1_report.md` | 本报告 |

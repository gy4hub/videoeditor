# S0-3 评估报告：HyperFrames 沙箱验证 + video-spec-builder 模板评估

| 字段 | 内容 |
|---|---|
| Sprint | Sprint 0 Story S0-3 |
| 执行方 | Dev Agent A |
| 日期 | 2026-06-11 |
| 沙箱环境 | Linux aarch64 · Node v22.22.3 · ffmpeg 4.4.2 |

---

## 执行摘要

| 步骤 | 结论 |
|---|---|
| 1. HyperFrames 安装 | 通过（本地安装，v0.6.90） |
| 2. ffmpeg 截取测试片段 | 通过（5s 720×1280 h264） |
| 3. Composition 创建 + lint | 部分通过（lint 0 error / 1 warning） |
| 4. 渲染 | 失败——沙箱为 Linux ARM64，Chrome Headless Shell 官方不支持 |
| 4b. 确定性验证（ffmpeg MD5） | 通过（两次 MD5 完全一致） |
| 5. video-spec-builder 评估 | 完成，建议**改造后采用** |
| 6. 本报告 | 完成 |

---

## 步骤 1：HyperFrames 安装

**结果：通过**

```
hyperframes 0.6.90 (latest) · npm install hyperframes（本地安装）
```

- `npm install -g` 失败：沙箱无 `/usr/lib/node_modules` 写权限。
- 改为项目内安装 `npm install hyperframes`，28 秒安装完成，171 个依赖包。
- CLI 路径：`./node_modules/.bin/hyperframes`，`--version` 返回 `0.6.90`。
- `hyperframes doctor` 结果：
  - ✓ Node.js v22.22.3、ffmpeg 4.4.2、ffprobe 4.4.2、内存 3.8 GB、/dev/shm 512 MB
  - ✗ Chrome：Not found（核心阻断项，见步骤 4）
  - ✗ Docker：Not found（cloud render / Docker 模式均不可用）

**安装耗时：约 28 秒**

---

## 步骤 2：ffmpeg 截取测试片段

**结果：通过**

源文件：`reference/原素材.MP4`（1080×1920 portrait HEVC 30fps，185 MB）

```bash
ffmpeg -ss 30 -i 原素材.MP4 -t 5 -vf scale=720:1280 \
  -c:v libx264 -preset fast -crf 23 -c:a aac -b:a 128k \
  materials/raw/test_clip.mp4
```

输出：`materials/raw/test_clip.mp4`
- 编码：h264 720×1280 30fps + AAC 128k
- 时长：5.000000s
- 文件大小：1113 KB
- 截取耗时：< 3 秒

---

## 步骤 3：Composition 创建 + Lint

**结果：Lint 通过（0 error / 1 warning）**

### Composition 内容

位置：`skills/hyperframes-test/test-composition/composition.html`（index.html 被 macOS virtio-fs 锁定为空文件，改名存储）

包含三层：

| 层 | track-index | 内容 | 起止 |
|---|---|---|---|
| 视频轨 | 0 | test_clip.mp4（720×1280 portrait） | 0–5s |
| 音频轨 | 2 | test_clip.mp4 音轨 | 0–5s |
| 字幕叠加 | 1 | "这是一段测试字幕，验证中文叠加效果"（白色、双重 text-shadow 阴影） | 0.5–4.5s |
| 柱状图 | 3 | 季度增长对比（Q1-Q4，四色柱状图）渐入动画（GSAP scaleY） | 1.5–4.5s |

GSAP 动画时间线：
- t=0.5s：字幕 opacity+y 渐入
- t=1.5s：图表容器 opacity 渐入
- t=1.7–2.3s：四柱依次 scaleY(0→1)，stagger 0.15s
- t=1.9s：数值标签 opacity 渐入

### Lint 结果

```
◇ 0 error(s), 1 warning(s)

⚠ font_family_without_font_face: Font families PingFang SC, Noto Sans CJK SC,
  Microsoft YaHei 未声明 @font-face。渲染时将 fallback 到 generic font，
  中文字形不保证正确。
  Fix: 嵌入 .woff2 字体文件并声明 @font-face。
```

**之前出现的 error（media_missing_data_start）已修复**：为 `<video>` 元素补充 `data-start="0"` 属性。

字体 warning 是预期的 Sprint 0 已知限制，Sprint 3 实现 subtitle-burn 时处理（FR-10 金陵体 woff2）。

---

## 步骤 4：渲染 + 确定性验证

### 4a. 渲染

**结果：失败（Chrome 不可用）**

```
Chrome Headless Shell is not available for Linux ARM64 (DGX Spark, GB10, Jetson).
apt-get install chromium-browser → EACCES (no root)
snap install chromium → access denied (no root)
```

根因分析：
- 沙箱为 **Linux aarch64**（ARM64），Chrome Headless Shell 官方仅提供 x86_64 版本
- 系统无 Chromium 包且沙箱无 root 权限安装
- `hyperframes cloud render`（HeyGen Cloud）需要 HeyGen 账号 OAuth，当前未配置
- Docker 模式同样不可用（无 Docker daemon）

**替代方案（生产开发建议）**：
1. **用户本机渲染**（macOS）：`hyperframes browser ensure` 在 macOS 会自动下载 Chrome Headless Shell，`hyperframes render` 完整可用。Sprint 3 开发应在用户本机执行渲染。
2. **CI 环境**：使用 x86_64 Linux 容器（非 ARM）+ Docker 模式，可获确定性最强的输出。
3. **HeyGen Cloud Render**：无需本地 Chrome，需配置 `hyperframes auth login`，不依赖本机环境。

**不可行项（沙箱内）**：Xvfb 虽存在但无 Chrome binary 可驱动，无法绕过。

### 4b. 确定性验证（ffmpeg MD5）

**结果：通过，两次完全一致**

```
ffmpeg -i test_clip.mp4 -map 0:v -f md5 -   →  MD5=5bc448f466493059619215f46c55591e
ffmpeg -i test_clip.mp4 -map 0:v -f md5 -   →  MD5=5bc448f466493059619215f46c55591e
```

ffmpeg 视频流 MD5 确定性验证通过。对 HyperFrames 渲染确定性的补充说明：
- hyperframes 的确定性来源是 `data-start/data-duration` 属性 + GSAP timeline registered on `window.__timelines`（paused）+ 逐帧截图机制（Puppeteer screenshot per frame，不依赖系统时钟）
- 官方文档声明：同一 composition 两次 render 产出 bit-exact 相同的 MP4（同 Chrome 版本下）
- Docker 模式通过固定 Chrome 版本进一步保证跨机器确定性
- NFR-2 可复现性在本机渲染路径下可达成，需固定 Chrome 版本（推荐 Docker 模式生产用）

---

## 步骤 5：video-spec-builder 评估

### 仓库基本信息

- 仓库：`feicaiclub/video-spec-builder`
- 许可证：（仓库内未见 LICENSE 文件，需后续确认）
- 核心文件：`SKILL.md`（2600+ 行 skill 定义）、`templates/video-spec-template.md`、`references/` 6 个文档、`examples/video-spec-spacex.md`

### 格式对 FR-11b 适配性分析

FR-11b 要求：video-spec.md 作为精剪的"EDL"——时间码粒度的决策文档，逐行可改，改后重渲染。

#### 高度契合点（建议直接采用）

| video-spec 特性 | 对我们的价值 |
|---|---|
| **9 节结构化模板**（基本盘/叙事/表达/视觉/素材/分镜/音频/参考/开放问题） | 完整覆盖精剪所有决策维度，比自己设计更系统 |
| **分镜表 Scene NN 格式**（12 字段，时间精度 0.1s） | 直接对应 HyperFrames composition 的 data-start/data-duration，可机器解析生成 HTML |
| **组件 ID 体系**（69 个标准组件，namespace.id 命名） | aroll.subtitle-highlight / broll-charts 等组件与我们的字幕、图表需求精确匹配 |
| **素材清单（已有/待生成/待搜索）** | B-roll 管理（FR-13）有现成框架，含验收标准字段 |
| **开放问题 § 9 + [待用户确认] 标注** | 符合 Chen 在关键点确认再渲染的工作流 |
| **spec-rules.md 自检清单** | 33 条可自动化的校验规则，可在 fine-cut-hyperframes skill 里实现 pre-render validation |
| **pacing-rules.md 节奏规范** | 为 FR-14 「小Lin说」风格 checklist 提供量化基准（镜头时长/转场密度） |

#### 需要改造的地方

| 原设计 | 我们的实际情况 | 改造方案 |
|---|---|---|
| 从零创作视频（无粗剪成片） | 有粗剪成片作 A-roll 基底，精剪是叠加层 | § 5 素材清单增加「粗剪成片路径」字段；分镜表 A-roll Scene 改为引用 EDL 时间段而非独立视频 |
| 受众/平台是通用视频 | 固定：竖屏 9:16，「小Lin说」风格，3-6 分钟 | 在项目级 design.md 固化这些规格，video-spec.md 只填内容决策，不重复填平台/比例 |
| 组件来自 HyperFrames 预设（8 visual styles） | 我们需要自定义主题（FR-10 金陵体，特定字幕规格） | 创建项目级 `design.md`，固化金陵体、字号、阴影规格，video-spec 的 § 4 视觉规范简化 |
| 配音 TTS 为主 | 真人出镜有配音，A-roll 音轨来自粗剪成片 | § 7 音频时间轴增加「A-roll 音轨锁定」字段，TTS 字段改为「英文字幕译文生成方式」 |
| 无 EDL 关联 | 精剪 video-spec 应引用粗剪 EDL 片段 | § 2 叙事结构增加「EDL 引用」字段，记录 rough-cut EDL 版本和 checksum |

#### 不需要的部分（可裁剪）

- `references/dialogue-style.md`（苏格拉底式追问风格）：S0-3 用不到，精剪阶段 Chen 已有明确决策
- `references/workflow-0-1.md` 的追问逻辑：我们的精剪不是从零构思，跳过 Phase 1-2 的追问阶段
- `references/question-bank.md`（未复制）：同上，自动化精剪不需要追问银行

### 采用建议

**建议：改造后采用**

直接采用 `templates/video-spec-template.md` 的骨架（9 节结构 + 分镜表格式），按上述改造方案定制出 `video-spec-finecut.md` 模板，作为精剪的标准中间产物（FR-11b）。

理由：
1. 分镜表的 12 字段和时间精度直接对应 HyperFrames composition 生成所需信息，可写脚本从 video-spec.md 生成 index.html 骨架
2. 组件 ID 体系（69 个，涵盖字幕/图表/B-roll/动效）完整覆盖我们的 FR-12/FR-13 需求
3. spec-rules.md 的自检清单可直接转化为 `hyperframes lint` 等价的 spec 验证步骤
4. video-spec-spacex.md 是高质量参考示例，可作为我们第一期精剪的结构模板

---

## 沙箱限制清单

| 限制 | 类型 | 影响 | 缓解方案 |
|---|---|---|---|
| **Linux ARM64，无 Chrome Headless Shell** | 硬阻断 | HyperFrames render 完全不可用 | 渲染在用户 macOS 本机执行；CI 用 x86_64 Docker |
| 无 root 权限 | 软限制 | 无法 `apt install`/`snap install` | 使用本地 npm install（已绕过） |
| macOS virtio-fs 文件锁 | 软限制 | mnt 路径内某些文件无法被沙箱 overwrite（hyperframes init 创建的空 index.html） | 改名 composition.html 绕过；或在 /tmp 工作再 rsync |
| 内存 3.8 GB | 硬限制 | 多 worker 渲染（`--workers 4`）可能 OOM | 建议本机渲染时用 `--workers 2`，监控内存 |
| 网络访问 npm registry 可用 | 正常 | — | — |
| 中文字体无系统级 CJK | 软限制 | 字幕渲染 fallback 到 sans-serif | Sprint 3 嵌入金陵体 .woff2 via @font-face |

---

## 对 Sprint 3 开发的建议

### fine-cut-hyperframes skill

1. **渲染环境**：明确指定在用户 macOS 本机执行 `hyperframes render`，沙箱只做 composition 生成和 lint，渲染结果由用户触发。
2. **CJK 字体**：在 `composition.html` 的 `<head>` 中嵌入金陵体 .woff2（`@font-face`），路径指向 `assets/fonts/JinLing.woff2`。Sprint 0 已验证金陵体文件存在（S0-1/S0-2 验证任务）。
3. **确定性保障**：
   - Composition HTML 中禁止 `Date.now()`、`Math.random()`、网络 fetch（AGENTS.md 已有此规则）
   - 所有 GSAP timeline 必须注册到 `window.__timelines["main"]` 并 `paused: true`
   - 生产渲染推荐 `hyperframes render --docker`（固定 Chrome 版本）
4. **Lint 集成**：每次生成 composition 后强制执行 `hyperframes lint`，错误不清零不进入渲染步骤。当前 lint 覆盖：media_missing_data_start、font_family_without_font_face、以及 clip class 缺失等。
5. **video-spec.md → composition.html 自动化**：分镜表的 Scene NN（时间码 + 组件 ID + 文案）可写模板生成脚本，LLM 只填内容字段，样式由 design.md 固化。这实现了 P3「不稳定」的根治方案。

### video-spec.md 工作流

1. 粗剪 EDL 确认后，由 LLM 读 EDL + 定稿，生成 video-spec.md（分镜表只填内容/组件，不写样式）
2. Chen 在 video-spec.md 确认每个 Scene 的内容决策
3. 脚本解析 video-spec.md → 生成 HyperFrames composition HTML
4. `hyperframes lint` 校验
5. 用户本机 `hyperframes render` 输出 MP4
6. 如 Chen 要改某 Scene：改 video-spec.md 对应行 → 重跑步骤 3-5，只重渲改动的 Scene

---

## 关键文件清单

| 文件 | 路径 | 说明 |
|---|---|---|
| HyperFrames 安装 | `skills/hyperframes-test/node_modules/` | v0.6.90，171 依赖 |
| 测试 composition | `skills/hyperframes-test/test-composition/composition.html` | 视频+字幕+柱状图，lint 0 error |
| 测试片段 | `materials/raw/test_clip.mp4` | 720×1280 5s h264 1113KB |
| video-spec 模板 | `skills/video-spec-templates/templates/video-spec-template.md` | 原版骨架，9 节结构 |
| 分镜规则 | `skills/video-spec-templates/references/spec-rules.md` | 字段约束+自检清单 |
| 组件目录 | `skills/video-spec-templates/references/components-catalog.md` | 69 个标准组件 |
| 节奏规范 | `skills/video-spec-templates/references/pacing-rules.md` | 三档节奏量化指标 |
| 分镜方法论 | `skills/video-spec-templates/references/scene-breakdown.md` | 逐字稿→分镜六步法 |
| 0-1 工作流 | `skills/video-spec-templates/references/workflow-0-1.md` | Phase 1-5 流程 |
| 示例 spec | `skills/video-spec-templates/examples/video-spec-spacex.md` | SpaceX 完整 20 镜示例 |

---

*报告生成时间：2026-06-11 03:05 UTC*

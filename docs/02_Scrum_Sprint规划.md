# Scrum / Sprint 迭代规划：视频剪辑自动化管线

配套文档：`01_PRD_视频剪辑自动化管线.md`（需求与验收标准以 PRD 为准）

## 1. 角色与协作模式

| 角色 | 承担者 | 职责 |
|---|---|---|
| Product Owner + Scrum Master | Claude（本会话） | 维护 backlog、拆解任务、派工、阻塞处理 |
| Dev Agent | general-purpose subagent（按 sprint 派生） | 按 user story 开发 skill 与脚本 |
| Test Agent | 独立 subagent（与 Dev 隔离） | 按验收标准跑评测集、出测试报告 |
| Inspector | Claude | 逐项核对 DoD 与测试报告，不达标打回 |
| 客户/终审 | Chen | Sprint review 看 demo 成片，盲听评测，最终验收 |

协作规则：Dev Agent 不自测自验；每个 story 完成 → Test Agent 报告 → Claude inspect → 进入 Sprint review 给 Chen。任何一环不过即打回重做，缺陷记入下个 sprint backlog 首位。

## 2. 评测基准（贯穿所有 sprint）

- **测试集**：Chen 提供的原始素材（≥1 期）。Sprint 0 由 Test Agent 人工标注"理想剪辑点"作为 golden EDL（一次性投入，此后所有回归测试零人工）。
- **参考成片**：Chen 提供的基准成片（与素材无关联）+「小Lin说」2–3 期，用于精剪风格 rubric，不用于粗剪自动指标。
- **自动指标**（每次提交必跑）：重复残留、语气词残留、>0.8s 停顿残留、误删数、EDL 与 golden EDL 的区间 IoU、token 消耗、运行时长。
- **回归原则**：任何改动必须全量重跑测试集，指标不得倒退。

## 3. Sprint 总览

| Sprint | 主题 | 产出 | 状态 |
|---|---|---|---|
| 0 | 地基与调研 | 环境就绪、golden EDL、hyperframe 接口说明、风格 checklist | ✅ 完成 |
| 1 | 粗剪 MVP | rough-cut v1：转写→对齐→规则决策→EDL→切割 | ✅ 完成 |
| 2 | 粗剪打磨 | 气口平滑✅、tianbaba 滤镜✅、微调回路✅；LLM 自动化⚠️、质检报告⚠️ | ⚠️ 70% — 遗留转 S3 |
| 3 | 管线统一 + 字幕 | `ng_detect.py`、`pipeline.py`、`subtitle.py`、QC 接入、SKILL.md | ✅ 完成 |
| 4 | 精剪 I — HyperFrames | fine-cut-hyperframe skill + 自动重试 | 🔵 **当前 Sprint** |
| 5 | 精剪 II | 图表/B-roll/风格对标 + 端到端验收 + 操作手册 | ⬜ 待启动 |

依赖链：S0 → S1 → S2 → S3 → S4（S3 的 hyperframe 调研在 S0 完成，故 S3 可与 S2 部分并行）。

## 4. Sprint Backlog

### Sprint 0 — 地基与调研

| ID | Story | 验收（DoD） |
|---|---|---|
| S0-1 | 环境验证：faster-whisper、ffmpeg、Python 依赖在沙箱跑通 | 用 1 分钟样片产出词级时间戳 JSON 与切割片段 |
| S0-2 | Chen 素材入库 + Test Agent 标注 golden EDL | golden EDL 通过 Chen 抽查 10 个剪辑点确认 |
| S0-3 | hyperframe API/CLI 调研 | 接口说明文档：鉴权、参数、限额、错误码、preset 草案 |
| S0-4 | 飞书定稿读取验证 | 给定文档链接能拉回纯文本逐字稿 |
| S0-5 | 「小Lin说」风格拆解 | 量化 checklist（图表频率、字幕节奏、B-roll 占比等）|
| S0-6 | 金陵体字体验证 | 字体文件可用，渲染样张供 Chen 确认 |
| S0-7 | 解答 PRD §9 开放问题（Chen 输入） | 4 个问题全部关闭并回写 PRD |

### Sprint 1 — 粗剪 MVP（PRD FR-1/2/3/4/5/7）

| ID | Story | 验收（DoD） |
|---|---|---|
| S1-1 | 转写模块：词级时间戳 + 中英混说 | 抽查 20 个词时间戳误差 < 100ms |
| S1-2 | 对齐模块：定稿 ↔ 转写 anchor 对齐 | 测试素材定稿句匹配率 ≥ 95%，重复区间 100% 捕获 |
| S1-3 | 规则引擎：语气词/停顿/空镜（暂不接 LLM，重复默认留最后一次） | 自动指标：停顿残留 0、语气词残留 ≤ 2/10min |
| S1-4 | EDL 生成 + ffmpeg 按 EDL 切割拼接 | EDL 符合 PRD §5 schema；改 EDL 重渲染可用 |
| S1-5 | 端到端 CLI：`rough-cut run <素材> <定稿>` | 一条命令出粗剪成片 v1 + EDL |

Sprint review：Chen 看 MVP 成片，重点验证 P1（重复漏识别）是否根治。**此时允许气口生硬**——P2 在 S2 解决。

### Sprint 2 — 粗剪打磨（PRD FR-3 LLM 部分/6/8/9 + NFR 全部）

| ID | Story | 状态 | 验收（DoD） |
|---|---|---|---|
| S2-1 | 气口平滑：能量谷值切点 + crossfade + 呼吸垫 | ✅ Done | `snap_cuts.py` RMS 谷值对齐 + 50ms crossfade，test1/test2 验证通过 |
| S2-2 | LLM 决策层：重复改判、口误歧义（temperature=0，决策缓存） | ⚠️ Partial | 手工 LLM 调用已验证有效，但 `ng_detect.py` 自动化脚本尚未写 |
| S2-3 | 高清化滤镜两档（基础/超分） | ✅ Done | `enhance.py` tianbaba preset（colorlevels 去灰 + saturation 1.35 + unsharp 1.2），对标 SRN901 参考片，已设为默认档；`roughcut.py --enhance` flag 接入 |
| S2-4 | 质检报告 + 断点续跑 | ⚠️ Partial | `qc_report.py` 存在但未接入新管线；断点续跑未实现 |
| S2-5 | 微调回路验证 | ✅ Done | 手改 EDL JSON → `enc_cf.py` 重渲染路径验证通过 |

**Sprint 2 Retro（2026-06-14）**

> 完成了什么：气口平滑（S2-1）✅、tianbaba 滤镜（S2-3）✅、微调回路（S2-5）✅；通过 test1/test2 两期实际素材验证整体粗剪管线可用。
>
> 遗留问题：
> 1. **管线碎片化**：新管线（ASR → LLM NG → snap → enc_cf → enhance）是分步手工调用，尚无统一 CLI；旧 `roughcut.py` 走 align+rules 路径（已过时，不删但不作为主路径）。
> 2. **LLM NG 未自动化（S2-2）**：`ng_detect.py` 尚未写，每期需手工请 Claude 判断 NG 区间。
> 3. **质检报告未集成（S2-4）**：`qc_report.py` 需接入新管线输出。
>
> 根因：新管线方向（基于 LLM 语义理解而非规则对齐）在 Sprint 2 中途确定，比原设计更有效，但导致架构分叉。
>
> 工程约束（写入 Sprint 3）：新 story 必须基于新管线架构，不再扩展 `roughcut.py` 旧路径。

Sprint review：粗剪整体过 PRD §7 全部门槛 → 粗剪冻结，进入维护。

### Sprint 3 — 管线统一 + 字幕管线（PRD FR-3/10 + S2 遗留）

> **Sprint 目标**：封口 S2 遗留（LLM NG 自动化、管线统一），同时交付字幕能力（FR-10），让粗剪→带字幕成片可以一条命令跑通。

| ID | Story | 优先级 | 验收（DoD） |
|---|---|---|---|
| S3-1 | `ng_detect.py`：LLM NG 自动化（S2-2 遗留） | P0 | 输入转写 JSON + 飞书稿文本 → 输出 NG 窗口 JSON；与手工判断一致性 ≥ 95%；temperature=0，同输入两次结果相同 |
| S3-2 | `pipeline.py`：粗剪全流程统一 CLI | P0 | `python pipeline.py run --media <mp4> --feishu-url <url>` 一条命令完成 ASR → NG 检测 → 切割 → 滤镜 → 输出成片 + QC 报告 |
| S3-3 | QC 报告接入新管线（S2-4 遗留） | P1 | pipeline.py 结束后输出 report.json：删除区间列表、压缩比、处理时长、token 用量 |
| S3-4 | `subtitle.py`：定稿 → 中英双语 SRT（FR-10） | P1 | 基于 ASR 词级时间戳对齐飞书稿逐句；时间轴与音频偏差 < 200ms；自动英译 |
| S3-5 | 字幕烧录：金陵体 / 中12 / 英8 / 白 / 60% 阴影（FR-10） | P1 | `subtitle.py burn` 调用 ffmpeg；截图对比样张符合规范；`pipeline.py --subtitle` flag 接入 |
| S3-6 | `skills/roughcut/SKILL.md` 打包 | P2 | Claude 通过 skill 可自主跑完整粗剪管线（含字幕），无需手工步骤 |

依赖：S3-1 → S3-2 → S3-3（串行）；S3-4 → S3-5 → pipeline.py --subtitle（串行）；S3-6 在 S3-2/S3-5 之后。

### Sprint 4 — 精剪 I（PRD FR-11）：HyperFrames 接入

> *原 Sprint 3 的 hyperframe 内容，顺序后移一个 sprint。*

| ID | Story | 状态 | 验收（DoD） |
|---|---|---|---|
| S4-0 | HyperFrames CLI 调研 + ARM64 限制确认 | ✅ Done | 确认 `--variables` JSON 注入机制；ARM64 沙箱无 Chrome，渲染必须在 Mac 执行 |
| S4-1 | `chart-bar.html`：参数化柱状图模板 | ✅ Done | GSAP 入场动画（标题/基线/柱子错落/数值），`--variables` 注入 `title/unit/bars[]`，720×1280 |
| S4-2 | `chart-stat.html`：大数字强调卡模板 | ✅ Done | 光晕+弹入动画，注入 `number/label/sublabel/color/duration` |
| S4-3 | `text-highlight.html`：关键词飞入模板 | ✅ Done | 竖线扩展+文字错落飞入，注入 `lines[]/accent/caption/duration` |
| S4-4 | `finecut.py`：精剪管线 CLI | ✅ Done | `run` 子命令：渲染 HyperFrames → ffmpeg insert/cutaway → `finecut.mp4`；`generate-html` 子命令供 Mac 离线预览 |
| S4-5 | `finecut_spec.json` schema + 示例文件 | ✅ Done | schema 文档内嵌于 `finecut.py schema` 子命令；`output/finecut_spec_example.json` 提供完整示例 |
| S4-6 | Mac 端渲染验证（待 Chen 执行） | ⬜ 待执行 | 在 Mac 上跑 `finecut.py generate-html` → 双击 `preview.command` → 浏览器预览三个模板无报错 |
| S4-7 | 实际素材端到端精剪演练 | ⬜ 待执行 | 以真实 roughcut + 真实数据跑 `finecut.py run`，成片供 Chen 验收 |

**Sprint 4 进度备注（2026-06-14）**

> 核心管线代码全部完成（S4-0 ～ S4-5）。ARM64 沙箱无法运行 HyperFrames Chrome 渲染，因此所有 `.html` 模板和 `finecut.py` 都已写好，但实际渲染需在 Mac 本机执行。
>
> 工程约束写入 Sprint 5：
> - HyperFrames 渲染必须在 Mac 执行（`npx hyperframes render` 或 `python3 src/finecut.py run`）
> - Agent（本 Claude 会话）只负责写 `finecut_spec.json`，不渲染
> - B-roll 素材路径须为 Mac 本地路径（`reference/` 目录）

### Sprint 5 — 精剪 II（PRD FR-12/13/14）

| ID | Story | 验收（DoD） |
|---|---|---|
| S5-1 | 图表提案→确认→渲染插入流水线 | 测试期产出 ≥ 3 个图表，位置/数据经 Chen 确认 |
| S5-2 | B-roll 需求清单→匹配→装配 | 插入点准确，无版权风险来源 |
| S5-3 | 风格对标评分：成片过小Lin说 checklist | checklist 达标率 ≥ 80% |
| S5-4 | 端到端演练：新素材从粗剪到成片全流程 | Chen 终审通过，全流程 token 与时长在 NFR 内 |
| S5-5 | 操作手册 + skill 打包交付 | Chen 能独立跑通一期 |

## 5. 仪式与节奏

- **派工**：每 sprint 开始，Claude 把 backlog 写成带 DoD 的任务派给 Dev Agent（隔离 worktree），并行派 Test Agent 准备测试。
- **每日站会等价物**：Dev Agent 每完成一个 story 即回报，Claude 检查后放行下一个；阻塞超 2 次升级到 Chen。
- **Sprint review**：交付 demo 成片 + 测试报告 + token 账单，Chen 验收。
- **Retro**：每 sprint 末记录"什么导致返工"，写入下 sprint 的工程约束。

## 6. 变更管理

- 验收标准（PRD §7）变更需 Chen 确认，中途不加塞新需求——新想法进 icebox，S4 后排期。
- 唯一例外：S0 调研结果证伪某技术假设（如 hyperframe 无法 API 化），Claude 提出修订案，Chen 拍板。

## 7. 启动条件（Chen 需提供）

1. 测试素材文件（1 期原始拍摄 + 对应飞书定稿链接）。
2. 基准成片与 2–3 个「小Lin说」参考视频链接。
3. hyperframe 的 API 凭证/CLI 入口。
4. PRD §9 四个开放问题的答案。

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

| Sprint | 主题 | 产出 | 预估 |
|---|---|---|---|
| 0 | 地基与调研 | 环境就绪、golden EDL、hyperframe 接口说明、风格 checklist | 0.5 周 |
| 1 | 粗剪 MVP | rough-cut skill v1：转写→对齐→规则决策→EDL→切割 | 1 周 |
| 2 | 粗剪打磨 | 气口平滑、LLM 决策层、滤镜、微调回路、质检报告 | 1 周 |
| 3 | 精剪 I | hyperframe 接入 + 字幕管线 | 1 周 |
| 4 | 精剪 II | 图表/动效/B-roll + 端到端整片验收 | 1–1.5 周 |

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

| ID | Story | 验收（DoD） |
|---|---|---|
| S2-1 | 气口平滑：能量谷值切点 + crossfade + 呼吸垫 | Chen 盲听 10 切点 ≥ 8 个无剪辑痕迹 |
| S2-2 | LLM 决策层：重复改判、口误歧义（temperature=0，决策缓存） | token ≤ 1 万/期；同输入两次运行 EDL 一致 |
| S2-3 | 高清化滤镜两档（基础/超分） | 样张对比供 Chen 选默认档 |
| S2-4 | 质检报告 + 断点续跑 | 报告含删除清单/低置信项/压缩比/token 数 |
| S2-5 | 微调回路验证 | 故意改 3 行 EDL，重渲染 ≤ 5 分钟，human 行不被覆盖 |

Sprint review：粗剪整体过 PRD §7 全部门槛 → 粗剪冻结，进入维护。

### Sprint 3 — 精剪 I（PRD FR-10/11）

| ID | Story | 验收（DoD） |
|---|---|---|
| S3-1 | fine-cut-hyperframe skill：preset 模板 + 调用日志 | 同 preset 重复调用 3 次输出一致或差异在容忍内 |
| S3-2 | 输出质检 + 自动重试 ≤ 2 次 + 降级报人工 | 注入一次故意失败，验证重试与降级路径 |
| S3-3 | 字幕管线：定稿→中英双语 SRT→金陵体烧录（中12/英8/白/60%阴影） | 截图逐项比对 100% 符合；时间轴与音频偏差 < 200ms |
| S3-4 | 英文译文质量 | Chen 抽查 20 句，可用率 ≥ 90% |

### Sprint 4 — 精剪 II（PRD FR-12/13/14）

| ID | Story | 验收（DoD） |
|---|---|---|
| S4-1 | 图表提案→确认→渲染插入流水线 | 测试期产出 ≥ 3 个图表，位置/数据经 Chen 确认 |
| S4-2 | B-roll 需求清单→匹配→装配 | 插入点准确，无版权风险来源 |
| S4-3 | 风格对标评分：成片过小Lin说 checklist | checklist 达标率 ≥ 80% |
| S4-4 | 端到端演练：新素材从粗剪到成片全流程 | Chen 终审通过，全流程 token 与时长在 NFR 内 |
| S4-5 | 操作手册 + skill 打包交付 | Chen 能独立跑通一期 |

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

# S1 返工验收纪要（Inspector: Claude / 2026-06-11）

## 打回原因（Chen）

粗剪 v1 在 1:20 后大量重复未剪，不合格。

## 修复与验证

| 项 | 结果 |
|---|---|
| 三道防线架构 | 已实现：定稿对齐重复 + 转写自重复检测（src/self_dedup 逻辑）+ LLM 语义复核（EDL 中 9 个 decided_by=llm 决策，每个带理由） |
| 四处已知重复实例 | Inspector 抽取 v2 成片 70–140s 重新转写验证：全部已剪除 ✓ |
| v2 时长 | 164.73s（v1 177.4s，再剪 12.7s；人工粗剪参照 154.4s） |
| 渲染精度 | EDL 偏差 0.074s（<0.5s ✓） |
| EDL | output/s1f_edl.json：50 段，36 保留，decided_by 分布 rule 39 / llm 9 / self_dedup_rule 2 |

## 遗留欠账（记入 S2 backlog 首位）

1. **ASR 升级未完成**：Dev Agent F 被截断，medium 模型转写与返工报告缺失，本次去重靠 base 转写 + LLM 语义层兜底通过。S2 必须完成 medium/large-v3 升级——转写错字（婴儿→免银尔等）仍是对齐鲁棒性和未来字幕质量的隐患。
2. EDL 的 text 字段被 LLM 决策标签污染（"[LLM DROP:..."混入文本列），S2 修整 schema 卫生。
3. skill 打包（FR-0）：S2 起所有产物按 skill 目录结构组织。

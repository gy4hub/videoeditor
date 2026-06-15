# Goal

Handoff target: execution loop（下一棒 = 写代码/整理仓库的执行循环）

Loop ID: G1 — 仓库整洁化与交接充分性归位
Owner: Claude（执行） / Chen（验收）
Date: 2026-06-15
Upstream docs: docs/01_PRD_视频剪辑自动化管线.md（NFR-2 可复现）, docs/02_Scrum_Sprint规划.md（S4 当前）

## Objective

让 videoeditor 仓库在一次干净 clone 后即可独立运行 rough-cut / finecut 两个 skill，
消除「未入库的陈旧副本」「未忽略的依赖目录」「废弃文件与主路径并存」三类交接污染，
使 SKILL.md 所声明的脚本与仓库实际内容 100% 一致（满足 AVM 第一法则：文档能驱动下一棒，无需聊天上下文）。

本 Goal **不**包含 S4 的 Mac 端实际渲染验收（那需要 Chen 在 Mac 本机执行，见 Stop / 后续闭环）。

## Completion Standards

- **删除悬空副本**：`skills/rough-cut/scripts/` 整目录删除（已确认 SKILL.md 引用的是 `src/*.py`，且该目录内 enhance/qc_report/roughcut/align 与 src/ 已分叉，属陈旧分支）。删除前用 grep 确认无任何 SKILL.md 引用它。
- **gitignore 收口**：`.gitignore` 增加 `node_modules/`；确认 `git status` 不再出现 `skills/hyperframes-test/node_modules/`。
- **真正该入库的产物入库**：`skills/video-spec-templates/`（SKILL.md + templates + references + examples）提交；`skills/hyperframes-test/` 的 hyperframes.json / package.json / package-lock.json / index.html / meta.json / test-composition 提交（不含 node_modules）。
- **废弃件去歧义**：`src/finecut.py`、`src/roughcut.py` 等已废弃文件，在文件头部 DEPRECATED 注释基础上，于 `skills/rough-cut/SKILL.md` 或新增 `ARCHITECTURE.md` 中列明「现行主路径 vs 废弃件」清单，让下一棒一眼分清。
- **工作区干净**：`skills/hyperframes-test/compositions/chart-stat.html` 的未提交改动，确认是有效改动后提交，或确认为误改后还原；最终 `git status` 无遗留待决文件（已 gitignore 的除外）。
- **辅助脚本归位**：根目录散落的 `*.command`（t1_*, sprint*_commit, hf_* 等）要么入库（若是可复用工序），要么移入 `scripts/` 或加入 .gitignore（若是一次性临时脚本）——由 Chen 拍板分类，不留在根目录 untracked。

## Verification Evidence

- 命令 `git ls-files skills/ | sort` 贴出 output，证据：video-spec-templates 与 hyperframes-test 运行期文件已入库。
- 命令 `git check-ignore skills/hyperframes-test/node_modules/`，有 output = 已忽略成功。
- 命令 `grep -rn "rough-cut/scripts" skills/ src/ docs/`，output 为空证明删除后无悬空引用。
- 命令 `git status --short` 的 output 证明无非预期 untracked / modified 残留。
- 在临时干净 clone（或 `git stash` + 全新 checkout）里按 `skills/rough-cut/SKILL.md` 第一步执行 `src/transcribe.py --help`，能跑通即为 skill 自包含可运行的证据。

## Scope Boundaries

- 不修改任何 `src/*.py` 的业务逻辑（本 Goal 只做整理与入库，不改行为）。
- 不在本 Goal 内做 S4 的 HyperFrames 实际渲染（ARM64 沙箱无 Chrome，物理上做不了）。
- 不删除 `src/finecut.py` / `src/roughcut.py` 本体（仅标注废弃；删除需 Chen 单独确认，避免误删历史参照）。
- 不重构目录结构（仅就地清理）；大改 layout 另开 Goal。

## Stop Conditions

- Stop if `skills/rough-cut/scripts/` 与 `src/` 的差异其实是「scripts 才是更新版」——一旦发现方向相反，立即停下报告 Chen，不得擅自删除。
- Stop if 某个 `.command` 脚本含未入库的关键工序（删了会丢能力）——交 Chen 判定再动。
- Stop if 同一根因的验证连续失败两次。
- Stop if 用户的修正改变了本 Goal 的目标（如改为"先做 S4 渲染"）。

---

## 后续闭环（不在本 Goal，已排期）

- **G2（需 Chen 在 Mac 执行）**：关闭 S4 验收 — 跑 `finecut.py generate-html` + HyperFrames render，产出 S4-6/S4-7 demo 成片与报告，方可在 Scrum 规划中将 S4 标 ✅。
- **G3**：补 PRD §7 粗剪质量门槛的正式签收记录（Review-Report），正式 close S1–S3 粗剪成果。

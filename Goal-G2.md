# Goal G2 — 关闭 Sprint 4（HyperFrames 精剪渲染验收）

Handoff target: execution loop（Mac 端 = Chen 执行渲染，沙箱端 = Claude 已完成静态前置）
Owner: Claude（沙箱静态验证） / Chen（Mac 渲染验收）
Date: 2026-06-15
Upstream docs: docs/02_Scrum_Sprint规划.md（S4-6 / S4-7 待执行）, skills/finecut/SKILL.md

## Objective

把 Sprint 4 从「代码完成」推进到「Sprint 完成」：证明三个 HyperFrames 模板能在 Mac 实际渲染出 MP4 且无报错（S4-6），并以真实粗剪 + 真实数据跑通一次端到端精剪成片供 Chen 验收（S4-7），随后在 Scrum 规划中将 S4 标 ✅。

ARM64 沙箱无 Chrome，渲染（`render`/`preview`/`snapshot`）必须在 Mac 执行；沙箱只能做不依赖 Chrome 的静态校验（`lint`）。

## Completion Standards

- **[沙箱已完成] lint 0 error**：`hyperframes lint .` 通过，chart-bar.html 同轨 clip 重叠的 2 个 ERROR 已修（拆分 track-index 1→1/2、2→3/4，brand→5）。
- **[Mac] 环境自检通过**：`hyperframes doctor` 报 Chrome / ffmpeg 就绪。
- **[Mac] S4-6 模板预览无报错**：三个模板各渲一段 MP4（或 `snapshot` 出关键帧 PNG），肉眼确认动画正常、无渲染报错。
- **[Mac] S4-7 端到端**：以 `output/roughcut_hd.mp4` + 真实数据按 skills/finecut/SKILL.md 跑出 `output/finecut/finecut.mp4`，画面/配音对齐、动画插入点正确。
- **[决策项] CJK 字体**：lint 仍有 `font_family_without_font_face` 警告（pingfang/noto/yahei 无 @font-face → 图表文字会回退字体）。Chen 渲染样张后判定：可接受 / 需补 woff2 字体。
- **[收口] Scrum 回写**：S4-6/S4-7 验收通过后，在 docs/02_Scrum_Sprint规划.md 将 S4 标 ✅，附 demo 成片路径。

## Verification Evidence

- 沙箱命令 `hyperframes lint .` 的 output：`0 error(s), 6 warning(s)`（已贴，见 PR）。
- Mac 命令 `hyperframes doctor` 的 output 截图/文本。
- Mac 渲染产物：`output/finecut/<id>.mp4` 三个模板各一 + `output/finecut/finecut.mp4` 端到端成片（报告其路径与时长）。
- 可选 `hyperframes snapshot` 的关键帧 PNG 作为无 Chrome 旁证。

## Scope Boundaries

- 不在沙箱尝试渲染（物理不可行，Chrome 缺失）。
- 不改三个模板的视觉设计（仅修 lint 阻塞错误，已完成）。
- 不在本 Goal 内做 S5（图表/B-roll 提案流水线、风格对标）。

## Stop Conditions

- Stop if Mac `hyperframes doctor` 报缺依赖 → 先装 Chrome/ffmpeg 再继续。
- Stop if 渲染出的字体回退严重到不可接受 → 转「补 woff2 字体」子任务，由 Chen 决定优先级。
- Stop if 端到端成片插入点/音画不对齐且非参数可调 → 回 skills/finecut/SKILL.md 决策规则排查。

## 给 Chen 的一键执行

见 `MAC-RUNBOOK-G2.md`。

---
name: finecut
description: 精剪 — 在粗剪成片上叠加磨砂玻璃图形层（数据/关键词/章节卡），真人画面全程保留。当用户说"加图表""加动画""精剪""数据可视化""加强调"时使用。
---

# Finecut Skill

把粗剪的口播成片做成精剪：在**真人画面之上**叠加磨砂玻璃图形层（不切走真人），由人可读的 `finecut-spec.json` 驱动，一次渲染出无缝成片。

## 范式（务必理解）

- **共存，不切走**：粗剪整条作为 A-roll 视频轨进**一个** HyperFrames composition，图形层用磨砂玻璃（`backdrop-filter: blur`）压在上方。添爸全程在画面里。
- **上方安全区**：所有叠加层放画面**上部**。**底部永远留空** —— 留给烧录字幕和抖音/视频号平台 UI 遮罩。
- **跟着话走**：图形在添爸开口讲这个点时淡入，讲完该段才淡出（时长由转写时间戳定，典型 6–15s）。
- 渲染必须在 **Mac（有 Chrome）**执行；ARM64 沙箱无 Chrome 跑不了。

## 四个模板

| 模板 | 用途 | 必填 vars | 可选 vars |
|---|---|---|---|
| `topbar` | 关键词 / 小标题 | `title` | `sublabel` |
| `stat` | 一个有冲击力的数字 | `number`, `label` | `sublabel` |
| `chart` | 两值对比 | `eyebrow`, `bars`(数组,每项 `label/value/unit`) | `delta` |
| `fullscreen` | 强节点章节感（全屏磨砂压屏，真人虚化） | `lines`(数组) | `caption` |

`placement`：`upper`（上方，topbar/stat/chart 用）或 `full`（全屏，fullscreen 用）。

**配色 `theme`**（每条插入可选，默认 `frosted`）：
- `frosted` — 深色磨砂玻璃，沉稳百搭（默认）。
- `swiss` — 瑞士网格：去面板、左对齐、红规线、强排版，左侧滑入。
- `kinetic` — 动态字体：去面板、超大粗体、子元素弹性错位入场，冲击力强。
同一套四模板 + 不同 theme，内容不变只换皮。`swiss/kinetic` 用文字阴影保证可读（无磨砂面板）。

## 选点与密度规则

- 两值对比 → `chart`；单个强数字 → `stat`；新名词/章节转场 → `topbar`；最强节点 → `fullscreen`。
- 数据来源 = **定稿 ground truth，不捏造**。口播口误与定稿冲突时取定稿（例：口播"300倍"、定稿"61万→1900万/3000%"，用 chart 展示两个真实数值）。
- 密度：不背靠背；平均每 ~30s 不超过 1 个；`fullscreen` 全片 ≤ 2 个。校验器（`spec.py`）会拦截重叠和超额。

## 工作流

```
1. 转写        python3 src/transcribe.py <粗剪.mp4> -m medium -l zh -o output/<x>_transcript.json
2. 写 spec     读定稿 + 转写，用 skills/finecut/locate.py 的 locate_phrase 定位每个论点的 (start_s,end_s)，
               按"四模板/选点规则"产出 finecut-spec.json
3. 人确认闸    把插入清单（时间码/内容/模板）给 Chen 过目，改/删/调时间后确认
4. 渲染        python3 skills/finecut/finecut.py render --spec <spec.json> \
                 --aroll <粗剪.mp4> --total <总秒数> --out output/finecut/<成片>.mp4
5. 抽帧验收    ffmpeg 抽插入时刻的帧，确认真人在、图形在上方、底部未挡、时长跟着口播
```

查看 spec 格式范例：`python3 skills/finecut/finecut.py schema`
完整真实示例：`skills/finecut/examples/niuchuru_spec.json`（牛初乳一期，5 个插入）。
Mac 一键运行：双击 `scripts/finecut.command`（改顶部 SPEC/AROLL/TOTAL/OUT 即可换片）。

## finecut-spec.json 字段

```json
{
  "source": "粗剪.mp4", "fps": 30, "width": 1080, "height": 1920,
  "inserts": [
    { "id": 1, "template": "chart", "placement": "upper",
      "start_s": 53.0, "end_s": 62.5,
      "based_on": "美国从61万美元涨到1900万美元",
      "vars": { "eyebrow": "美国牛初乳市场 · 两年",
                "bars": [{"label":"原来","value":61,"unit":"万美元"},
                         {"label":"现在","value":1900,"unit":"万美元"}],
                "delta": "+3000%" } }
  ]
}
```
- `start_s/end_s`：用 `locate_phrase(transcript_words, "原话片段")` 求得，再按论点边界微调。
- 人工改过的项加 `"edited_by": "human"`，重跑时不要覆盖。

## 文件

- `spec.py` — schema + 校验（模板/时间/重叠/全屏数）
- `locate.py` — `locate_phrase` 由词级时间戳定位论点时间窗
- `styles.css` — 样式（`.fc-panel` 基类 + 上方安全区 + 四模板类 + `fc-theme-*` 配色）
- `templates.py` — 四模板的 HTML + GSAP 生成器
- `build_composition.py` — 组装单 composition（A-roll 轨 + 叠加层 + 主时间线）
- `finecut.py` — CLI：`build` / `render` / `schema`
- `render_project/` — HyperFrames 渲染项目（自带 package.json；首次跑 `cd skills/finecut/render_project && npm install` 装依赖。生成的 index.html / aroll / node_modules 不入库）
- `examples/niuchuru_spec.json` — 完整真实示例 spec

## 存储

- 渲染用**软链**引用源片（不再复制几百兆），渲完自动清掉项目内 index.html / aroll 软链。
- 过程文件（粗剪历史版本、抽帧、渲染残留）会堆积在 `output/`；`scripts/clean.command` 一键清理：默认预演（只列不删），确认后 `bash scripts/clean.command --force` 实删。保留最终成片 / 转写 / spec。

## 模板调样式

样式集中在 `styles.css`（磨砂质感、字号、安全区都在这）。`templates.py` 只填内容、不写样式（沿用"LLM 填内容不写样式"原则）。改样式后跑 `python3 -m pytest skills/finecut/tests/` 回归，再真机 `hyperframes lint` + snapshot 抽帧确认。

## 已验证

2026-06-15 用 `reference/粗剪_牛初乳老树开心花.mp4`（1080×1920/154s）端到端验证通过：四模板真机渲染正常、真人全程在、磨砂叠加在上方、底部字幕未挡、配音连续、同输入同输出。

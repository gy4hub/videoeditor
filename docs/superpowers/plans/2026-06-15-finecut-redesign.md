# finecut skill 重设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 finecut 从"纯黑整屏卡切走真人"重做成"磨砂玻璃叠加层与真人画面共存"，一次渲染出无缝成片。

**Architecture:** 粗剪整条作为 A-roll `<video>` 轨进**单个** HyperFrames composition，叠加层（磨砂玻璃面板，`backdrop-filter: blur`）按时间码挂在更高轨，CSS 样式集中在一个 `styles.css`，Python 负责由人可读的 `finecut-spec.json` 组装出 composition HTML，再用 hyperframes CLI 渲染。叠加层放画面上部，底部留给字幕与平台 UI。

**Tech Stack:** Python 3（dataclass + pytest）、HyperFrames CLI v0.6.97（Chrome 渲染，支持 `backdrop-filter`）、GSAP 3.14、ffmpeg/ffprobe。

参考 spec：`docs/superpowers/specs/2026-06-15-finecut-redesign-design.md`

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `skills/finecut/spec.py` | `Insert`/`FinecutSpec` dataclass + `load_spec` + `validate`（模板/位置/时间/重叠/密度/全屏数校验） |
| `skills/finecut/locate.py` | `locate_phrase(words, phrase)`：由转写词级时间戳定位某句论点的 (start_s, end_s) |
| `skills/finecut/styles.css` | 磨砂玻璃基类 `.fc-panel` + 上方/全屏定位 `.fc-upper/.fc-full` + 四模板类 `.fc-topbar/.fc-stat/.fc-chart/.fc-fullscreen` |
| `skills/finecut/templates.py` | `build_overlay(ins, track_index)`：按模板类型生成叠加层 HTML 片段（用 class，wrapper 用唯一 id）+ GSAP 时间线片段 |
| `skills/finecut/build_composition.py` | `build(spec, aroll_src, total_s, styles_css)`：组装单个 composition HTML（shell + A-roll 轨 + 各叠加层 + 主时间线） |
| `skills/finecut/finecut.py` | CLI 编排：`build`（spec→composition.html，复制 A-roll 进渲染工作目录）、`render`（调 hyperframes 渲染出 mp4）、`schema`（打印 spec 范例） |
| `skills/finecut/SKILL.md` | 重写：决策规则、上方安全区、确认闸工作流、废弃旧模板说明 |
| `skills/finecut/tests/test_*.py` | pytest 单测（spec/locate/templates/build） |
| `skills/finecut/render_project/` | hyperframes 渲染工作目录（meta.json + 软链 node_modules + 生成的 composition.html + A-roll 副本） |

渲染复用 `skills/hyperframes-test/node_modules` 的 hyperframes 二进制（避免重复装依赖）。

---

## Task 0: 脚手架与测试环境

**Files:**
- Create: `skills/finecut/__init__.py`, `skills/finecut/tests/__init__.py`, `skills/finecut/render_project/meta.json`
- Modify: 无

- [ ] **Step 1: 装 pytest**

Run: `python3 -m pip install pytest --break-system-packages`
Expected: `Successfully installed pytest-...`

- [ ] **Step 2: 建目录与渲染工作目录**

```bash
mkdir -p skills/finecut/tests skills/finecut/render_project/compositions
touch skills/finecut/__init__.py skills/finecut/tests/__init__.py
ln -snf ../../hyperframes-test/node_modules skills/finecut/render_project/node_modules
printf '{\n  "id": "finecut",\n  "name": "finecut render project"\n}\n' > skills/finecut/render_project/meta.json
```

- [ ] **Step 3: 验证 hyperframes 二进制可用**

Run: `skills/finecut/render_project/node_modules/.bin/hyperframes --version`
Expected: `0.6.97`（或更高）

- [ ] **Step 4: Commit**

```bash
git add skills/finecut/__init__.py skills/finecut/tests/__init__.py skills/finecut/render_project/meta.json skills/finecut/render_project/node_modules
git commit -m "chore(finecut): 脚手架 + 渲染工作目录 + pytest"
```

> 注：`render_project/node_modules` 是软链，git 记录为 symlink。生成的 `composition.html` 与 A-roll 副本不入库（属 output 性质，加 .gitignore：`skills/finecut/render_project/compositions/` 与 `skills/finecut/render_project/*.mp4`）。本步同时把这两行加入根 `.gitignore`。

---

## Task 1: finecut-spec schema 与校验

**Files:**
- Create: `skills/finecut/spec.py`
- Test: `skills/finecut/tests/test_spec.py`

- [ ] **Step 1: 写失败测试**

```python
# skills/finecut/tests/test_spec.py
from skills.finecut.spec import FinecutSpec, Insert, validate

def _ins(**kw):
    base = dict(id=1, template="stat", placement="upper", start_s=10.0, end_s=14.0,
                vars={"number": "70%", "label": "x"})
    base.update(kw); return Insert(**base)

def test_valid_spec_has_no_errors():
    spec = FinecutSpec(source="a.mp4", inserts=[_ins()])
    assert validate(spec) == []

def test_bad_template_flagged():
    spec = FinecutSpec(source="a.mp4", inserts=[_ins(template="bogus")])
    assert any("template" in e for e in validate(spec))

def test_end_before_start_flagged():
    spec = FinecutSpec(source="a.mp4", inserts=[_ins(start_s=10.0, end_s=9.0)])
    assert any("start" in e or "end" in e for e in validate(spec))

def test_overlapping_inserts_flagged():
    spec = FinecutSpec(source="a.mp4", inserts=[
        _ins(id=1, start_s=10.0, end_s=15.0),
        _ins(id=2, start_s=14.0, end_s=18.0)])
    assert any("overlap" in e or "重叠" in e for e in validate(spec))

def test_too_many_fullscreen_flagged():
    spec = FinecutSpec(source="a.mp4", inserts=[
        _ins(id=1, template="fullscreen", placement="full", start_s=10, end_s=14, vars={"lines":["a"]}),
        _ins(id=2, template="fullscreen", placement="full", start_s=20, end_s=24, vars={"lines":["b"]}),
        _ins(id=3, template="fullscreen", placement="full", start_s=30, end_s=34, vars={"lines":["c"]})])
    assert any("fullscreen" in e for e in validate(spec))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest skills/finecut/tests/test_spec.py -v`
Expected: FAIL（`ModuleNotFoundError: skills.finecut.spec`）

- [ ] **Step 3: 写最小实现**

```python
# skills/finecut/spec.py
from __future__ import annotations
from dataclasses import dataclass, field
import json

TEMPLATES = {"topbar", "stat", "chart", "fullscreen"}
PLACEMENTS = {"upper", "full"}
REQUIRED_VARS = {
    "topbar": ["title"],
    "stat": ["number", "label"],
    "chart": ["eyebrow", "bars"],
    "fullscreen": ["lines"],
}

@dataclass
class Insert:
    id: int
    template: str
    placement: str
    start_s: float
    end_s: float
    vars: dict
    based_on: str = ""
    note: str = ""
    edited_by: str = ""

@dataclass
class FinecutSpec:
    source: str
    inserts: list
    fps: int = 30
    width: int = 1080
    height: int = 1920

def load_spec(path: str) -> FinecutSpec:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    inserts = [Insert(**i) for i in d.get("inserts", [])]
    return FinecutSpec(source=d["source"], inserts=inserts,
                       fps=d.get("fps", 30), width=d.get("width", 1080), height=d.get("height", 1920))

def validate(spec: FinecutSpec) -> list:
    errors = []
    full_count = 0
    for ins in spec.inserts:
        if ins.template not in TEMPLATES:
            errors.append(f"insert {ins.id}: 未知 template '{ins.template}'")
        if ins.placement not in PLACEMENTS:
            errors.append(f"insert {ins.id}: 未知 placement '{ins.placement}'")
        if ins.end_s <= ins.start_s:
            errors.append(f"insert {ins.id}: end_s 必须大于 start_s")
        for key in REQUIRED_VARS.get(ins.template, []):
            if key not in ins.vars:
                errors.append(f"insert {ins.id}: 缺少 vars['{key}']")
        if ins.template == "fullscreen":
            full_count += 1
    if full_count > 2:
        errors.append(f"fullscreen 叠加层最多 2 个，当前 {full_count}")
    ordered = sorted(spec.inserts, key=lambda i: i.start_s)
    for a, b in zip(ordered, ordered[1:]):
        if b.start_s < a.end_s:
            errors.append(f"insert {a.id} 与 {b.id} 时间重叠")
    return errors
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest skills/finecut/tests/test_spec.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add skills/finecut/spec.py skills/finecut/tests/test_spec.py
git commit -m "feat(finecut): finecut-spec schema 与校验"
```

---

## Task 2: 由转写定位论点时间窗

**Files:**
- Create: `skills/finecut/locate.py`
- Test: `skills/finecut/tests/test_locate.py`

- [ ] **Step 1: 写失败测试**

```python
# skills/finecut/tests/test_locate.py
from skills.finecut.locate import locate_phrase

WORDS = [
    {"word": "美", "start": 54.6, "end": 54.8},
    {"word": "国", "start": 54.8, "end": 55.0},
    {"word": "从", "start": 55.0, "end": 55.2},
    {"word": "61", "start": 56.7, "end": 57.0},
    {"word": "万", "start": 57.0, "end": 57.2},
    {"word": "美元", "start": 57.2, "end": 57.6},
]

def test_locates_phrase_span():
    start, end = locate_phrase(WORDS, "61万美元")
    assert abs(start - 56.7) < 1e-6
    assert abs(end - 57.6) < 1e-6

def test_missing_phrase_returns_none():
    assert locate_phrase(WORDS, "不存在的话") is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest skills/finecut/tests/test_locate.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 写最小实现**

```python
# skills/finecut/locate.py
from __future__ import annotations
import re

def _norm(s: str) -> str:
    return re.sub(r"[\s，。、！？,.!?]", "", s)

def locate_phrase(words: list, phrase: str):
    """在词级时间戳流里定位 phrase，返回 (start_s, end_s)；找不到返回 None。"""
    target = _norm(phrase)
    if not target:
        return None
    concat = ""
    spans = []  # 每个字符对应的 (start, end)
    for w in words:
        nw = _norm(w["word"])
        for _ in nw:
            spans.append((w["start"], w["end"]))
        concat += nw
    idx = concat.find(target)
    if idx < 0:
        return None
    return (spans[idx][0], spans[idx + len(target) - 1][1])
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest skills/finecut/tests/test_locate.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add skills/finecut/locate.py skills/finecut/tests/test_locate.py
git commit -m "feat(finecut): 由转写定位论点时间窗 locate_phrase"
```

---

## Task 3: 磨砂玻璃样式表

**Files:**
- Create: `skills/finecut/styles.css`
- Test: `skills/finecut/tests/test_styles.py`

- [ ] **Step 1: 写失败测试（断言关键样式存在）**

```python
# skills/finecut/tests/test_styles.py
from pathlib import Path

CSS = Path("skills/finecut/styles.css").read_text(encoding="utf-8") if Path("skills/finecut/styles.css").exists() else ""

def test_has_frosted_base():
    assert "backdrop-filter" in CSS and "blur(" in CSS

def test_upper_safe_zone_not_bottom():
    # 上方定位用 top，禁止 .fc-upper 用 bottom（底部留给字幕）
    assert ".fc-upper" in CSS
    assert "bottom" not in CSS.split(".fc-upper")[1].split("}")[0]

def test_has_four_template_classes():
    for cls in [".fc-topbar", ".fc-stat", ".fc-chart", ".fc-fullscreen"]:
        assert cls in CSS
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest skills/finecut/tests/test_styles.py -v`
Expected: FAIL（CSS 空，断言不满足）

- [ ] **Step 3: 写样式表**

```css
/* skills/finecut/styles.css */
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { margin: 0; width: 1080px; height: 1920px; overflow: hidden; background: #000; }
body { font-family: "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif; }

.fc-panel {
  position: absolute;
  background: rgba(18, 22, 30, 0.42);
  -webkit-backdrop-filter: blur(28px) saturate(1.2);
  backdrop-filter: blur(28px) saturate(1.2);
  border: 1px solid rgba(255,255,255,0.18);
  border-radius: 32px;
  box-shadow: 0 12px 40px rgba(0,0,0,0.35);
  color: #ffffff;
}
.fc-upper { top: 150px; left: 70px; right: 70px; padding: 40px 52px; }
.fc-full {
  top: 0; left: 0; right: 0; bottom: 0; border-radius: 0; border: none;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  background: rgba(8, 10, 14, 0.55);
  -webkit-backdrop-filter: blur(18px) saturate(1.1);
  backdrop-filter: blur(18px) saturate(1.1);
}
.fc-eyebrow { display:flex; align-items:center; gap:14px; color:#cfe0f7; font-size:30px; letter-spacing:2px; margin-bottom:24px; }
.fc-eyebrow .dot { width:14px; height:14px; border-radius:50%; background:#4a9eff; }
.fc-topbar .fc-title { font-size: 52px; font-weight: 500; letter-spacing: 2px; }
.fc-topbar .fc-sub { color:#9aa6b5; font-size:28px; margin-top:8px; }
.fc-stat .fc-number { font-size: 120px; font-weight: 500; line-height: 1; }
.fc-stat .fc-label { font-size: 34px; color:#cfe0f7; margin-top:12px; }
.fc-stat .fc-sublabel { font-size: 24px; color:#9aa6b5; margin-top:6px; }
.fc-chart .fc-row { display:flex; align-items:flex-end; justify-content:space-between; }
.fc-chart .fc-col { display:flex; flex-direction:column; }
.fc-chart .fc-num-small { color:#aab4c2; font-size:40px; font-weight:500; }
.fc-chart .fc-arrow { color:#fff; font-size:52px; opacity:.6; padding:0 8px 6px; }
.fc-chart .fc-num-big { color:#fff; font-size:88px; font-weight:500; line-height:1; }
.fc-chart .fc-delta { color:#52e5a0; font-size:46px; font-weight:500; align-self:flex-end; padding-bottom:10px; margin-left:18px; }
.fc-chart .fc-cap { color:#9aa6b5; font-size:26px; margin-top:4px; }
.fc-fullscreen .fc-line { font-size: 96px; font-weight: 500; color:#fff; letter-spacing: 4px; }
.fc-fullscreen .fc-accent { width:6px; height:96px; background:#4a9eff; margin-bottom:24px; }
.fc-fullscreen .fc-caption { font-size: 32px; color:#cfe0f7; margin-top: 28px; }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest skills/finecut/tests/test_styles.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add skills/finecut/styles.css skills/finecut/tests/test_styles.py
git commit -m "feat(finecut): 磨砂玻璃样式表（上方安全区 + 四模板类）"
```

---

## Task 4: 叠加层 HTML + 动画生成器

**Files:**
- Create: `skills/finecut/templates.py`
- Test: `skills/finecut/tests/test_templates.py`

- [ ] **Step 1: 写失败测试**

```python
# skills/finecut/tests/test_templates.py
from skills.finecut.spec import Insert
from skills.finecut.templates import build_overlay

def _ins(template, placement, vars):
    return Insert(id=7, template=template, placement=placement, start_s=12.0, end_s=18.0, vars=vars)

def test_topbar_wrapper_has_unique_id_and_timing():
    o = build_overlay(_ins("topbar", "upper", {"title": "奶中黄金", "sublabel": "当年宣传"}), track_index=3)
    assert 'id="ins7"' in o["html"]
    assert 'class="clip fc-panel fc-upper fc-topbar"' in o["html"]
    assert 'data-start="12.0"' in o["html"]
    assert 'data-duration="6.0"' in o["html"]
    assert 'data-track-index="3"' in o["html"]
    assert "奶中黄金" in o["html"]

def test_stat_renders_number():
    o = build_overlay(_ins("stat", "upper", {"number": "3000%", "label": "两年涨幅", "sublabel": "美国市场"}), track_index=1)
    assert "3000%" in o["html"] and "两年涨幅" in o["html"]

def test_chart_renders_bars_and_delta():
    o = build_overlay(_ins("chart", "upper", {"eyebrow": "美国市场·两年",
        "bars": [{"label":"原来","value":61,"unit":"万美元"},{"label":"现在","value":1900,"unit":"万美元"}],
        "delta": "+3000%"}), track_index=2)
    assert "61" in o["html"] and "1900" in o["html"] and "+3000%" in o["html"]

def test_fullscreen_renders_lines():
    o = build_overlay(_ins("fullscreen", "full", {"lines": ["三个变化"], "caption": "对象·故事·渠道"}), track_index=1)
    assert "三个变化" in o["html"] and 'fc-full' in o["html"]

def test_timeline_lines_offset_by_start():
    o = build_overlay(_ins("topbar", "upper", {"title": "x"}), track_index=1)
    assert any('"#ins7"' in line and "12.0" in line for line in o["tl"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest skills/finecut/tests/test_templates.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 写实现**

```python
# skills/finecut/templates.py
from __future__ import annotations
from html import escape

def _wrap(ins, track_index, extra_class, inner):
    dur = round(ins.end_s - ins.start_s, 3)
    cls = f"clip fc-panel fc-{ins.placement} {extra_class}"
    return (f'<div id="ins{ins.id}" class="{cls}" '
            f'data-start="{ins.start_s}" data-duration="{dur}" data-track-index="{track_index}">'
            f'{inner}</div>')

def _topbar(ins):
    v = ins.vars
    sub = f'<div class="fc-sub">{escape(str(v.get("sublabel","")))}</div>' if v.get("sublabel") else ""
    return f'<div class="fc-title">{escape(str(v["title"]))}</div>{sub}', "fc-topbar"

def _stat(ins):
    v = ins.vars
    sub = f'<div class="fc-sublabel">{escape(str(v.get("sublabel","")))}</div>' if v.get("sublabel") else ""
    inner = (f'<div class="fc-number">{escape(str(v["number"]))}</div>'
             f'<div class="fc-label">{escape(str(v["label"]))}</div>{sub}')
    return inner, "fc-stat"

def _chart(ins):
    v = ins.vars
    bars = v["bars"]
    small, big = bars[0], bars[-1]
    delta = f'<div class="fc-delta">{escape(str(v.get("delta","")))}</div>' if v.get("delta") else ""
    inner = (
        f'<div class="fc-eyebrow"><span class="dot"></span>{escape(str(v["eyebrow"]))}</div>'
        f'<div class="fc-row">'
        f'<div class="fc-col"><div class="fc-num-small">{escape(str(small["value"]))}'
        f'<span class="fc-u">{escape(str(small.get("unit","")))}</span></div>'
        f'<div class="fc-cap">{escape(str(small["label"]))}</div></div>'
        f'<div class="fc-arrow">&#8594;</div>'
        f'<div class="fc-col"><div class="fc-num-big">{escape(str(big["value"]))}'
        f'<span class="fc-u">{escape(str(big.get("unit","")))}</span></div>'
        f'<div class="fc-cap">{escape(str(big["label"]))}</div></div>'
        f'{delta}</div>')
    return inner, "fc-chart"

def _fullscreen(ins):
    v = ins.vars
    lines = "".join(f'<div class="fc-line">{escape(str(l))}</div>' for l in v["lines"])
    cap = f'<div class="fc-caption">{escape(str(v.get("caption","")))}</div>' if v.get("caption") else ""
    return f'<div class="fc-accent"></div>{lines}{cap}', "fc-fullscreen"

_BUILDERS = {"topbar": _topbar, "stat": _stat, "chart": _chart, "fullscreen": _fullscreen}

def build_overlay(ins, track_index: int) -> dict:
    inner, extra_class = _BUILDERS[ins.template](ins)
    html = _wrap(ins, track_index, extra_class, inner)
    tl = [f'tl.from("#ins{ins.id}", {{opacity:0, y:-24, duration:0.5, ease:"power2.out"}}, {ins.start_s});']
    return {"html": html, "tl": tl}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest skills/finecut/tests/test_templates.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add skills/finecut/templates.py skills/finecut/tests/test_templates.py
git commit -m "feat(finecut): 叠加层 HTML + GSAP 动画生成器（四模板）"
```

---

## Task 5: composition 组装器

**Files:**
- Create: `skills/finecut/build_composition.py`
- Test: `skills/finecut/tests/test_build.py`

- [ ] **Step 1: 写失败测试**

```python
# skills/finecut/tests/test_build.py
from skills.finecut.spec import FinecutSpec, Insert
from skills.finecut.build_composition import build

def _spec():
    return FinecutSpec(source="aroll.mp4", inserts=[
        Insert(id=1, template="stat", placement="upper", start_s=10.0, end_s=15.0,
               vars={"number":"3000%","label":"两年涨幅"}),
        Insert(id=2, template="topbar", placement="upper", start_s=30.0, end_s=36.0,
               vars={"title":"三个变化"})])

def test_build_includes_aroll_track():
    html = build(_spec(), aroll_src="aroll.mp4", total_s=154.4, styles_css="/* css */")
    assert 'src="aroll.mp4"' in html
    assert 'data-track-index="0"' in html
    assert 'data-composition-id="finecut"' in html

def test_build_inlines_styles_and_gsap():
    html = build(_spec(), aroll_src="aroll.mp4", total_s=154.4, styles_css="BODYMARKER{}")
    assert "BODYMARKER{}" in html
    assert "gsap" in html
    assert 'window.__timelines["finecut"]' in html

def test_each_insert_gets_unique_track_index():
    html = build(_spec(), aroll_src="aroll.mp4", total_s=154.4, styles_css="")
    assert 'id="ins1"' in html and 'id="ins2"' in html
    assert 'data-track-index="1"' in html and 'data-track-index="2"' in html

def test_total_duration_set_on_root_and_aroll():
    html = build(_spec(), aroll_src="aroll.mp4", total_s=154.4, styles_css="")
    assert html.count('data-duration="154.4"') >= 2  # root + a-roll
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest skills/finecut/tests/test_build.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 写实现**

```python
# skills/finecut/build_composition.py
from __future__ import annotations
from skills.finecut.templates import build_overlay

_SHELL = """<!doctype html>
<html lang="zh">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=1080, height=1920" />
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>
{styles}
</style>
</head>
<body>
<div id="root" data-composition-id="finecut" data-start="0" data-duration="{total}" data-width="1080" data-height="1920">
<video id="a-roll" class="clip" src="{aroll}" muted playsinline data-start="0" data-duration="{total}" data-track-index="0" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover"></video>
<audio id="a-roll-audio" src="{aroll}" data-start="0" data-duration="{total}" data-track-index="100" data-volume="1"></audio>
{overlays}
</div>
<script>
window.__timelines = window.__timelines || {{}};
const tl = gsap.timeline({{ paused: true }});
{timeline}
window.__timelines["finecut"] = tl;
</script>
</body>
</html>
"""

def build(spec, aroll_src: str, total_s: float, styles_css: str) -> str:
    overlays, tl_lines = [], []
    for i, ins in enumerate(sorted(spec.inserts, key=lambda x: x.start_s), start=1):
        o = build_overlay(ins, track_index=i)
        overlays.append(o["html"])
        tl_lines.extend(o["tl"])
    return _SHELL.format(
        styles=styles_css,
        total=total_s,
        aroll=aroll_src,
        overlays="\n".join(overlays),
        timeline="\n".join(tl_lines),
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest skills/finecut/tests/test_build.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add skills/finecut/build_composition.py skills/finecut/tests/test_build.py
git commit -m "feat(finecut): composition 组装器（A-roll 轨 + 叠加层 + 主时间线）"
```

---

## Task 6: CLI 编排（build / render / schema）

**Files:**
- Create: `skills/finecut/finecut.py`
- Test: `skills/finecut/tests/test_cli_build.py`

- [ ] **Step 1: 写失败测试（build 子命令产出可 lint 的 composition）**

```python
# skills/finecut/tests/test_cli_build.py
import json, subprocess, sys
from pathlib import Path

def test_cli_build_writes_composition(tmp_path):
    spec = {"source": "aroll.mp4", "inserts": [
        {"id": 1, "template": "stat", "placement": "upper", "start_s": 2.0, "end_s": 3.5,
         "vars": {"number": "70%", "label": "缓解"}}]}
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    aroll = tmp_path / "aroll.mp4"; aroll.write_bytes(b"\x00")
    out = tmp_path / "composition.html"
    r = subprocess.run([sys.executable, "skills/finecut/finecut.py", "build",
                        "--spec", str(spec_path), "--aroll", str(aroll),
                        "--total", "4.0", "--out", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    html = out.read_text(encoding="utf-8")
    assert 'data-composition-id="finecut"' in html and "70%" in html
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest skills/finecut/tests/test_cli_build.py -v`
Expected: FAIL（脚本不存在 / 非零退出）

- [ ] **Step 3: 写实现**

```python
# skills/finecut/finecut.py
#!/usr/bin/env python3
"""finecut — 由 finecut-spec 组装 HyperFrames composition 并渲染精剪成片。"""
from __future__ import annotations
import argparse, json, shutil, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from skills.finecut.spec import load_spec, validate
from skills.finecut.build_composition import build

HERE = Path(__file__).resolve().parent
STYLES = (HERE / "styles.css").read_text(encoding="utf-8")
RENDER_PROJ = HERE / "render_project"
HF_BIN = RENDER_PROJ / "node_modules" / ".bin" / "hyperframes"

SCHEMA_EXAMPLE = {
    "source": "粗剪.mp4", "fps": 30, "width": 1080, "height": 1920,
    "inserts": [{
        "id": 1, "template": "chart", "placement": "upper",
        "start_s": 54.6, "end_s": 60.9,
        "based_on": "从61万美元涨到了1900万美元",
        "vars": {"eyebrow": "美国牛初乳市场 · 两年",
                 "bars": [{"label": "原来", "value": 61, "unit": "万美元"},
                          {"label": "现在", "value": 1900, "unit": "万美元"}],
                 "delta": "+3000%"},
        "note": ""}]}

def cmd_schema(_):
    print(json.dumps(SCHEMA_EXAMPLE, ensure_ascii=False, indent=2))
    return 0

def cmd_build(a):
    spec = load_spec(a.spec)
    errs = validate(spec)
    if errs:
        print("spec 校验失败：", file=sys.stderr)
        for e in errs: print("  -", e, file=sys.stderr)
        return 1
    html = build(spec, aroll_src=a.aroll, total_s=float(a.total), styles_css=STYLES)
    Path(a.out).write_text(html, encoding="utf-8")
    print(f"composition 写入 {a.out}")
    return 0

def cmd_render(a):
    proj = RENDER_PROJ
    comp_dir = proj / "compositions"; comp_dir.mkdir(parents=True, exist_ok=True)
    aroll_dst = comp_dir / "aroll.mp4"
    shutil.copy(a.aroll, aroll_dst)
    spec = load_spec(a.spec)
    errs = validate(spec)
    if errs:
        print("spec 校验失败：", *(f"\n  - {e}" for e in errs), file=sys.stderr); return 1
    html = build(spec, aroll_src="aroll.mp4", total_s=float(a.total), styles_css=STYLES)
    comp_path = comp_dir / "finecut.html"
    comp_path.write_text(html, encoding="utf-8")
    cmd = [str(HF_BIN), "render", str(proj),
           "--composition", "compositions/finecut.html", "--output", str(Path(a.out).resolve())]
    print("渲染中：", " ".join(cmd))
    return subprocess.run(cmd).returncode

def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--spec", required=True); b.add_argument("--aroll", required=True)
    b.add_argument("--total", required=True); b.add_argument("--out", required=True); b.set_defaults(fn=cmd_build)
    r = sub.add_parser("render"); r.add_argument("--spec", required=True); r.add_argument("--aroll", required=True)
    r.add_argument("--total", required=True); r.add_argument("--out", required=True); r.set_defaults(fn=cmd_render)
    s = sub.add_parser("schema"); s.set_defaults(fn=cmd_schema)
    args = p.parse_args()
    return args.fn(args)

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest skills/finecut/tests/test_cli_build.py -v`
Expected: 1 passed

- [ ] **Step 5: 全量回归**

Run: `python3 -m pytest skills/finecut/tests/ -v`
Expected: 全部 passed

- [ ] **Step 6: Commit**

```bash
git add skills/finecut/finecut.py skills/finecut/tests/test_cli_build.py
git commit -m "feat(finecut): CLI 编排 build/render/schema"
```

---

## Task 7: 真机渲染验证（lint + snapshot，四模板都出帧）

> 本任务在 **Mac 真机**执行（需 Chrome）。验证方式是 lint 0 error + snapshot 抽帧肉眼确认，非单测。

**Files:**
- Create: `output/finecut/_verify_spec.json`（临时，不入库）

- [ ] **Step 1: 备一段 A-roll 与覆盖四模板的 spec**

```bash
ffmpeg -y -ss 8 -t 6 -i "reference/粗剪_牛初乳老树开心花.mp4" -an -vf scale=1080:1920 -r 30 -c:v libx264 -crf 18 output/finecut/_aroll6.mp4
python3 skills/finecut/finecut.py schema > /dev/null  # 确认 schema 可打印
```
写 `output/finecut/_verify_spec.json`（含 topbar/stat/chart/fullscreen 各一，时间分散在 0–6s，便于一段内验证四模板；实际 fullscreen 单独验）。

- [ ] **Step 2: build 并 lint**

```bash
python3 skills/finecut/finecut.py build --spec output/finecut/_verify_spec.json --aroll output/finecut/_aroll6.mp4 --total 6.0 --out skills/finecut/render_project/compositions/finecut.html
cp output/finecut/_aroll6.mp4 skills/finecut/render_project/compositions/aroll.mp4
skills/finecut/render_project/node_modules/.bin/hyperframes lint skills/finecut/render_project
```
Expected: `0 error(s)`（warning 允许）。若报同轨重叠 → 检查 track-index 分配。

- [ ] **Step 3: snapshot 抽帧**

```bash
cd skills/finecut/render_project && node_modules/.bin/hyperframes snapshot . --composition compositions/finecut.html --at 1,2.5,4,5.5
```
Expected: 生成 PNG。逐张确认：①真人可见 ②磨砂面板在上方、透出背景 ③底部无遮挡 ④字体正常。

- [ ] **Step 4: 修问题（如有）**

若某模板字体/排版/对比有问题，回 `styles.css` 或对应 `templates.py` builder 调整，重跑 Step 2–3。

- [ ] **Step 5: Commit（仅样式/模板修正，验证产物不入库）**

```bash
git add skills/finecut/styles.css skills/finecut/templates.py
git commit -m "fix(finecut): 真机渲染微调四模板样式" || echo "无需修正"
```

---

## Task 8: 端到端验收（牛初乳真实素材）

> Mac 真机执行。目标 = spec 的 §5 端到端验收。

- [ ] **Step 1: 转写（若无现成）**

Run: `python3 src/transcribe.py "reference/粗剪_牛初乳老树开心花.mp4" -m medium -l zh -o output/niuchuru_transcript.json`
Expected: 已存在则跳过；产出词级时间戳 JSON。

- [ ] **Step 2: Agent 写 finecut-spec（人确认闸）**

读 `materials/scripts/定稿_牛初乳.md` + `output/niuchuru_transcript.json`，按选点规则产出 `output/finecut/niuchuru_spec.json`：
- chart @ "61万→1900万"（用 locate_phrase 求 start/end）
- topbar @ "奶中黄金" 或 stat @ 关键数字
- fullscreen @ "三个变化"
向 Chen 展示清单，确认/微调后继续。

- [ ] **Step 2.5: 校验 spec**

Run: `python3 -c "import sys; sys.path.insert(0,'.'); from skills.finecut.spec import load_spec,validate; print(validate(load_spec('output/finecut/niuchuru_spec.json')))"`
Expected: `[]`

- [ ] **Step 3: 渲染**

```bash
python3 skills/finecut/finecut.py render --spec output/finecut/niuchuru_spec.json --aroll "reference/粗剪_牛初乳老树开心花.mp4" --total 154.45 --out output/finecut/niuchuru_finecut.mp4
```
Expected: 渲染完成，输出 mp4。

- [ ] **Step 4: 抽帧验收**

```bash
for t in 10 19 56 65; do ffmpeg -y -i output/finecut/niuchuru_finecut.mp4 -ss $t -frames:v 1 output/finecut/verify_$t.png; done
```
逐张确认：①真人全程在 ②叠加层在上方、不压底部字幕 ③图形时长跟着口播 ④无割裂。供 Chen 终审。

- [ ] **Step 5: 回归一致性**

再渲一次到 `niuchuru_finecut_2.mp4`，`ffprobe` 时长一致、抽帧一致（无随机）。

---

## Task 9: 重写 SKILL.md + 废弃旧模板说明

**Files:**
- Modify: `skills/finecut/SKILL.md`（整体重写）
- Modify: `ARCHITECTURE.md`（标注旧 hyperframes-test 纯黑模板废弃、新 finecut 主路径）

- [ ] **Step 1: 重写 SKILL.md**

内容必须含：(a) 任务概述（磨砂叠加层共存范式）；(b) 四模板用途与 vars 表；(c) 上方安全区与底部留空规则；(d) 选点+密度规则；(e) 工作流：transcribe → 写 spec → 人确认 → `finecut.py render`；(f) finecut-spec 格式（引 `finecut.py schema`）；(g) 渲染须在 Mac（Chrome）。

- [ ] **Step 2: 更新 ARCHITECTURE.md**

在精剪节标注：现行主路径 = `skills/finecut/`（spec 驱动、磨砂叠加层）；`skills/hyperframes-test/compositions/*` 的纯黑整屏模板标记为旧实验，不再作为精剪主模板。

- [ ] **Step 3: Commit**

```bash
git add skills/finecut/SKILL.md ARCHITECTURE.md
git commit -m "docs(finecut): 重写 SKILL.md（磨砂叠加层范式）+ 更新架构清单"
```

---

## Self-Review（写计划后自查）

**Spec 覆盖：**
- §3.1 A-roll 进视频轨 → Task 5（build 含 a-roll 轨）✅
- §3.2 磨砂 + 上方安全区 → Task 3（styles，含 test_upper_safe_zone_not_bottom）✅
- §3.3 跟着话走自动费时 → Task 2（locate）+ Task 8 Step 2（用 locate 求窗）✅
- §3.4 四模板 → Task 4 ✅
- §3.5 选点密度（重叠/全屏≤2）→ Task 1 校验 ✅
- §3.6 确认闸工作流 → Task 6（CLI）+ Task 8 Step 2 ✅
- §4 finecut-spec 格式 → Task 1 + Task 6 schema ✅
- §5 验收 → Task 7（单模板）+ Task 8（端到端）✅
- §7 改写 SKILL.md → Task 9 ✅

**占位符扫描：** 无 TBD/TODO；每个代码步骤含完整代码。Task 7 Step 1 的 `_verify_spec.json` 内容描述了构成（四模板各一），执行者据 schema 填具体值——属真机验证脚本，非生产代码。

**类型一致性：** `Insert`/`FinecutSpec` 字段（id/template/placement/start_s/end_s/vars/based_on/note/edited_by）在 spec/templates/build/cli 中一致；`build_overlay(ins, track_index)` 签名在 Task 4 定义、Task 5 调用一致；`build(spec, aroll_src, total_s, styles_css)` 在 Task 5 定义、Task 6 调用一致。

**已知偏差（合理）：** §3.3 "讲完该段才淡出" 的淡出动画当前 builder 只做了淡入（`tl.from`），淡出依赖 HyperFrames clip 到 data-duration 结束自然消失。若需显式淡出动画，可在 Task 4 builder 末尾加 `tl.to(... opacity:0 ...)` —— 留作 Task 7 真机观察后决定，不阻塞主流程。

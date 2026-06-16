# skills/finecut/spec.py
from __future__ import annotations
from dataclasses import dataclass
import json

TEMPLATES = {"topbar", "stat", "chart", "fullscreen"}
PLACEMENTS = {"upper", "full"}
THEMES = {"frosted", "swiss", "kinetic"}  # 视觉配色：磨砂 / 瑞士网格 / 动态字体
# 每个模板要求的 placement（fullscreen 必须全屏，其余必须上方）
EXPECTED_PLACEMENT = {"topbar": "upper", "stat": "upper", "chart": "upper", "fullscreen": "full"}
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
    theme: str = "frosted"
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
    seen_ids = set()
    for ins in spec.inserts:
        if ins.id in seen_ids:
            errors.append(f"insert id 重复: {ins.id}")
        seen_ids.add(ins.id)
        if ins.template not in TEMPLATES:
            errors.append(f"insert {ins.id}: 未知 template '{ins.template}'")
        if ins.placement not in PLACEMENTS:
            errors.append(f"insert {ins.id}: 未知 placement '{ins.placement}'")
        if ins.theme not in THEMES:
            errors.append(f"insert {ins.id}: 未知 theme '{ins.theme}'")
        expected = EXPECTED_PLACEMENT.get(ins.template)
        if expected and ins.placement != expected:
            errors.append(f"insert {ins.id}: template '{ins.template}' 的 placement 必须是 '{expected}'")
        if ins.end_s <= ins.start_s:
            errors.append(f"insert {ins.id}: end_s 必须大于 start_s")
        for key in REQUIRED_VARS.get(ins.template, []):
            if key not in ins.vars:
                errors.append(f"insert {ins.id}: 缺少 vars['{key}']")
        if ins.template == "chart":
            bars = ins.vars.get("bars")
            if not isinstance(bars, list) or not bars:
                errors.append(f"insert {ins.id}: chart 的 bars 必须是非空数组")
        if ins.template == "fullscreen":
            full_count += 1
    if full_count > 2:
        errors.append(f"fullscreen 叠加层最多 2 个，当前 {full_count}")
    ordered = sorted(spec.inserts, key=lambda i: i.start_s)
    for a, b in zip(ordered, ordered[1:]):
        if b.start_s < a.end_s:
            errors.append(f"insert {a.id} 与 {b.id} 时间重叠")
    return errors

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

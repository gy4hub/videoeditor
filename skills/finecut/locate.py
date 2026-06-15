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
    spans = []
    for w in words:
        nw = _norm(w["word"])
        for _ in nw:
            spans.append((w["start"], w["end"]))
        concat += nw
    idx = concat.find(target)
    if idx < 0:
        return None
    return (spans[idx][0], spans[idx + len(target) - 1][1])

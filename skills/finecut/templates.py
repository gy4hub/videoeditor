# skills/finecut/templates.py
from __future__ import annotations
from html import escape

def _wrap(ins, track_index, extra_class, inner):
    dur = round(ins.end_s - ins.start_s, 3)
    cls = f"clip fc-panel fc-{ins.placement} {extra_class} fc-theme-{ins.theme}"
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

def _enter_tl(ins):
    """按 theme 给出入场动画：frosted 下滑淡入；swiss 左侧滑入；kinetic 子元素弹性错位。"""
    sid = f"#ins{ins.id}"
    if ins.theme == "swiss":
        return f'tl.from("{sid}", {{opacity:0, x:-40, duration:0.5, ease:"power3.out"}}, {ins.start_s});'
    if ins.theme == "kinetic":
        return (f'tl.set("{sid}", {{opacity:1}}, {ins.start_s});'
                f'tl.from("{sid} > *", {{opacity:0, y:40, scale:0.8, duration:0.5, '
                f'stagger:0.12, ease:"back.out(1.7)"}}, {ins.start_s});')
    return f'tl.from("{sid}", {{opacity:0, y:-24, duration:0.5, ease:"power2.out"}}, {ins.start_s});'

def build_overlay(ins, track_index: int) -> dict:
    inner, extra_class = _BUILDERS[ins.template](ins)
    html = _wrap(ins, track_index, extra_class, inner)
    fade_out_at = round(ins.end_s - 0.4, 3)
    tl = [
        _enter_tl(ins),
        f'tl.to("#ins{ins.id}", {{opacity:0, duration:0.4, ease:"power1.in"}}, {fade_out_at});',
    ]
    return {"html": html, "tl": tl}

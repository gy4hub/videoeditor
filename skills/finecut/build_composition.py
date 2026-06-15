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
    # 叠加层 track-index 从 1 递增；音频轨固定 100，故叠加层数量须 < 100 以免冲突
    assert len(spec.inserts) < 100, "overlay 数量过多，会与音频轨 index(100) 冲突"
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

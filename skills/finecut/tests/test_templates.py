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

def test_has_fade_in_and_out():
    o = build_overlay(_ins("topbar", "upper", {"title": "x"}), track_index=1)
    assert any("from" in l for l in o["tl"])
    assert any("to(" in l and "opacity:0" in l for l in o["tl"])

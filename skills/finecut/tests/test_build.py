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
    assert html.count('data-duration="154.4"') >= 2

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

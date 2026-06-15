from pathlib import Path

CSS = Path("skills/finecut/styles.css").read_text(encoding="utf-8") if Path("skills/finecut/styles.css").exists() else ""

def test_has_frosted_base():
    assert "backdrop-filter" in CSS and "blur(" in CSS

def test_upper_safe_zone_not_bottom():
    assert ".fc-upper" in CSS
    assert "bottom" not in CSS.split(".fc-upper")[1].split("}")[0]

def test_has_four_template_classes():
    for cls in [".fc-topbar", ".fc-stat", ".fc-chart", ".fc-fullscreen"]:
        assert cls in CSS

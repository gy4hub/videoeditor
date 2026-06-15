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

# skills/finecut/finecut.py
#!/usr/bin/env python3
"""finecut — 由 finecut-spec 组装 HyperFrames composition 并渲染精剪成片。"""
from __future__ import annotations
import argparse, json, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from skills.finecut.spec import load_spec, validate
from skills.finecut.build_composition import build

HERE = Path(__file__).resolve().parent
STYLES = (HERE / "styles.css").read_text(encoding="utf-8")
RENDER_PROJ = HERE / "render_project"
HF_BIN = RENDER_PROJ / "node_modules" / ".bin" / "hyperframes"

SCHEMA_EXAMPLE = {
    "source": "粗剪.mp4", "fps": 30, "width": 1080, "height": 1920,
    "inserts": [{
        "id": 1, "template": "chart", "placement": "upper",
        "start_s": 54.6, "end_s": 60.9,
        "based_on": "从61万美元涨到了1900万美元",
        "vars": {"eyebrow": "美国牛初乳市场 · 两年",
                 "bars": [{"label": "原来", "value": 61, "unit": "万美元"},
                          {"label": "现在", "value": 1900, "unit": "万美元"}],
                 "delta": "+3000%"},
        "note": ""}]}

def cmd_schema(_):
    print(json.dumps(SCHEMA_EXAMPLE, ensure_ascii=False, indent=2))
    return 0

def cmd_build(a):
    spec = load_spec(a.spec)
    errs = validate(spec)
    if errs:
        print("spec 校验失败：", file=sys.stderr)
        for e in errs: print("  -", e, file=sys.stderr)
        return 1
    html = build(spec, aroll_src=a.aroll, total_s=float(a.total), styles_css=STYLES)
    Path(a.out).write_text(html, encoding="utf-8")
    print(f"composition 写入 {a.out}")
    return 0

def cmd_render(a):
    proj = RENDER_PROJ
    proj.mkdir(parents=True, exist_ok=True)
    spec = load_spec(a.spec)
    errs = validate(spec)
    if errs:
        print("spec 校验失败：", *(f"\n  - {e}" for e in errs), file=sys.stderr); return 1
    # hyperframes render 要求项目根有 index.html；A-roll 用相对项目根的路径。
    # 用软链而非复制源片，避免每次渲染泄漏数百兆（源片可能 600M+）。
    aroll_link = proj / "aroll.mp4"
    if aroll_link.exists() or aroll_link.is_symlink():
        aroll_link.unlink()
    aroll_link.symlink_to(Path(a.aroll).resolve())
    html = build(spec, aroll_src="aroll.mp4", total_s=float(a.total), styles_css=STYLES)
    (proj / "index.html").write_text(html, encoding="utf-8")
    cmd = [str(HF_BIN), "render", str(proj), "--output", str(Path(a.out).resolve())]
    print("渲染中：", " ".join(cmd))
    rc = subprocess.run(cmd).returncode
    # 渲染产物已落到 --output；清理项目内的临时件（软链 + 生成的 index.html）
    if aroll_link.is_symlink():
        aroll_link.unlink()
    (proj / "index.html").unlink(missing_ok=True)
    return rc

def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--spec", required=True); b.add_argument("--aroll", required=True)
    b.add_argument("--total", required=True); b.add_argument("--out", required=True); b.set_defaults(fn=cmd_build)
    r = sub.add_parser("render"); r.add_argument("--spec", required=True); r.add_argument("--aroll", required=True)
    r.add_argument("--total", required=True); r.add_argument("--out", required=True); r.set_defaults(fn=cmd_render)
    s = sub.add_parser("schema"); s.set_defaults(fn=cmd_schema)
    args = p.parse_args()
    return args.fn(args)

if __name__ == "__main__":
    raise SystemExit(main())

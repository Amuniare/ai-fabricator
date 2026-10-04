"""The finished folder: parts, print files, a picture guide and the source.

Layout of ``package/<Project Name>/``::

    guide.html            one file: pieces, hardware, assembly steps with pictures
    Parts/                P01_Name.stl (as printed) and P01_Name.step (as assembled)
    Print files/          Plate_1.gcode.3mf ... open in Bambu Studio and print
    complete_model.step   every printed piece, assembled, as one file
    complete_model.glb    the same for viewers and web pages (part colours kept)
    Source/               project.yaml, design.py, sources.yaml, imports/
    README.txt
"""

from __future__ import annotations

import base64
import html
import json
import re
import shutil
from pathlib import Path

from . import slicer
from .design import JOINT_KINDS
from .project import Project, ProjectError


def _safe(name: str) -> str:
    """A file or folder name that is valid on Windows, Mac and Linux."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", name).strip(" .")
    return re.sub(r"\s+", " ", cleaned) or "Project"


def _fmt_minutes(minutes: float) -> str:
    h, m = divmod(int(round(minutes)), 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def _data_uri(path: Path | None) -> str | None:
    if path is None or not Path(path).exists():
        return None
    mime = "image/png" if str(path).lower().endswith(".png") else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii")


def _picture(project: Project, parts: list[str]) -> Path | None:
    """One picture of just these parts, or None when pictures can't be made."""
    try:
        from . import render
        shots = render.pictures(project, parts=parts, views=["angled"])
        return Path(shots[0]) if shots else None
    except Exception:  # a missing picture shouldn't stop the package
        return None


def _complete_step(project: Project, printed: list[dict], out: Path) -> None:
    from build123d import Compound, export_step, import_step

    shapes = [import_step(str(project.build_dir / "parts" / f"{p['id']}.step")) for p in printed]
    export_step(Compound(shapes), str(out))


def make(project: Project, settings, slice_plates: bool = True) -> Path:
    """Build the package folder and return its path. Replaces any earlier package of this project."""
    from . import viewer

    data = viewer.load_model_json(project)
    if data.get("status") == "fail":
        raise ProjectError("The last build has problems that must be fixed before packaging. "
                           "Run 'fabricator check' to see them.")
    printed = [p for p in data["parts"] if p["printed"]]
    if not printed:
        raise ProjectError("This design has no printed pieces to package.")
    for p in printed:
        if not (project.build_dir / "parts" / f"{p['id']}.step").exists():
            raise ProjectError("Build it first: the saved pieces are missing. Run 'fabricator build'.")

    sliced = slicer.slice_project(project, settings) if slice_plates else None

    root = project.package_dir / _safe(project.name)
    shutil.rmtree(root, ignore_errors=True)
    parts_dir, print_dir, src_dir = root / "Parts", root / "Print files", root / "Source"
    for d in (parts_dir, src_dir):
        d.mkdir(parents=True)

    # Parts: STL as printed (ready for any slicer), STEP as assembled (for CAD).
    for p in printed:
        stem = f"{p['id']}_{_safe(p['name']).replace(' ', '_')}"
        stl = project.build_dir / "print" / f"{p['id']}.stl"
        shutil.copy2(stl if stl.exists() else project.build_dir / "parts" / f"{p['id']}.stl", parts_dir / f"{stem}.stl")
        shutil.copy2(project.build_dir / "parts" / f"{p['id']}.step", parts_dir / f"{stem}.step")

    # Print files.
    plate_of: dict[str, dict] = {}
    if sliced:
        print_dir.mkdir()
        for plate in sliced["plates"]:
            shutil.copy2(plate["file"], print_dir / f"Plate_{plate['number']}.gcode.3mf")
            for pid in plate["parts"]:
                plate_of[pid] = plate

    # Whole model.
    _complete_step(project, printed, root / "complete_model.step")
    glb, _ = viewer.glb_bytes(project, include_reference=False)
    (root / "complete_model.glb").write_bytes(glb)

    # Source.
    for name in ("project.yaml", "design.py", "sources.yaml"):
        if (project.path / name).exists():
            shutil.copy2(project.path / name, src_dir / name)
    if (project.path / "imports").is_dir():
        shutil.copytree(project.path / "imports", src_dir / "imports")

    (root / "guide.html").write_text(_guide(project, data, sliced, plate_of), encoding="utf-8")
    (root / "README.txt").write_text(_readme(project, bool(sliced)), encoding="utf-8")
    return root


def _readme(project: Project, sliced: bool) -> str:
    lines = [
        f"{project.name}: everything you need to make it.",
        "Open guide.html in your browser for the pieces, the shopping list and the assembly steps.",
        ("Print files: open each Plate file in Bambu Studio and press Print."
         if sliced else "Print files: open the files in Parts in Bambu Studio, pick your printer and press Print."),
        "Parts holds every piece as STL (lying the way it prints) and STEP (for editing in CAD).",
        "complete_model.step and complete_model.glb show the finished object; Source can rebuild it.",
    ]
    return "\n".join(lines) + "\n"


def _guide(project: Project, data: dict, sliced: dict | None, plate_of: dict) -> str:
    e = html.escape
    names = {p["id"]: p["name"] for p in data["parts"]}
    printed = [p for p in data["parts"] if p["printed"]]

    overview = None
    for rel in data.get("pictures", []):
        overview = project.path / rel
        break
    overview_uri = _data_uri(overview)

    rows = []
    for p in printed:
        size = " x ".join(f"{v:g}" for v in (p.get("print_size_mm") or p["size_mm"]))
        plate = plate_of.get(p["id"])
        if plate:
            share = "" if len(plate["parts"]) == 1 else f" (shared plate {plate['number']})"
            time = _fmt_minutes(plate["minutes"]) + share
        else:
            time = "not sliced"
        rows.append(f"<tr><td>{e(p['id'])}</td><td>{e(p['name'])}</td><td>{e(size)} mm</td><td>{e(time)}</td></tr>")

    hardware = data.get("hardware") or {}
    hw_html = ("<ul>" + "".join(f"<li>{q} &times; {e(item)}</li>" for item, q in hardware.items()) + "</ul>"
               if hardware else "<p>None. Everything is printed.</p>")

    steps = []
    for n, j in enumerate(data.get("joints", []), start=1):
        a, b = names.get(j["a"], j["a"]), names.get(j["b"], j["b"])
        kind = JOINT_KINDS.get(j["kind"], j["kind"])
        hw = f" You will need: {e(', '.join(j['hardware']))}." if j.get("hardware") else ""
        note = f" {e(j['note'])}" if j.get("note") else ""
        img = _data_uri(_picture(project, [j["a"], j["b"]]))
        pic = f'<img alt="{e(a)} and {e(b)}" src="{img}">' if img else ""
        steps.append(
            f"<li><p><strong>Join {e(a)} ({e(j['a'])}) to {e(b)} ({e(j['b'])}).</strong> "
            f"Joint: {e(j['kind'])}, {e(kind)}.{hw}{note}</p>{pic}</li>")
    steps_html = ("<ol>" + "".join(steps) + "</ol>" if steps
                  else "<p>This is a single piece, so there is nothing to assemble.</p>")

    total = ""
    if sliced:
        total = (f"<p>Printed on {e(sliced['printer'])} in {e(sliced['material'])}: "
                 f"{_fmt_minutes(sliced['total_minutes'])} and {sliced['total_grams']:.0f} g of plastic "
                 f"across {len(sliced['plates'])} plate(s).</p>")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(project.name)} - guide</title>
<style>
  :root {{ --bg:#ffffff; --ink:#1d2330; --muted:#5b6475; --line:#d9dce3; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg:#14171d; --ink:#e8eaf0; --muted:#9aa3b5; --line:#2f3644; }} }}
  body {{ margin:0 auto; max-width:820px; padding:24px 16px 64px; background:var(--bg); color:var(--ink);
         font:16px/1.5 system-ui, Segoe UI, sans-serif; }}
  h1 {{ margin-bottom:4px; }} h2 {{ margin-top:36px; border-bottom:1px solid var(--line); padding-bottom:4px; }}
  img {{ max-width:100%; height:auto; border-radius:8px; border:1px solid var(--line); }}
  table {{ width:100%; border-collapse:collapse; }}
  th, td {{ text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); }}
  li {{ margin-bottom:20px; }} .muted {{ color:var(--muted); }}
</style></head><body>
<h1>{e(project.name)}</h1>
<p class="muted">{e(project.data.get('request') or '')}</p>
{f'<img alt="The finished object" src="{overview_uri}">' if overview_uri else ''}
{total}
<h2>Pieces to print</h2>
<table><tr><th>ID</th><th>Name</th><th>Size as printed</th><th>Print time</th></tr>
{''.join(rows)}</table>
<h2>What to buy</h2>
{hw_html}
<h2>Putting it together</h2>
{steps_html}
</body></html>
"""

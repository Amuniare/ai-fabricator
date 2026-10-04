"""Slice several ways of making the same thing and say which is best.

``compare(project, settings, part, what)`` returns::

    {"options": [{"label", "minutes", "grams", "support_grams", ...}],
     "recommended": label, "reason": one plain sentence}
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from . import slicer
from .project import Project, ProjectError

NO_SUPPORT_TIME_ALLOWANCE = 0.35  # a support-free option may take up to 35% longer
SPLIT_JOINTS = ("peg", "dowel", "screw")


def _fmt(minutes: float) -> str:
    h, m = divmod(int(round(minutes)), 60)
    return f"{h}h {m:02d}m" if h else f"{m} minutes"


def _built(project: Project) -> dict:
    f = project.build_dir / "model.json"
    if not f.exists():
        raise ProjectError("Build it first: this project hasn't been built yet. Run 'fabricator build'.")
    return json.loads(f.read_text(encoding="utf-8"))


def _pick_part(data: dict, part: str | None) -> dict:
    printed = [p for p in data["parts"] if p.get("printed")]
    if not printed:
        raise ProjectError("This design has no printed pieces to compare.")
    if part is None:
        if len(printed) == 1:
            return printed[0]
        raise ProjectError("Which piece? Use --part with one of: "
                           + ", ".join(f"{p['id']} ({p['name']})" for p in printed))
    for p in printed:
        if part.lower() in (p["id"].lower(), p["name"].lower()):
            return p
    raise ProjectError(f"No printed piece '{part}'. Printed pieces: "
                       + ", ".join(f"{p['id']} ({p['name']})" for p in printed))


def recommend(options: list[dict]) -> tuple[dict, str]:
    """Pick one option and say why in a sentence that matches the numbers.

    Supports are wasted plastic and leave rough marks, so an option without them wins
    unless it takes much longer (more than NO_SUPPORT_TIME_ALLOWANCE) than the fastest.
    """
    key = lambda o: (round(o["minutes"], 1), o["grams"])  # noqa: E731  ties go to fewer grams
    fastest = min(options, key=key)

    def needs_support(o):
        return bool(o.get("support_needed")) or o.get("support_grams", 0) > 0.05

    clean = [o for o in options if not needs_support(o)]
    if clean:
        best_clean = min(clean, key=key)
        if best_clean is fastest:
            return best_clean, (f"{best_clean['label']} is the quickest at {_fmt(best_clean['minutes'])} "
                                f"and needs no supports.")
        extra = best_clean["minutes"] - fastest["minutes"]
        if best_clean["minutes"] <= fastest["minutes"] * (1 + NO_SUPPORT_TIME_ALLOWANCE):
            return best_clean, (f"{best_clean['label']} needs no supports. It takes {_fmt(extra)} longer than "
                                f"{fastest['label']}, but wastes no plastic and leaves cleaner surfaces.")
        return fastest, (f"{fastest['label']} is quickest at {_fmt(fastest['minutes'])} but needs "
                         f"{fastest.get('support_grams', 0):.0f} g of supports; {best_clean['label']} needs none "
                         f"but takes {_fmt(extra)} longer.")
    return fastest, (f"{fastest['label']} is the quickest at {_fmt(fastest['minutes'])}. "
                     f"Every option needs some supports.")


def compare(project: Project, settings, part: str | None = None, what: str = "orientation") -> dict:
    if what == "orientation":
        return _compare_orientation(project, settings, part)
    if what == "split":
        return _compare_split(project, settings)
    raise ProjectError("I can compare 'orientation' or 'split'.")


# ---- orientation ----------------------------------------------------------------------------

def _compare_orientation(project: Project, settings, part: str | None) -> dict:
    from build123d import export_stl, import_step

    from . import orient
    from .design import Context, Part3D

    data = _built(project)
    info = _pick_part(data, part)
    step = project.build_dir / "parts" / f"{info['id']}.step"
    if not step.exists():
        raise ProjectError("Build it first: the saved piece is missing. Run 'fabricator build'.")
    shape = import_step(str(step))
    part3d = Part3D(id=info["id"], name=info["name"], shape=shape, color=info.get("color", "#888888"),
                    hardware=info.get("hardware", []), notes=info.get("notes", ""))
    bed = slicer.printer_bed(settings)
    ctx = Context(settings, bed, project.path)
    cands = list(orient.candidates(part3d, bed, ctx))
    cands = [c for c in cands if getattr(c, "fits", True)] or cands
    cands = cands[:3]
    if not cands:
        raise ProjectError(f"I couldn't find any way to lay {info['name']} on the bed.")

    options = []
    options_cfg = project.data.get("print") or {}
    with tempfile.TemporaryDirectory(prefix="fabricator-compare-") as tmp:
        for i, cand in enumerate(cands):
            placed = orient.place_on_bed(shape, cand)
            stl = Path(tmp) / f"option{i}.stl"
            export_stl(placed, str(stl), tolerance=0.02, angular_tolerance=0.2)
            r = slicer.slice_files(settings, [stl], None, options_cfg, bool(getattr(cand, "support_needed", False)))
            options.append({
                "label": cand.label, "minutes": r["minutes"], "grams": r["grams"],
                "support_grams": r["support_grams"],
                "support_needed": bool(getattr(cand, "support_needed", False)),
                "height_mm": getattr(cand, "height_mm", None),
                "face_down": getattr(cand, "face_down", None),
                "overhang_area_mm2": getattr(cand, "overhang_area_mm2", None),
            })
    best, reason = recommend(options)
    return {"part": info["id"], "what": "orientation", "options": options,
            "recommended": best["label"], "reason": reason}


# ---- split -------------------------------------------------------------------------------------

def _copy_project(project: Project, dest: Path) -> Project:
    ignore = shutil.ignore_patterns("build", "slices", "package", ".git", "__pycache__")
    shutil.copytree(project.path, dest, ignore=ignore)
    return Project(dest)


def _compare_split(project: Project, settings) -> dict:
    import yaml

    from .build import build

    _built(project)
    current = dict(project.data.get("split") or {})
    base_joint = current.get("joint", "peg")
    variants = [("as designed (%s joints)" % base_joint, current)]
    for joint in SPLIT_JOINTS:
        if joint != base_joint and len(variants) < 3:
            variants.append((f"{joint} joints", dict(current, joint=joint)))

    options, skipped = [], []
    with tempfile.TemporaryDirectory(prefix="fabricator-compare-") as tmp:
        for i, (label, split_opts) in enumerate(variants):
            copy = _copy_project(project, Path(tmp) / f"option{i}")
            data = copy.data
            data["split"] = split_opts
            (copy.path / "project.yaml").write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                                                    encoding="utf-8")
            try:
                result = build(copy, save=False, pictures=False)
                if result.status == "fail":
                    skipped.append(f"{label} (the checks failed)")
                    continue
                sliced = slicer.slice_project(copy, settings)
            except ProjectError as e:
                skipped.append(f"{label} ({e})")
                continue
            printed = [p for p in result.summary["parts"] if p["printed"]]
            options.append({
                "label": label, "minutes": sliced["total_minutes"], "grams": sliced["total_grams"],
                "support_grams": sum(p["support_grams"] for p in sliced["plates"]),
                "support_needed": any((p.get("orientation") or {}).get("support_needed") for p in printed),
                "pieces": len(printed), "plates": len(sliced["plates"]), "split": split_opts,
            })
    if not options:
        raise ProjectError("None of the split choices could be built and sliced. " + "; ".join(skipped))
    best, reason = recommend(options)
    if len(options) > 1:
        reason += f" It makes {best['pieces']} piece(s)."
    out = {"what": "split", "options": options, "recommended": best["label"], "reason": reason}
    if skipped:
        out["skipped"] = skipped
    return out

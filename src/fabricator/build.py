"""The build: design code in, checked parts and pictures out.

Steps, in order:

1. Run design.py in its own process (runner.py) and load the parts it made.
2. Split any part too big for the printer into pieces with joints (split.py).
3. Choose how each piece lies on the bed (orient.py).
4. Save every piece as STEP and STL, both as assembled and as printed.
5. Run the checks: geometry, printability and assembly (checks/).
6. Make pictures (render.py).
7. Write build/model.json and build/checks.json, and save a version when everything passed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from build123d import export_step, export_stl, import_brep

from . import orient, render, slicer, split
from .checks import Check, assembly, geometry, printability, worst
from .design import Context, Joint, Model, Part3D
from .meshing import to_mesh
from .project import Project, ProjectError
from .settings import Settings

DESIGN_TIME_LIMIT = 300  # seconds


@dataclass
class BuildResult:
    project: Project
    model: Model
    checks: list[Check]
    status: str  # pass / warn / fail
    pictures: list[Path] = field(default_factory=list)
    version: dict | None = None
    messages: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


class DesignError(ProjectError):
    """The design code failed. Carries the details Claude needs to fix it."""

    def __init__(self, message: str, line: int | None, details: str):
        super().__init__(message)
        self.line = line
        self.details = details


def run_design(project: Project, bed: tuple[float, float, float]) -> Model:
    """Run design.py in a separate process and load what it made."""
    raw_dir = project.build_dir / "raw"
    shutil.rmtree(raw_dir, ignore_errors=True)
    raw_dir.mkdir(parents=True)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "fabricator.runner", str(project.path), str(raw_dir),
             ",".join(str(b) for b in bed)],
            capture_output=True, text=True, encoding="utf-8", timeout=DESIGN_TIME_LIMIT, env=env,
        )
    except subprocess.TimeoutExpired:
        raise DesignError(
            f"The design took longer than {DESIGN_TIME_LIMIT // 60} minutes to build, so it was stopped. "
            "Usually this means too many small details or rounded edges on a complex shape.",
            None, "",
        ) from None
    error_file = raw_dir / "error.json"
    if proc.returncode != 0 or error_file.exists():
        if error_file.exists():
            err = json.loads(error_file.read_text(encoding="utf-8"))
            raise DesignError(err["error"], err.get("design_line"), err.get("traceback", ""))
        raise DesignError("The design stopped unexpectedly.", None, proc.stderr[-4000:])
    return load_raw(raw_dir)


def load_raw(raw_dir: Path) -> Model:
    data = json.loads((raw_dir / "raw.json").read_text(encoding="utf-8"))
    model = Model(data["name"])
    for p in data["parts"]:
        model.parts.append(Part3D(
            id=p["id"], name=p["name"], shape=import_brep(str(raw_dir / p["file"])),
            color=p["color"], printed=p["printed"], hardware=p["hardware"],
            face_down=p.get("face_down"), notes=p.get("notes", ""),
        ))
    for j in data["joints"]:
        joint = Joint(
            a=j["a"], b=j["b"], kind=j["kind"], fit=j["fit"], gap=j["gap"],
            hardware=j["hardware"], at=tuple(j["at"]) if j["at"] else None,
            axis=tuple(j["axis"]) if j["axis"] else None, note=j["note"],
        )
        joint.features = {k: import_brep(str(raw_dir / f)) for k, f in j["features"].items()}
        model.joints.append(joint)
    model.extra_hardware = data["hardware"]
    model.notes = data["notes"]
    return model


def build(project: Project, note: str | None = None, save: bool = True, pictures: bool = True) -> BuildResult:
    started = time.time()
    settings = Settings.load()
    bed = slicer.printer_bed(settings)
    ctx = Context(settings, bed, project.path)

    previous = _fingerprints_from(project.build_dir / "model.json")

    # Start clean so nothing stale is mistaken for this build's output.
    for sub in ("parts", "print", "pictures"):
        shutil.rmtree(project.build_dir / sub, ignore_errors=True)
    for f in ("model.json", "checks.json"):
        (project.build_dir / f).unlink(missing_ok=True)

    model = run_design(project, bed)
    messages: list[str] = list(model.notes)

    # Split anything too big for the printer.
    options = project.data.get("split") or {}
    model, split_messages = split.split_oversized(model, ctx, options)
    messages += split_messages

    parts_dir = project.build_dir / "parts"
    print_dir = project.build_dir / "print"
    parts_dir.mkdir(parents=True, exist_ok=True)
    print_dir.mkdir(parents=True, exist_ok=True)

    checks: list[Check] = []
    part_info = []
    for part in model.parts:
        export_step(part.shape, str(parts_dir / f"{part.id}.step"))
        export_stl(part.shape, str(parts_dir / f"{part.id}.stl"), tolerance=0.02, angular_tolerance=0.2)
        info = {
            "id": part.id, "name": part.name, "printed": part.printed, "color": part.color,
            "hardware": part.hardware, "source_part": part.source_part, "notes": part.notes,
        }
        bb = part.shape.bounding_box()
        info["size_mm"] = [round(bb.size.X, 2), round(bb.size.Y, 2), round(bb.size.Z, 2)]
        info["volume_cm3"] = round(part.shape.volume / 1000, 2)
        info["fingerprint"] = fingerprint(part.shape)
        if part.printed:
            choice = orient.choose(part, bed, ctx)
            placed = orient.place_on_bed(part.shape, choice)
            export_stl(placed, str(print_dir / f"{part.id}.stl"), tolerance=0.02, angular_tolerance=0.2)
            placed_mesh = to_mesh(placed)
            info["orientation"] = choice.to_dict()
            pb = placed.bounding_box()
            info["print_size_mm"] = [round(pb.size.X, 2), round(pb.size.Y, 2), round(pb.size.Z, 2)]
            checks += geometry.check_part(part, placed, placed_mesh, ctx)
            checks += printability.check_part(part, placed, placed_mesh, ctx, choice)
        part_info.append(info)

    checks += assembly.check_model(model, ctx)
    status = worst(checks)

    shots: list[Path] = []
    if pictures:
        shots = render.build_pictures(model, project.build_dir / "pictures", ctx)

    changes = reprint_list(previous, part_info)
    if changes:
        messages.append(changes)

    summary = {
        "name": model.name,
        "printer": settings.printer, "nozzle": settings.nozzle, "material": settings.material,
        "bed_mm": list(bed),
        "status": status,
        "parts": part_info,
        "joints": [
            {"a": j.a, "b": j.b, "kind": j.kind, "fit": j.fit, "gap": j.gap,
             "hardware": j.hardware, "at": j.at, "axis": j.axis, "note": j.note}
            for j in model.joints
        ],
        "hardware": hardware_list(model),
        "messages": messages,
        "pictures": [str(p.relative_to(project.path)) for p in shots],
        "seconds": round(time.time() - started, 1),
    }
    (project.build_dir / "model.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (project.build_dir / "checks.json").write_text(
        json.dumps({"status": status, "checks": [c.to_dict() for c in checks]}, indent=2), encoding="utf-8"
    )

    version = None
    if save and status != "fail":
        version = project.save_version(note or "design updated")
        if version:
            summary["version"] = version["number"]

    return BuildResult(project, model, checks, status, shots, version, messages, summary)


def fingerprint(shape) -> str:
    """A short code that changes whenever a piece's shape changes (size, volume, surface)."""
    import hashlib

    bb = shape.bounding_box()
    numbers = [shape.volume, shape.area, *tuple(bb.min), *tuple(bb.max)]
    return hashlib.sha1(",".join(f"{v:.2f}" for v in numbers).encode()).hexdigest()[:12]


def _fingerprints_from(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {p["id"]: p.get("fingerprint", "") for p in data.get("parts", []) if p.get("printed")}


def reprint_list(previous: dict[str, str], parts: list[dict]) -> str:
    """One sentence saying which printed pieces changed since the last build, if there are several."""
    printed = [p for p in parts if p["printed"]]
    if not previous or len(printed) < 2:
        return ""
    changed = [p["id"] for p in printed if previous.get(p["id"]) != p["fingerprint"]]
    same = [p["id"] for p in printed if p["id"] not in changed]
    if not changed:
        return "No pieces changed shape since the last build."
    if not same:
        return "Every piece changed shape since the last build."
    return (f"Changed since the last build: {', '.join(changed)}. "
            f"Unchanged: {', '.join(same)} — no need to reprint those if they're already printed.")


def hardware_list(model: Model) -> dict[str, int]:
    """Every bought part the assembly needs, counted: {'M3x12 screw': 4}."""
    counts: dict[str, int] = {}
    items = list(model.extra_hardware)
    for part in model.parts:
        items += part.hardware
    for joint in model.joints:
        items += joint.hardware
    for item in items:
        counts[item] = counts.get(item, 0) + 1
    return dict(sorted(counts.items()))


def load_built(project: Project) -> dict:
    """The last build's summary, or an error telling the user to build first."""
    path = project.build_dir / "model.json"
    if not path.exists():
        raise ProjectError("This project hasn't been built yet. Run 'fabricator build' first.")
    return json.loads(path.read_text(encoding="utf-8"))

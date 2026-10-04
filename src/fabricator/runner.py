"""Runs a project's design.py in its own process and saves the parts it makes.

Called as ``python -m fabricator.runner <project folder> <output folder>``. Keeping the
design in a separate process means a broken or slow design can't take the tool down,
and the build can stop it after a time limit.

Writes ``raw.json`` (parts, joints, hardware) and one ``.brep`` file per part (an exact
CAD file). On failure writes ``error.json`` with the error and the design line it came from.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import traceback
from pathlib import Path

import yaml


def _load_design(project_dir: Path):
    path = project_dir / "design.py"
    spec = importlib.util.spec_from_file_location("project_design", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Can't read {path}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(project_dir))
    spec.loader.exec_module(module)
    if not hasattr(module, "build"):
        raise RuntimeError("design.py has no build(p, ctx) function.")
    return module


def _design_line(tb) -> int | None:
    """The line in design.py where the error happened, if it happened there."""
    package_dir = Path(__file__).parent.resolve()
    for frame in reversed(traceback.extract_tb(tb)):
        path = Path(frame.filename)
        if path.name == "design.py" and path.parent.resolve() != package_dir:
            return frame.lineno
    return None


def main(project_dir: Path, out_dir: Path, bed: tuple[float, float, float]) -> int:
    from build123d import Compound, Shape, export_brep

    from .design import Context, Model, Params
    from .settings import Settings

    out_dir.mkdir(parents=True, exist_ok=True)
    data = yaml.safe_load((project_dir / "project.yaml").read_text(encoding="utf-8")) or {}
    params = Params(data.get("parameters") or {})
    ctx = Context(Settings.load(), bed, project_dir)

    module = _load_design(project_dir)
    model = module.build(params, ctx)
    if not isinstance(model, Model):
        raise RuntimeError("build() must return a Model. End it with 'return model'.")
    if not model.parts:
        raise RuntimeError("The design made no parts. Add at least one with model.add(...).")

    parts = []
    for part in model.parts:
        shape = part.shape
        if not isinstance(shape, Shape):
            raise RuntimeError(f"Part '{part.name}' is not a 3D shape.")
        if isinstance(shape, Compound) and len(shape.solids()) == 1:
            shape = shape.solids()[0]
        file = out_dir / f"{part.id}.brep"
        export_brep(shape, str(file))
        parts.append({
            "id": part.id, "name": part.name, "file": file.name, "color": part.color,
            "printed": part.printed, "hardware": part.hardware, "face_down": part.face_down,
            "notes": part.notes,
        })

    joints = []
    for i, joint in enumerate(model.joints):
        features = {}
        for key, shape in joint.features.items():
            file = out_dir / f"joint{i}_{key}.brep"
            export_brep(shape, str(file))
            features[key] = file.name
        joints.append({
            "a": joint.a, "b": joint.b, "kind": joint.kind, "fit": joint.fit,
            "gap": joint.gap if joint.gap is not None else ctx.fits.get(joint.fit),
            "hardware": joint.hardware, "at": joint.at, "axis": joint.axis,
            "note": joint.note, "features": features,
        })

    (out_dir / "raw.json").write_text(json.dumps({
        "name": model.name, "parts": parts, "joints": joints,
        "hardware": model.extra_hardware, "notes": model.notes,
    }, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    project_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    bed = tuple(float(x) for x in sys.argv[3].split(",")) if len(sys.argv) > 3 else (180.0, 180.0, 180.0)
    try:
        sys.exit(main(project_dir, out_dir, bed))  # type: ignore[arg-type]
    except Exception as exc:  # report every failure in a form the build can explain
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "error.json").write_text(json.dumps({
            "error": f"{type(exc).__name__}: {exc}",
            "design_line": _design_line(exc.__traceback__),
            "traceback": traceback.format_exc(),
        }, indent=2), encoding="utf-8")
        sys.exit(1)

"""Where outside information came from: measurements, datasheets and downloaded shape files.

Everything lives in the project's ``sources.yaml`` so a number can always be traced back
to its origin. Imported files are shape data only; they are never run as code.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .project import Project, ProjectError, slugify

ALLOWED = {".step", ".stp", ".stl", ".3mf", ".obj"}
# Highest trust first.
RANK = ["manufacturer", "datasheet", "standard", "measured", "retailer", "community"]
TRUSTED = {"manufacturer", "datasheet", "standard"}
DISAGREE = 0.02


def _read(project: Project) -> dict:
    path = project.path / "sources.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    data = data or {}
    data.setdefault("sources", [])
    data.setdefault("imports", [])
    return data


def _write(project: Project, data: dict) -> None:
    (project.path / "sources.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8"
    )


def _number(value) -> float | None:
    try:
        return float(str(value).strip().split()[0])
    except (ValueError, IndexError):
        return None


def add_source(
    project: Project,
    what: str,
    value,
    unit: str = "mm",
    url: str = "",
    kind: str = "manufacturer",
    confidence: str = "high",
    note: str = "",
) -> dict:
    """Record an outside value. Adds a "warning" if it conflicts with a better source."""
    if kind not in RANK:
        raise ProjectError(f"Unknown source kind '{kind}'. Use one of: {', '.join(RANK)}.")
    data = _read(project)
    record = {
        "what": what,
        "value": value,
        "unit": unit,
        "url": url,
        "kind": kind,
        "confidence": confidence,
        "note": note,
        "date": date.today().isoformat(),
    }

    warning = None
    new_num = _number(value)
    for old in data["sources"]:
        if old.get("what") != what or old.get("kind") not in TRUSTED:
            continue
        lower = RANK.index(kind) > RANK.index(old["kind"])
        old_num = _number(old.get("value"))
        differs = (
            new_num is not None and old_num not in (None, 0.0) and abs(new_num - old_num) / abs(old_num) > DISAGREE
        )
        if differs:
            warning = (
                f"This {kind} value ({value} {unit}) disagrees with the {old['kind']} value "
                f"already recorded for '{what}' ({old['value']} {old.get('unit', '')}). "
                f"Check which is right before using it."
            )
            break
        if lower and old_num != new_num:
            warning = (
                f"A {old['kind']} source already gives '{what}' as {old['value']} "
                f"{old.get('unit', '')}; prefer that one over this {kind} source."
            )
            break

    data["sources"].append(record)
    _write(project, data)
    out = dict(record)
    if warning:
        out["warning"] = warning
    return out


def _bbox_from_trimesh(path: Path) -> list[float]:
    import trimesh

    loaded: Any = trimesh.load(str(path), force="scene")  # Scene or Trimesh; stubs say Geometry
    if hasattr(loaded, "dump"):
        meshes = [m for m in loaded.dump() if hasattr(m, "vertices") and len(m.vertices)]
        if not meshes:
            raise ValueError("no shapes found in the file")
        loaded = trimesh.util.concatenate(meshes)
    if not len(loaded.vertices) or not len(loaded.faces):
        raise ValueError("the file has no surfaces")
    return [round(float(v), 3) for v in loaded.extents]


def _bbox_from_step(path: Path) -> list[float]:
    from build123d import import_step

    shape = import_step(str(path))
    bb = shape.bounding_box()
    size = [bb.size.X, bb.size.Y, bb.size.Z]
    if max(size) <= 0:
        raise ValueError("the file has no solid shapes")
    return [round(float(v), 3) for v in size]


def import_file(
    project: Project,
    file,
    name: str | None = None,
    url: str = "",
    author: str = "",
    license: str = "unknown",
    confidence: str = "medium",
) -> dict:
    """Copy a shape file into the project, check it loads, and record where it came from."""
    src = Path(file).expanduser()
    ext = src.suffix.lower()
    if ext not in ALLOWED:
        raise ProjectError(
            f"Only shape files can be imported ({', '.join(sorted(ALLOWED))}), because they hold "
            f"geometry and can't run code. '{src.name}' is not one of those."
        )
    if not src.is_file():
        raise ProjectError(f"I can't find the file '{src}'.")
    name = slugify(name or src.stem).replace("-", "_")
    name = re.sub(r"[^a-z0-9_]", "_", name)
    dest_dir = project.path / "imports"
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / f"{name}{ext}"

    try:
        size_mm = _bbox_from_step(src) if ext in (".step", ".stp") else _bbox_from_trimesh(src)
    except Exception as e:
        raise ProjectError(f"'{src.name}' doesn't load as a 3D shape ({e}). Nothing was imported.") from e

    data = _read(project)
    for old in data["imports"]:  # replacing an import of the same name: drop other formats
        if old.get("name") == name:
            old_file = project.path / old.get("file", "")
            if old_file.is_file() and old_file != dest:
                old_file.unlink()
    data["imports"] = [i for i in data["imports"] if i.get("name") != name]
    shutil.copyfile(src, dest)

    record = {
        "name": name,
        "format": ext.lstrip(".").replace("stp", "step"),
        "file": f"imports/{dest.name}",
        "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
        "bytes": dest.stat().st_size,
        "size_mm": size_mm,
        "url": url,
        "author": author,
        "license": license,
        "confidence": confidence,
        "date": date.today().isoformat(),
    }
    data["imports"].append(record)
    _write(project, data)
    return record


def load_import(project_dir, name: str):
    """Load an imported file as a build123d shape. Reads geometry only."""
    folder = Path(project_dir) / "imports"
    matches = [p for p in sorted(folder.glob(f"{name}.*")) if p.suffix.lower() in ALLOWED] if folder.is_dir() else []
    if not matches:
        have = sorted({p.stem for p in folder.glob("*") if p.suffix.lower() in ALLOWED}) if folder.is_dir() else []
        raise ProjectError(
            f"No imported file called '{name}'."
            + (f" Imported so far: {', '.join(have)}." if have else " Nothing has been imported yet.")
        )
    path = matches[0]
    ext = path.suffix.lower()
    try:
        if ext in (".step", ".stp"):
            from build123d import import_step

            return import_step(str(path))
        if ext == ".stl":
            from build123d import import_stl

            return import_stl(str(path))
        return _mesh_to_shape(path)
    except ProjectError:
        raise
    except Exception as e:
        raise ProjectError(f"The imported file '{path.name}' couldn't be loaded ({e}).") from e


def _mesh_to_shape(path: Path):
    """3MF/OBJ: go through trimesh, write a temporary STL, read it with build123d."""
    import tempfile

    import trimesh
    from build123d import import_stl

    loaded: Any = trimesh.load(str(path), force="scene")  # Scene or Trimesh; stubs say Geometry
    mesh = trimesh.util.concatenate(list(loaded.dump())) if hasattr(loaded, "dump") else loaded
    with tempfile.TemporaryDirectory() as tmp:
        stl = Path(tmp) / "m.stl"
        mesh.export(str(stl))
        return import_stl(str(stl))

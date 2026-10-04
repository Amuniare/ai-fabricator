"""The slicer: finds Bambu Studio (or OrcaSlicer), merges its profiles, slices, reads time and grams."""

from __future__ import annotations

import shutil
from pathlib import Path

from .. import plates as plates_mod
from ..project import Project, ProjectError
from ..settings import Settings
from . import bambu, orca
from .profiles import (find_slicer, list_printers, match_printer, printer_bed,  # noqa: F401
                       project_profiles, require_slicer)

__all__ = ["find_slicer", "list_printers", "match_printer", "printer_bed", "slice_project", "slice_files"]

PLATE_SPACING = 6.0  # mm between pieces on a plate


def slice_files(settings: Settings, files: list[Path], out_file: Path | None = None,
                print_options: dict | None = None, supports: bool = False, info: dict | None = None) -> dict:
    """Slice already-placed STL files onto one bed.

    Returns {minutes, grams, support_grams, file, plates, slicer, printer, material}.
    """
    info = info or require_slicer(settings)
    profiles = project_profiles(settings, print_options, supports, info)
    runner = orca.run if info["kind"] == "orca" else bambu.run
    result = runner(info, profiles, files, out_file)
    result.update(slicer="Bambu Studio" if info["kind"] == "bambu" else "OrcaSlicer",
                  printer=settings.printer, material=profiles["filament_name"].split(" @")[0])
    return result


def _built_parts(project: Project, wanted: list[str] | None) -> list[dict]:
    model = project.build_dir / "model.json"
    if not model.exists():
        raise ProjectError("Build it first: this project hasn't been built yet. Run 'fabricator build'.")
    import json
    data = json.loads(model.read_text(encoding="utf-8"))
    if data.get("status") == "fail":
        raise ProjectError("Build it first: the last build has problems that must be fixed before "
                           "anything can be printed. Run 'fabricator check' to see them.")
    printed = [p for p in data["parts"] if p.get("printed")]
    if wanted:
        keys = {w.lower() for w in wanted}
        chosen = [p for p in printed if p["id"].lower() in keys or p["name"].lower() in keys]
        if not chosen:
            raise ProjectError("None of those parts are printed pieces. Printed pieces: "
                               + ", ".join(f"{p['id']} ({p['name']})" for p in printed))
        printed = chosen
    if not printed:
        raise ProjectError("This design has no printed pieces to slice.")
    return printed


def slice_project(project: Project, settings: Settings, parts: list[str] | None = None) -> dict:
    """Slice every printed piece onto as few plates as possible.

    Writes ``slices/plate_N.gcode.3mf`` and returns the contract documented in architecture.md.
    """
    printed = _built_parts(project, parts)
    info = require_slicer(settings)
    bed = printer_bed(settings)
    pieces = [{"id": p["id"], "size": list(p.get("print_size_mm") or p["size_mm"])} for p in printed]
    groups = plates_mod.plan(pieces, bed, PLATE_SPACING)
    by_id = {p["id"]: p for p in printed}

    shutil.rmtree(project.slices_dir, ignore_errors=True)
    project.slices_dir.mkdir(parents=True)
    options = project.data.get("print") or {}
    out_plates = []
    for number, ids in enumerate(groups, start=1):
        files = [project.build_dir / "print" / f"{i}.stl" for i in ids]
        needs_support = any((by_id[i].get("orientation") or {}).get("support_needed") for i in ids)
        r = slice_files(settings, files, project.slices_dir / f"plate_{number}.gcode.3mf",
                        options, needs_support, info)
        out_plates.append({"number": number, "parts": list(ids), "file": r["file"],
                           "minutes": r["minutes"], "grams": r["grams"], "support_grams": r["support_grams"]})
    return {
        "slicer": r["slicer"], "printer": settings.printer, "material": r["material"],
        "plates": out_plates,
        "total_minutes": sum(p["minutes"] for p in out_plates),
        "total_grams": sum(p["grams"] for p in out_plates),
    }

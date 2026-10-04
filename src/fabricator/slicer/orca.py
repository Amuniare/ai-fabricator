"""OrcaSlicer: same flow as Bambu Studio, with its own command line.

UNTESTED. This was written from OrcaSlicer's documented command line and its source
(``orca-slicer --slice 0 --load-settings "m.json;p.json" --load-filaments f.json
--outputdir OUT --export-3mf name.3mf files...``). Orca reads the same profile JSON
format as Bambu Studio and, like it, needs inherited profile settings merged first, so
the profiles built in ``profiles.py`` are reused. If Orca doesn't write ``result.json``,
``bambu.run`` falls back to the totals in the gcode header. Unlike Bambu Studio,
Orca's ``--export-3mf`` may not embed gcode; the person should then open the project
and press "Slice plate" in Orca, which is a limit of that slicer.
"""

from __future__ import annotations

from pathlib import Path

from . import bambu


def command(info: dict, files: list[Path], work: Path, out: Path, name: str) -> list[str]:
    return [
        info["exe"],
        "--arrange",
        "1",
        "--load-settings",
        f"{work / 'machine.json'};{work / 'process.json'}",
        "--load-filaments",
        str(work / "filament.json"),
        "--slice",
        "0",
        "--outputdir",
        str(out),
        "--export-3mf",
        name,
        *[str(f) for f in files],
    ]


def run(
    info: dict, profiles: dict, files: list[Path], out_file: Path | None = None, timeout: int = bambu.TIMEOUT_SECONDS
) -> dict:
    return bambu.run(info, profiles, files, out_file, timeout, cmd_builder=command, label="OrcaSlicer")

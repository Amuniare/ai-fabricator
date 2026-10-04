"""Running Bambu Studio on the command line and reading what it produced.

The command, proven on Bambu Studio 02.08:

    bambu-studio --arrange 1 --load-settings "machine.json;process.json"
        --load-filaments filament.json --slice 0 --outputdir OUT
        --export-3mf name.gcode.3mf piece1.stl piece2.stl

It writes OUT/result.json (time and grams per plate), OUT/name.gcode.3mf (what the
person opens and prints) and OUT/plate_N.gcode. Pieces are already lying the way they
will print, so nothing here asks the slicer to re-orient them.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from ..project import ProjectError

TIMEOUT_SECONDS = 600

# Lines the slicer prints that mean nothing to a person.
_NOISE = re.compile(r"wayland|glfw|xdg|libEGL|MESA|dbus|gtk|gdk|pixbuf|thumbnail|fontconfig", re.I)


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


def write_profiles(profiles: dict, work: Path) -> None:
    for kind in ("machine", "process", "filament"):
        (work / f"{kind}.json").write_text(json.dumps(profiles[kind], indent=1), encoding="utf-8")


def _plain_error(text: str) -> str:
    low = text.lower()
    if "nothing to slice" in low or "empty" in low:
        return "The slicer found nothing to print in those files."
    if "too large" in low or "outside" in low or "out of" in low:
        return "Something doesn't fit on the printer's bed."
    return text.strip()


def parse_result(out: Path) -> list[dict]:
    """Per-plate [{seconds, grams}] from result.json. Raises ProjectError if the slicer reported failure."""
    f = out / "result.json"
    if not f.exists():
        return []
    data = json.loads(f.read_text(encoding="utf-8"))
    if data.get("return_code", 0) != 0:
        raise ProjectError(
            "The slicer couldn't slice this: "
            + _plain_error(str(data.get("error_string", "unknown problem")))
            + " Build it again and check the results for problems."
        )
    plates = []
    for p in data.get("sliced_plates", []):
        grams = sum(float(fil.get("total_used_g", 0)) for fil in p.get("filaments", []))
        plates.append({"seconds": float(p.get("total_predication", 0)), "grams": grams})
    return plates


def gcode_support_fraction(gcode: Path) -> float:
    """Share of the extruded plastic that went into supports (0..1).

    Bambu's gcode announces each kind of move with a comment: ``; FEATURE: Support`` and
    ``; FEATURE: Support interface`` for supports. Extrusion is relative (M83), so the E
    value on each printing move is the filament length pushed. Retractions have a negative
    E or no X/Y move and are skipped.
    """
    feature = ""
    support = total = 0.0
    move = re.compile(r"^G[123]\b")
    e_re = re.compile(r"\sE(-?\d*\.?\d+)")
    xy_re = re.compile(r"\s[XY]-?\d")
    with gcode.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith(("; FEATURE:", ";TYPE:")):  # ;TYPE: is OrcaSlicer's spelling
                feature = line.split(":", 1)[1].strip()
            elif move.match(line):
                m = e_re.search(line)
                if m and xy_re.search(line):
                    e = float(m.group(1))
                    if e > 0:
                        total += e
                        if feature.startswith("Support"):
                            support += e
    return support / total if total else 0.0


def gcode_header(gcode: Path) -> dict:
    """Total grams and seconds from the comment block at the top of a gcode file."""
    info: dict = {}
    with gcode.open(encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            if i > 400:
                break
            m = re.match(r";\s*total filament weight \[g\]\s*:\s*([\d.]+)", line)
            if m:
                info["grams"] = float(m.group(1))
            m = re.match(r";\s*estimated printing time \(normal mode\)\s*=\s*(.*)", line)
            if m:
                secs = 0
                for n, unit in re.findall(r"(\d+)([dhms])", m.group(1)):
                    secs += int(n) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
                info["seconds"] = secs
    return info


def run(
    info: dict,
    profiles: dict,
    files: list[Path],
    out_file: Path | None = None,
    timeout: int = TIMEOUT_SECONDS,
    cmd_builder=command,
    label: str = "Bambu Studio",
) -> dict:
    """Slice ``files`` together onto one bed. Returns {minutes, grams, support_grams, file, plates}."""
    for f in files:
        if not Path(f).exists():
            raise ProjectError(f"A piece to print is missing ({Path(f).name}). Build it again first.")
    with tempfile.TemporaryDirectory(prefix="fabricator-slice-") as tmp:
        work = Path(tmp)
        out = work / "out"
        out.mkdir()
        write_profiles(profiles, work)
        name = "print.gcode.3mf"
        cmd = cmd_builder(info, [Path(f) for f in files], work, out, name)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=work,
                env=dict(os.environ),
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise ProjectError(
                f"{label} took longer than {timeout // 60} minutes to slice this, so it was stopped. "
                "The piece may be very detailed; try a thicker layer height or fewer small details."
            ) from None
        except OSError as e:
            raise ProjectError(f"Couldn't start {label} ({e}). Check the install with 'fabricator doctor'.") from None
        plates = parse_result(out)
        gcodes = sorted(out.glob("plate_*.gcode"), key=lambda p: int(re.findall(r"\d+", p.stem)[0]))
        if not plates and gcodes:  # no result.json: fall back to the gcode header
            plates = [
                {"seconds": gcode_header(g).get("seconds", 0), "grams": gcode_header(g).get("grams", 0)} for g in gcodes
            ]
        threemf = out / name
        if proc.returncode != 0 or not plates or not threemf.exists():
            quiet = "\n".join(line for line in proc.stderr.splitlines() if not _NOISE.search(line))
            raise ProjectError(
                f"{label} didn't produce a print file (exit code {proc.returncode}). "
                + (quiet.strip()[-400:] or "It gave no reason.")
            )
        seconds = sum(p["seconds"] for p in plates)
        grams = sum(p["grams"] for p in plates)
        support = sum(p["grams"] * gcode_support_fraction(g) for g, p in zip(gcodes, plates, strict=False))
        result = {
            "minutes": seconds / 60,
            "grams": grams,
            "support_grams": support,
            "file": None,
            "plates": len(plates),
        }
        if out_file is not None:
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_bytes(threemf.read_bytes())
            result["file"] = str(out_file)
        return result

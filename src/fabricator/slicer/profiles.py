"""Finding the slicer and reading its printer, process and filament profiles.

Bambu Studio (and OrcaSlicer, which shares the format) keeps profiles as JSON files
that inherit from parent profiles by name. The command line does not resolve that
inheritance, so ``merged`` does it here, and the result is written out for the slicer.
"""

from __future__ import annotations

import contextlib
import difflib
import json
import os
import re
import shutil
import sys
from pathlib import Path

from ..project import ProjectError
from ..settings import Settings, load_data

KINDS = ("machine", "process", "filament")


# ---- finding the slicer -------------------------------------------------------------


def _resources_for(exe: Path, kind: str) -> Path | None:
    """The resources folder of an install, found next to the program."""
    with contextlib.suppress(OSError):
        exe = exe.resolve()
    for base in (exe.parent, exe.parent.parent, exe.parent.parent.parent):
        for sub in ("resources", "Resources"):
            if (base / sub / "profiles").is_dir():
                return base / sub
    return None


def _version(resources: Path | None) -> str | None:
    if resources is None:
        return None
    for vendor in ("BBL", "OrcaFilamentLibrary"):
        f = resources / "profiles" / f"{vendor}.json"
        if f.exists():
            try:
                return str(json.loads(f.read_text(encoding="utf-8")).get("version"))
            except (OSError, ValueError):
                pass
    return None


def _info(exe: Path, kind: str, resources: Path | None = None) -> dict:
    resources = resources or _resources_for(exe, kind)
    return {
        "exe": str(exe),
        "resources": str(resources) if resources else None,
        "kind": kind,
        "version": _version(resources),
    }


def _candidates(kind: str) -> list[Path]:
    """Places a slicer is usually installed, for one kind ("bambu" or "orca")."""
    paths: list[Path] = []
    exe_name, folder = ("bambu-studio", "Bambu Studio") if kind == "bambu" else ("orca-slicer", "OrcaSlicer")
    if sys.platform == "win32":
        for var in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(var)
            if base:
                paths.append(Path(base) / folder / f"{exe_name}.exe")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            paths.append(Path(local) / "Programs" / folder / f"{exe_name}.exe")
        paths += [Path("C:/Program Files") / folder / f"{exe_name}.exe"]
    elif sys.platform == "darwin":
        paths.append(Path("/Applications") / f"{folder}.app" / "Contents" / "MacOS" / folder)
    else:
        home = Path.home()
        for base in (home / "Applications", home / "tools", home / ".local" / "opt", Path("/opt")):
            paths += [base / folder / "bin" / exe_name, base / exe_name / "bin" / exe_name]
    found = shutil.which(exe_name)
    if found:
        paths.append(Path(found))
    return paths


def find_slicer(settings: Settings | None = None) -> dict | None:
    """Where the slicer is: {exe, resources, kind, version}, or None if not installed."""
    settings = settings or Settings.load()
    attempts: list[dict] = []

    def add(exe: str | Path | None, kind: str, resources: str | None = None) -> None:
        if exe and Path(exe).is_file():
            attempts.append(_info(Path(exe), kind, Path(resources) if resources else None))

    if settings.slicer_path:
        kind = "orca" if "orca" in Path(settings.slicer_path).name.lower() else "bambu"
        add(settings.slicer_path, kind, os.environ.get(f"FABRICATOR_{kind.upper()}_RESOURCES"))
    order = ["bambu", "orca"] if settings.slicer != "orca" else ["orca", "bambu"]
    for kind in order:
        add(os.environ.get(f"FABRICATOR_{kind.upper()}"), kind, os.environ.get(f"FABRICATOR_{kind.upper()}_RESOURCES"))
    for kind in order:
        for path in _candidates(kind):
            add(path, kind)
    usable = [a for a in attempts if a["resources"]]
    # Prefer the kind the user chose, but only one that has profiles we can read.
    for kind in order:
        for a in usable:
            if a["kind"] == kind:
                return a
    return usable[0] if usable else None


def require_slicer(settings: Settings) -> dict:
    info = find_slicer(settings)
    if info is None:
        raise ProjectError(
            "Bambu Studio isn't installed (or Fabricator can't find it). Install it free from "
            "https://bambulab.com/en/download/studio, open it once, then try again. "
            'If it is installed somewhere unusual, run: fabricator setup --slicer-path "<path to bambu-studio>"'
        )
    return info


# ---- the profile index ----------------------------------------------------------------

_INDEX: dict[str, dict] = {}


def index(resources: str | Path) -> dict:
    """{vendor: {kind: {profile name: path}}} for every profile under resources/profiles."""
    key = str(resources)
    if key in _INDEX:
        return _INDEX[key]
    result: dict[str, dict[str, dict[str, Path]]] = {}
    root = Path(resources) / "profiles"
    for vendor in sorted(p for p in root.iterdir() if p.is_dir()):
        per_kind: dict[str, dict[str, Path]] = {k: {} for k in KINDS}
        for kind in KINDS:
            for f in (vendor / kind).rglob("*.json"):
                try:
                    name = json.loads(f.read_text(encoding="utf-8")).get("name")
                except (OSError, ValueError):
                    continue
                if name:
                    per_kind[kind][name] = f
        result[vendor.name] = per_kind
    _INDEX[key] = result
    return result


def _find(resources, kind: str, name: str, vendor: str | None = None) -> tuple[str, Path] | None:
    idx = index(resources)
    for v in ([vendor] if vendor else []) + [v for v in idx if v != vendor]:
        path = idx.get(v, {}).get(kind, {}).get(name)
        if path:
            return v, path
    return None


def merged(resources, kind: str, name: str, vendor: str | None = None) -> dict:
    """A profile with everything inherited from its parents filled in (child wins)."""
    hit = _find(resources, kind, name, vendor)
    if hit is None:
        raise ProjectError(f"The slicer has no {kind} profile called '{name}'.")
    vendor, path = hit
    data = json.loads(path.read_text(encoding="utf-8"))
    parent = data.get("inherits")
    base = merged(resources, kind, parent, vendor) if parent else {}
    base.update({k: v for k, v in data.items() if k != "inherits"})
    return base


def _machine_name(settings: Settings) -> str:
    return f"{settings.printer} {settings.nozzle:g} nozzle"


# ---- printers ---------------------------------------------------------------------------


def _bed_from_yaml(settings: Settings) -> tuple[float, float, float]:
    printers = load_data("printers.yaml")["printers"]
    if settings.printer not in printers:
        raise ProjectError(
            f"Fabricator doesn't know the build size of '{settings.printer}' and the slicer isn't "
            "installed to look it up. Install Bambu Studio, then run setup again."
        )
    return tuple(float(v) for v in printers[settings.printer]["bed"])  # type: ignore[return-value]


def printer_bed(settings: Settings) -> tuple[float, float, float]:
    """Usable build volume (x, y, z) in mm, from the slicer's printer profile when possible."""
    info = find_slicer(settings)
    if info:
        try:
            m = merged(info["resources"], "machine", _machine_name(settings), "BBL")
            xs, ys = [], []
            for pt in m["printable_area"]:
                x, _, y = str(pt).partition("x")
                xs.append(float(x))
                ys.append(float(y))
            return (max(xs) - min(xs), max(ys) - min(ys), float(m["printable_height"]))
        except (ProjectError, KeyError, ValueError):
            pass
    return _bed_from_yaml(settings)


def _models(resources) -> dict[str, str]:
    """{printer model: a machine profile name} for every printer a person can pick."""
    models: dict[str, str] = {}
    for vendor, per_kind in index(resources).items():
        if vendor != "BBL":  # only Bambu printers are supported
            continue
        for name in per_kind["machine"]:
            try:
                data = merged(resources, "machine", name, vendor)
            except ProjectError:
                continue
            if str(data.get("instantiation", "")).lower() != "true":
                continue
            model = data.get("printer_model") or re.sub(r"\s+[\d.]+ nozzle$", "", name)
            if vendor != "BBL" and not model.startswith(vendor):
                model = f"{vendor} {model}"
            models.setdefault(model, name)
    return models


def list_printers(settings: Settings | None = None) -> list[str]:
    settings = settings or Settings.load()
    info = find_slicer(settings)
    if info:
        names = set(_models(info["resources"]))
        bambu = sorted(n for n in names if n.startswith("Bambu Lab"))
        # Bambu printers first: this tool is made for them.
        if bambu:
            return bambu + sorted(n for n in names if n not in bambu)
    return list(load_data("printers.yaml")["printers"])


def _norm(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "", text.lower())
    return text.removeprefix("bambulab").removeprefix("bambu")


def match_printer(text: str, settings: Settings | None = None) -> str:
    """Turn what a person typed ("a1 mini") into a printer model name ("Bambu Lab A1 mini")."""
    names = list_printers(settings)
    query = _norm(text)
    if not query:
        raise ProjectError("Which printer? For example: a1 mini")
    exact = [n for n in names if _norm(n) == query]
    if exact:
        return exact[0]
    partial = [n for n in names if query in _norm(n)]
    if len(partial) == 1:
        return partial[0]
    pool = partial or difflib.get_close_matches(text, names, n=4, cutoff=0.4) or names[:6]
    raise ProjectError(
        f"I couldn't tell which printer '{text}' is. "
        + ("Did you mean: " if partial or pool is not names else "Some printers I know: ")
        + ", ".join(pool[:6])
        + "?"
    )


# ---- choosing profiles for a project --------------------------------------------------------


def _compatible(profile: dict, machine: str) -> bool:
    if str(profile.get("instantiation", "true")).lower() == "false":
        return False
    allowed = profile.get("compatible_printers") or []
    return machine in allowed


def choose_filament(resources, settings: Settings, machine: dict) -> tuple[str, dict]:
    """(name, merged profile) of the filament for the user's material on their printer."""
    machine_name = machine["name"]
    material = settings.material.upper()
    wanted = load_data("printers.yaml")["materials"].get(material)
    idx = index(resources)["BBL"]["filament"]
    nozzle_text = f"{settings.nozzle:g} nozzle"

    def search(match) -> list[tuple[tuple, str, dict]]:
        found = []
        for name in idx:
            if not match(name):
                continue
            prof = merged(resources, "filament", name, "BBL")
            if _compatible(prof, machine_name):
                has_nozzle = " nozzle" in name
                # Prefer the variant made for this nozzle, then Bambu's own, then the plainest name.
                rank = (
                    0 if nozzle_text in name else (1 if not has_nozzle else 2),
                    0 if name.startswith("Bambu") else 1,
                    len(name),
                )
                found.append((rank, name, prof))
        return sorted(found, key=lambda t: t[0])

    found = search(lambda n: bool(wanted) and n.startswith(wanted))
    if not found:

        def right_type(n: str) -> bool:
            try:
                t = merged(resources, "filament", n, "BBL").get("filament_type", [""])
                return str(t[0] if isinstance(t, list) else t).upper() == material
            except ProjectError:
                return False

        found = search(right_type)
    if not found:
        raise ProjectError(
            f"Bambu Studio has no {settings.material} settings for the {settings.printer} with a "
            f"{settings.nozzle:g} mm nozzle (some printers can't print every material). "
            "Pick another material with 'fabricator setup --material PLA'."
        )
    return found[0][1], found[0][2]


def project_profiles(
    settings: Settings, print_options: dict | None = None, supports: bool = False, info: dict | None = None
) -> dict:
    """Merged machine, process and filament profiles for this printer and material.

    ``print_options`` is project.yaml's ``print:`` block (strength, supports, layer).
    Returns {machine, process, filament, filament_name, density, diameter, resources}.
    """
    info = info or require_slicer(settings)
    resources = info["resources"]
    machine_name = _machine_name(settings)
    if _find(resources, "machine", machine_name) is None:
        raise ProjectError(
            f"The slicer has no profile for {settings.printer} with a {settings.nozzle:g} mm nozzle. "
            "Run 'fabricator printers' to see what is available, or change the nozzle with 'fabricator setup'."
        )
    machine = merged(resources, "machine", machine_name, "BBL")
    process_name = machine.get("default_print_profile")
    if not process_name:
        raise ProjectError(f"The slicer has no standard print settings for {machine_name}.")
    process = merged(resources, "process", process_name, "BBL")
    fil_name, filament = choose_filament(resources, settings, machine)

    opts = print_options or {}
    strength = str(opts.get("strength", "normal")).lower()
    if strength not in ("normal", "strong", "extra"):
        raise ProjectError("print: strength must be normal, strong or extra.")
    if strength == "strong":
        process.update(wall_loops="3", sparse_infill_density="25%")
    elif strength == "extra":
        process.update(wall_loops="4", sparse_infill_density="40%")
    if opts.get("layer"):
        process["layer_height"] = f"{float(opts['layer']):g}"
    mode = str(opts.get("supports", "auto")).lower()
    if mode not in ("auto", "on", "off"):
        raise ProjectError("print: supports must be auto, on or off.")
    if mode == "on" or (mode == "auto" and supports):
        process.update(enable_support="1", support_type="tree(auto)", support_style="default")
    elif mode == "off":
        process["enable_support"] = "0"

    for prof in (machine, process, filament):
        prof["from"] = "system"
    try:
        density = float(filament["filament_density"][0])
        diameter = float(filament["filament_diameter"][0])
    except (KeyError, IndexError, ValueError):
        density, diameter = 1.24, 1.75
    return {
        "machine": machine,
        "process": process,
        "filament": filament,
        "filament_name": fil_name,
        "density": density,
        "diameter": diameter,
        "resources": resources,
    }

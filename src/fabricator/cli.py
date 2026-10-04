"""The ``fabricator`` command. Claude runs these; people can too.

Every command prints a short plain-language result. Add ``--json`` for the full
structured result.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

from . import readme
from .project import Project, ProjectError
from .settings import Settings, home


def _out(args, text: str, data: dict | list | None = None) -> None:
    if getattr(args, "json", False) and data is not None:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(text)


def _fmt_minutes(minutes: float) -> str:
    h, m = divmod(round(minutes), 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


# ---- setup -----------------------------------------------------------------------


def cmd_doctor(args) -> int:
    from . import doctor

    results = doctor.run()
    lines = ["Fabricator system check", ""]
    for name, status, detail in results:
        label = {"ok": "OK", "missing": "MISSING", "optional": "not installed (optional)", "warn": "CHECK"}[status]
        lines.append(f"  {name:.<28} {label}" + (f"  {detail}" if detail else ""))
    bad = [r for r in results if r[1] == "missing"]
    lines += ["", "System ready." if not bad else "Fix the items marked MISSING, then run this again."]
    _out(args, "\n".join(lines), [{"item": n, "status": s, "detail": d} for n, s, d in results])
    return 1 if bad else 0


def cmd_setup(args) -> int:
    from . import slicer

    s = Settings.load()
    if args.printer:
        s.printer = slicer.match_printer(args.printer, s)
    if args.nozzle:
        s.nozzle = float(args.nozzle)
    if args.material:
        s.material = args.material.upper()
    if args.slicer:
        s.slicer = args.slicer
    if args.slicer_path:
        s.slicer_path = args.slicer_path
    s.configured = True
    path = s.save()
    bed = slicer.printer_bed(s)
    fits = s.fits()
    text = (
        f"Saved: {s.printer}, {s.nozzle} mm nozzle, {s.material}.\n"
        f"Build volume: {bed[0]:g} x {bed[1]:g} x {bed[2]:g} mm.\n"
        f"Fit gaps per side: " + ", ".join(f"{k} {v:.2f} mm" for k, v in fits.items()) + "\n"
        f"Settings file: {path}"
    )
    _out(
        args,
        text,
        {
            "settings": str(path),
            "printer": s.printer,
            "nozzle": s.nozzle,
            "material": s.material,
            "bed_mm": bed,
            "fits": fits,
        },
    )
    return 0


def cmd_printers(args) -> int:
    from . import slicer

    names = slicer.list_printers(Settings.load())
    _out(args, "Printers the slicer knows:\n" + "\n".join(f"  {n}" for n in names), names)
    return 0


# ---- projects ----------------------------------------------------------------------


def cmd_new(args) -> int:
    params = {}
    for item in args.param or []:
        key, _, value = item.partition("=")
        params[key.strip()] = _parse_value(value)
    project = Project.create(args.name, args.description or "", params or None, example=args.example)
    _out(
        args,
        f"Created project '{project.name}' at {project.path}\n"
        f'Edit design.py and project.yaml there, then run: fabricator build "{project.path.name}"',
        {"path": str(project.path), "name": project.name},
    )
    return 0


def cmd_list(args) -> int:
    projects = Project.all()
    if not projects:
        _out(args, f"No projects yet. Projects folder: {home()}", [])
        return 0
    rows = []
    for p in projects:
        versions = p.versions()
        rows.append(
            {
                "folder": p.path.name,
                "name": p.name,
                "versions": len(versions),
                "last": versions[-1]["date"] if versions else None,
            }
        )
    text = "\n".join(f"  {r['folder']:<32} {r['name']}  ({r['versions']} versions)" for r in rows)
    _out(args, f"Projects in {home()}:\n{text}", rows)
    return 0


def cmd_param(args) -> int:
    project = Project.find(args.project)
    data = project.data
    params = data.setdefault("parameters", {})
    if not args.set:
        _out(args, "\n".join(f"  {k} = {v}" for k, v in params.items()), params)
        return 0
    for item in args.set:
        key, _, value = item.partition("=")
        params[key.strip()] = _parse_value(value)
    project.save(data)
    _out(args, "Updated: " + ", ".join(args.set), params)
    return 0


def cmd_measure(args) -> int:
    """Record a measurement the user gave, so it isn't lost in the chat."""
    project = Project.find(args.project)
    data = project.data
    data.setdefault("measurements", {})[args.what] = {
        "value": _parse_value(args.value),
        "unit": args.unit,
        "how": args.how,
    }
    project.save(data)
    _out(args, f"Recorded {args.what} = {args.value} {args.unit} ({args.how}).", data["measurements"])
    return 0


def _parse_value(value: str):
    value = value.strip()
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    return value


# ---- build and look ------------------------------------------------------------------


def cmd_build(args) -> int:
    from .build import DesignError, build

    project = Project.find(args.project)
    try:
        result = build(project, note=args.note, save=not args.no_save, pictures=not args.no_pictures)
    except DesignError as e:
        where = f" (design.py line {e.line})" if e.line else ""
        text = f"BUILD FAILED{where}\n{e}\n\nDetails for fixing:\n{e.details[-3000:]}"
        _out(args, text, {"status": "error", "error": str(e), "line": e.line, "details": e.details})
        return 2
    s = result.summary
    lines = [f"BUILD {result.status.upper()}: {s['name']}"]
    printed = [p for p in s["parts"] if p["printed"]]
    lines.append(f"{len(printed)} printed piece(s) for {s['printer']} ({s['material']}):")
    for p in printed:
        size = " x ".join(f"{v:g}" for v in p.get("print_size_mm", p["size_mm"]))
        how = p.get("orientation", {}).get("label", "")
        lines.append(f"  {p['id']} {p['name']}: {size} mm as printed; {how}")
    if s["hardware"]:
        lines.append("Hardware: " + ", ".join(f"{q} x {n}" for n, q in s["hardware"].items()))
    for m in s["messages"]:
        lines.append(f"Note: {m}")
    problems = [c for c in result.checks if c.status != "pass"]
    lines.append(
        f"Checks: {sum(c.status == 'pass' for c in result.checks)} passed, "
        f"{sum(c.status == 'warn' for c in result.checks)} warnings, "
        f"{sum(c.status == 'fail' for c in result.checks)} failed."
    )
    for c in problems:
        part = f"{c.part}: " if c.part else ""
        fix = f" Fix: {c.fix}" if c.fix else ""
        lines.append(f"  [{c.status.upper()}] {part}{c.message}{fix}")
    if result.pictures:
        lines.append("Pictures: " + ", ".join(str(p) for p in result.pictures))
    if result.version:
        lines.append(f"Saved as version {result.version['number']}: {result.version['label']}")
    elif result.status == "fail":
        lines.append("Not saved as a version, because some checks failed.")
    data = dict(s, checks=[c.to_dict() for c in result.checks])
    _out(args, "\n".join(lines), data)
    return 1 if result.status == "fail" else 0


def cmd_check(args) -> int:
    from .build import load_built

    project = Project.find(args.project)
    load_built(project)
    data = json.loads((project.build_dir / "checks.json").read_text(encoding="utf-8"))
    lines = [f"Checks: {data['status'].upper()}"]
    for c in data["checks"]:
        if args.all or c["status"] != "pass":
            lines.append(f"  [{c['status'].upper()}] {c.get('part', '') and c['part'] + ': '}{c['message']}")
    _out(args, "\n".join(lines), data)
    return 0


def cmd_show(args) -> int:
    from . import render

    project = Project.find(args.project)
    paths = render.pictures(
        project,
        parts=args.part,
        exploded=args.exploded,
        views=args.view,
        hide=args.hide,
        transparent=args.transparent,
        section=args.section,
    )
    _out(args, "Pictures:\n" + "\n".join(f"  {p}" for p in paths), [str(p) for p in paths])
    return 0


def cmd_view(args) -> int:
    from . import viewer

    project = Project.find(args.project)
    path = viewer.make(project, open_browser=not args.no_open)
    _out(args, f"3D viewer: {path}", {"path": str(path)})
    return 0


def cmd_parts(args) -> int:
    from .build import load_built

    project = Project.find(args.project)
    s = load_built(project)
    lines = []
    for p in s["parts"]:
        kind = "" if p["printed"] else " (reference, not printed)"
        lines.append(f"  {p['id']} {p['name']}{kind}")
    if s["joints"]:
        lines.append("How they join:")
        for j in s["joints"]:
            hw = f", using {', '.join(j['hardware'])}" if j["hardware"] else ""
            note = f" — {j['note']}" if j.get("note") else ""
            lines.append(f"  {j['a']} + {j['b']}: {j['kind']} ({j['fit']} fit){hw}{note}")
    _out(args, "\n".join(lines), {"parts": s["parts"], "joints": s["joints"]})
    return 0


# ---- slicing -------------------------------------------------------------------------


def cmd_slice(args) -> int:
    from . import slicer

    project = Project.find(args.project)
    result = slicer.slice_project(project, Settings.load(), parts=args.part)
    (project.slices_dir / readme.SLICE_SUMMARY).write_text(json.dumps(result, indent=2), encoding="utf-8")
    readme.write(project)
    lines = [f"Sliced with {result['slicer']} for {result['printer']} ({result['material']}):"]
    for plate in result["plates"]:
        support = f", supports {plate['support_grams']:.0f} g" if plate.get("support_grams") else ""
        lines.append(
            f"  Plate {plate['number']}: {', '.join(plate['parts'])} — "
            f"{_fmt_minutes(plate['minutes'])}, {plate['grams']:.0f} g{support}"
        )
        lines.append(f"    Print file: {plate['file']}")
    lines.append(
        f"Total: {_fmt_minutes(result['total_minutes'])}, {result['total_grams']:.0f} g "
        f"across {len(result['plates'])} plate(s)."
    )
    lines.append("Open a print file in Bambu Studio and press Print.")
    _out(args, "\n".join(lines), result)
    return 0


def cmd_compare(args) -> int:
    from . import compare

    project = Project.find(args.project)
    result = compare.compare(project, Settings.load(), part=args.part, what=args.what)
    lines = [f"Compared {len(result['options'])} options by slicing each one:"]
    for o in result["options"]:
        lines.append(
            f"  {o['label']}: {_fmt_minutes(o['minutes'])}, {o['grams']:.0f} g, "
            f"supports {o.get('support_grams', 0):.0f} g"
        )
    lines.append(f"Recommended: {result['recommended']} — {result['reason']}")
    _out(args, "\n".join(lines), result)
    return 0


# ---- history ---------------------------------------------------------------------------


def cmd_versions(args) -> int:
    project = Project.find(args.project)
    versions = project.versions()
    text = "\n".join(f"  v{v['number']}  {v['date']}  {v['label']}" for v in versions) or "  No versions yet."
    _out(args, f"Versions of {project.name}:\n{text}", versions)
    return 0


def cmd_restore(args) -> int:
    project = Project.find(args.project)
    v = project.restore_version(args.number)
    _out(args, f"Went back to version {args.number}, saved as v{v['number']}. Run 'fabricator build' to remake it.", v)
    return 0


def cmd_diff(args) -> int:
    import yaml

    project = Project.find(args.project)
    a = yaml.safe_load(project.file_at_version(args.a, "project.yaml")) or {}
    b = yaml.safe_load(project.file_at_version(args.b, "project.yaml")) or {}
    pa, pb = a.get("parameters", {}), b.get("parameters", {})
    changes = [
        {"parameter": k, "from": pa.get(k), "to": pb.get(k)}
        for k in sorted(set(pa) | set(pb))
        if pa.get(k) != pb.get(k)
    ]
    design_changed = project.file_at_version(args.a, "design.py") != project.file_at_version(args.b, "design.py")
    lines = [f"Version {args.a} to {args.b}:"]
    lines += [f"  {c['parameter']}: {c['from']} -> {c['to']}" for c in changes] or ["  No parameter changes."]
    lines.append("  The design code changed too." if design_changed else "  The design code is the same.")
    _out(args, "\n".join(lines), {"parameters": changes, "design_changed": design_changed})
    return 0


# ---- outside information and feedback ------------------------------------------------------


def cmd_source(args) -> int:
    from . import sources

    project = Project.find(args.project)
    rec = sources.add_source(
        project,
        what=args.what,
        value=args.value,
        unit=args.unit,
        url=args.url,
        kind=args.kind,
        confidence=args.confidence,
        note=args.note,
    )
    _out(args, f"Recorded {args.what} = {args.value} {args.unit} from {args.kind} source.", rec)
    return 0


def cmd_import(args) -> int:
    from . import sources

    project = Project.find(args.project)
    rec = sources.import_file(
        project,
        Path(args.file),
        name=args.name,
        url=args.url,
        author=args.author,
        license=args.license,
        confidence=args.confidence,
    )
    _out(
        args,
        f"Imported '{rec['name']}' ({rec['format']}). Use it in design.py as ctx.imported(\"{rec['name']}\").",
        rec,
    )
    return 0


def cmd_feedback(args) -> int:
    from . import feedback

    project = Project.find(args.project)
    result = feedback.record(project, Settings.load(), fit=args.fit, result=args.result, part=args.part, note=args.note)
    text = result["message"]
    if result.get("suggestions"):
        text += "\n" + "\n".join(f"  - {idea}" for idea in result["suggestions"])
    _out(args, text, result)
    return 0


def cmd_library(args) -> int:
    from . import library

    text, data = library.describe(args.item)
    _out(args, text, data)
    return 0


def cmd_package(args) -> int:
    from . import package

    project = Project.find(args.project)
    path = package.make(project, Settings.load(), slice_plates=not args.no_slice)
    _out(args, f"Finished package: {path}", {"path": str(path)})
    return 0


def cmd_update(args) -> int:
    from . import doctor

    _out(args, doctor.update())
    return 0


# ---- argument parsing ----------------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fabricator", description="Describe it, look at it, print it.")
    p.add_argument("--json", action="store_true", help="print full results as JSON")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, func, help):
        sp = sub.add_parser(name, help=help)
        sp.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
        sp.set_defaults(func=func)
        return sp

    def project_arg(sp, required=False):
        sp.add_argument("project", nargs=None if required else "?", help="project name or folder (default: latest)")

    add("doctor", cmd_doctor, "check that everything is installed")
    sp = add("setup", cmd_setup, "save printer, nozzle and material")
    sp.add_argument("--printer")
    sp.add_argument("--nozzle")
    sp.add_argument("--material")
    sp.add_argument("--slicer", choices=["bambu", "orca"])
    sp.add_argument("--slicer-path")
    add("printers", cmd_printers, "list printers the slicer knows")
    add("update", cmd_update, "get the latest version of this tool")

    sp = add("new", cmd_new, "start a project")
    sp.add_argument("name")
    sp.add_argument("--description", "-d")
    sp.add_argument("--param", "-p", action="append", help="name=value, repeatable")
    sp.add_argument("--example", "-e", help="start from an example in the examples folder")
    add("list", cmd_list, "list projects")
    sp = add("param", cmd_param, "show or change parameters")
    project_arg(sp)
    sp.add_argument("--set", "-s", action="append", help="name=value, repeatable")
    sp = add("measure", cmd_measure, "record a measurement")
    project_arg(sp)
    sp.add_argument("--what", required=True)
    sp.add_argument("--value", required=True)
    sp.add_argument("--unit", default="mm")
    sp.add_argument("--how", default="measured by user")

    sp = add("build", cmd_build, "build, check and picture the design")
    project_arg(sp)
    sp.add_argument("--note", "-m", help="what changed, for the version list")
    sp.add_argument("--no-save", action="store_true")
    sp.add_argument("--no-pictures", action="store_true")
    sp = add("check", cmd_check, "show the last build's check results")
    project_arg(sp)
    sp.add_argument("--all", action="store_true")
    sp = add("show", cmd_show, "make pictures of the model or chosen parts")
    project_arg(sp)
    sp.add_argument("--part", action="append", help="only these parts (id or name), repeatable")
    sp.add_argument("--hide", action="append", help="hide these parts, repeatable")
    sp.add_argument("--exploded", action="store_true")
    sp.add_argument("--transparent", action="store_true")
    sp.add_argument("--section", choices=["x", "y", "z"], help="cut through the middle")
    sp.add_argument(
        "--view",
        action="append",
        choices=["front", "back", "left", "right", "top", "bottom", "angled", "print", "plates"],
    )
    sp = add("view", cmd_view, "open an interactive 3D view in the browser")
    project_arg(sp)
    sp.add_argument("--no-open", action="store_true")
    sp = add("parts", cmd_parts, "list parts and how they join")
    project_arg(sp)

    sp = add("slice", cmd_slice, "slice into print files and report time and grams")
    project_arg(sp)
    sp.add_argument("--part", action="append")
    sp = add("compare", cmd_compare, "slice several options and recommend one")
    project_arg(sp)
    sp.add_argument("--part")
    sp.add_argument("--what", choices=["orientation", "split"], default="orientation")

    sp = add("versions", cmd_versions, "list saved versions")
    project_arg(sp)
    sp = add("restore", cmd_restore, "go back to a saved version")
    sp.add_argument("number", type=int)
    project_arg(sp)
    sp = add("diff", cmd_diff, "compare two versions")
    sp.add_argument("a", type=int)
    sp.add_argument("b", type=int)
    project_arg(sp)

    sp = add("source", cmd_source, "record where an outside measurement came from")
    project_arg(sp)
    sp.add_argument("--what", required=True)
    sp.add_argument("--value", required=True)
    sp.add_argument("--unit", default="mm")
    sp.add_argument("--url", required=True)
    sp.add_argument(
        "--kind",
        default="manufacturer",
        choices=["manufacturer", "datasheet", "standard", "retailer", "community", "measured"],
    )
    sp.add_argument("--confidence", default="high", choices=["high", "medium", "low"])
    sp.add_argument("--note", default="")
    sp = add("import", cmd_import, "bring in a STEP or STL file as reference geometry")
    sp.add_argument("file")
    project_arg(sp)
    sp.add_argument("--name")
    sp.add_argument("--url", default="")
    sp.add_argument("--author", default="")
    sp.add_argument("--license", default="unknown")
    sp.add_argument("--confidence", default="medium", choices=["high", "medium", "low"])
    sp = add("feedback", cmd_feedback, "record how a print turned out")
    project_arg(sp)
    sp.add_argument("--fit", choices=["press", "snug", "sliding", "loose", "hole"])
    sp.add_argument("--result", choices=["too-tight", "too-loose", "good"])
    sp.add_argument("--part")
    sp.add_argument("--note", default="")
    sp = add("library", cmd_library, "list built-in hardware (screws, nuts, inserts, magnets, bearings)")
    sp.add_argument("item", nargs="?")
    sp = add("package", cmd_package, "make the finished folder with parts, print files and a guide")
    project_arg(sp)
    sp.add_argument("--no-slice", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to a narrower encoding
    args = parser().parse_args(argv)
    try:
        return args.func(args) or 0
    except ProjectError as e:
        _out(args, f"Problem: {e}", {"status": "error", "error": str(e)})
        return 2


if __name__ == "__main__":
    sys.exit(main())

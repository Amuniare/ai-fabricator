"""Each project's README.md: one page saying what the object is, with its pictures.

Remade after every build and slice from project.yaml, build/model.json and
slices/summary.json, so it is never edited by hand (the user's own words go in notes.md).
VS Code shows it with the pictures when opened with "Open Preview".
"""

from __future__ import annotations

import json
from pathlib import Path

from .project import Project

README_FILE = "README.md"
SLICE_SUMMARY = "summary.json"
PICTURES = [
    ("overview.png", "How it looks"),
    ("exploded.png", "How the pieces go together"),
    ("print.png", "How each piece lies on the printer bed"),
]


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _minutes(m: float) -> str:
    h, mm = divmod(round(m), 60)
    return f"{h} h {mm:02d} min" if h else f"{mm} min"


def _size(mm: list[float]) -> str:
    return " × ".join(f"{v:.0f}" for v in mm) + " mm"


def _older(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_mtime < b.stat().st_mtime
    except OSError:
        return False


def write(project: Project) -> Path:
    data = project.data
    model = _read_json(project.build_dir / "model.json")
    sliced = _read_json(project.slices_dir / SLICE_SUMMARY)
    lines = [f"# {data.get('name', project.path.name)}", ""]
    lines += ["*This page is remade after every build. Your own notes go in [notes.md](notes.md).*", ""]
    if data.get("request"):
        lines += [f"> {data['request']}", ""]

    if model is None:
        lines += ["Not built yet.", ""]
    else:
        printed = [p for p in model["parts"] if p["printed"]]
        status = {"pass": "all checks passed", "warn": "passed with warnings", "fail": "has failed checks"}
        lines += [
            f"{len(printed)} printed piece(s) for {model['printer']} with {model['material']}, "
            f"{status.get(model['status'], model['status'])}.",
            "",
        ]
        for file, caption in PICTURES:
            if (project.build_dir / "pictures" / file).exists():
                lines += [f"**{caption}**", "", f"![{caption}](build/pictures/{file})", ""]
        lines += ["## Pieces", "", "| Piece | Name | Size |", "| --- | --- | --- |"]
        lines += [f"| {p['id']} | {p['name']} | {_size(p['size_mm'])} |" for p in printed]
        lines.append("")
        if model.get("hardware"):
            lines += ["## Bought parts", ""]
            lines += [f"- {name} × {count}" for name, count in model["hardware"].items()]
            lines.append("")

    if sliced:
        lines += ["## Printing", ""]
        if model is not None and _older(project.slices_dir / SLICE_SUMMARY, project.build_dir / "model.json"):
            lines += ["*These times are from before the last build. Slice again for current ones.*", ""]
        for plate in sliced["plates"]:
            lines.append(
                f"- Plate {plate['number']} ({', '.join(plate['parts'])}): {_minutes(plate['minutes'])}, "
                f"{plate['grams']:.0f} g — [print file](slices/{Path(plate['file']).name})"
            )
        lines += [
            f"- **Total: {_minutes(sliced['total_minutes'])}, {sliced['total_grams']:.0f} g**",
            "",
            "Double-click a print file to open it in Bambu Studio, then press Print.",
            "",
        ]

    if data.get("parameters"):
        lines += ["## Sizes you can change", "", "| Setting | Value |", "| --- | --- |"]
        lines += [f"| {k} | {v} |" for k, v in data["parameters"].items()]
        lines.append("")

    versions = project.versions()
    if versions:
        lines += ["## Versions", ""]
        lines += [f"{v['number']}. {v['label']} ({v['date']})" for v in versions]
        lines.append("")

    path = project.path / README_FILE
    path.write_text("\n".join(lines), encoding="utf-8")
    return path

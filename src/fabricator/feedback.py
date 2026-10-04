"""Learning from real prints: "too tight", "too loose", or a free-text note."""

from __future__ import annotations

import json
from datetime import date

import yaml

from .project import Project, ProjectError
from .settings import Settings, home, load_data

SUGGESTIONS = [
    "Make the walls thicker around the weak spot (screw holes and thin posts break first).",
    "Print with more wall loops (4 instead of 2) so the part is stronger there.",
    "Change the print orientation so the layers don't run across the weak spot; "
    "layers split apart far more easily than they snap.",
    "Add a fillet (rounded inside corner) where it cracked, to spread the force.",
    "Use a slightly larger screw hole or a screw with a smaller head so tightening doesn't wedge the part.",
]


def _joint_fits(project: Project, part: str | None) -> list[str]:
    path = project.build_dir / "model.json"
    if not path.exists():
        return []
    try:
        joints = json.loads(path.read_text(encoding="utf-8")).get("joints", [])
    except (OSError, ValueError):
        return []
    found: list[str] = []
    for j in joints:
        involved = part is None or part.lower() in (str(j.get("a", "")).lower(), str(j.get("b", "")).lower())
        if involved and j.get("fit") and j["fit"] not in found:
            found.append(j["fit"])
    return found


def _printer_short(settings: Settings) -> str:
    # "Bambu Lab A1 mini" -> "A1 mini"
    return settings.printer.replace("Bambu Lab ", "")


def record(
    project: Project,
    settings: Settings,
    fit: str | None = None,
    result: str | None = None,
    part: str | None = None,
    note: str = "",
) -> dict:
    entry = {"date": date.today().isoformat(), "part": part, "fit": fit, "result": result, "note": note}
    out: dict = {}

    if result in ("too-tight", "too-loose"):
        if not fit:
            fits = _joint_fits(project, part)
            if len(fits) == 1:
                fit = fits[0]
            elif not fits:
                raise ProjectError("Which fit was it? Say one of: press, snug, sliding, loose, hole.")
            else:
                raise ProjectError(
                    "That part has more than one kind of fit: "
                    + ", ".join(fits)
                    + ". Which one was too "
                    + ("tight" if result == "too-tight" else "loose")
                    + "?"
                )
        gaps = settings.fits()
        if fit not in gaps:
            raise ProjectError(f"Unknown fit '{fit}'. Known fits: {', '.join(gaps)}.")
        step = float(load_data("fits.yaml").get("feedback_step", 0.05))
        old = gaps[fit]
        new = round(max(0.0, old + (step if result == "too-tight" else -step)), 3)
        settings.fit_overrides.setdefault(settings.machine_key, {})[fit] = new
        settings.save()
        entry.update(fit=fit, old_gap=old, new_gap=new)
        if new == old:
            message = (
                f"Got it. The {fit} gap is already at 0 mm on your {_printer_short(settings)} "
                f"with {settings.material}, so it can't go any tighter."
            )
        else:
            message = (
                f"Got it. {fit.capitalize()} fits on your {_printer_short(settings)} with "
                f"{settings.material} now use a {new:g} mm gap instead of {old:g} mm. "
                f"Rebuild to update this design."
            )
        out.update(fit=fit, old_gap=old, new_gap=new)
    elif result == "good":
        entry.update(fit=fit)
        what = f"the {fit} fit" if fit else "that print"
        message = f"Good to hear. Noted that {what} worked well; no settings changed."
        out["changed"] = False
    else:
        message = "Noted. Here are some ideas to talk through."
        out["suggestions"] = list(SUGGESTIONS)
        if not note:
            raise ProjectError("Tell me what happened, or say whether it was too tight, too loose or good.")

    # project.yaml
    data = project.data
    data.setdefault("feedback", []).append(entry)
    project.save(data)
    # global log
    log = home() / "feedback-log.yaml"
    items = (yaml.safe_load(log.read_text(encoding="utf-8")) or []) if log.exists() else []
    items.append(
        {
            **entry,
            "project": project.path.name,
            "printer": settings.printer,
            "nozzle": settings.nozzle,
            "material": settings.material,
        }
    )
    log.write_text(yaml.safe_dump(items, sort_keys=False, allow_unicode=True), encoding="utf-8")

    out["message"] = message
    out["entry"] = entry
    return out

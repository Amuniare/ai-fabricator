"""System check and self-update. Nothing here may crash: every check reports instead."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[2]


def _check(name, fn):
    try:
        status, detail = fn()
    except Exception as e:  # a check failing is a result, not an error
        status, detail = "warn", f"Could not check this ({type(e).__name__}: {e})"
    return (name, status, detail)


def _python():
    v = sys.version_info
    text = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) == (3, 12):
        return "ok", text
    return "warn", f"{text} (Fabricator is made for 3.12; run 'uv sync' to get it)"


def _cad():
    from build123d import Box

    vol = Box(10, 10, 10).volume
    if abs(vol - 1000) > 1e-3:
        return "missing", f"The CAD engine gave a wrong answer ({vol}). Run setup again."
    return "ok", "build123d works"


def _mesh():
    import manifold3d  # noqa: F401
    import trimesh

    return "ok", f"trimesh {trimesh.__version__} with manifold3d"


def _git():
    exe = shutil.which("git")
    if not exe:
        return "missing", "Git is needed to save versions. Run setup again, or install it from git-scm.com"
    return "ok", exe


def _slicer_info(settings):
    try:
        from . import slicer

        return slicer.find_slicer(settings)
    except Exception:
        return None


def _field(info, *names):
    for n in names:
        v = info.get(n) if isinstance(info, dict) else getattr(info, n, None)
        if v:
            return v
    return None


def _settings():
    from .settings import Settings

    return Settings.load()


def _bambu():
    s = _settings()
    info = _slicer_info(s)
    if info is not None and _field(info, "kind") in (None, "bambu"):
        return "ok", str(_field(info, "exe", "path", "executable") or "found")
    # find_slicer may have returned OrcaSlicer only; look for Bambu directly
    exe = os.environ.get("FABRICATOR_BAMBU")
    if exe and Path(exe).exists():
        return "ok", exe
    return "missing", "Install Bambu Studio from bambulab.com"


def _profile():
    s = _settings()
    try:
        from . import slicer

        bed = slicer.printer_bed(s)
    except Exception as e:
        return "warn", f"Could not read the profile for {s.printer} ({e})"
    if _slicer_info(s) is None:
        return "warn", f"{s.printer}: using built-in sizes (the slicer profile could not be read)"
    return "ok", f"{s.printer}, bed {bed[0]:g} x {bed[1]:g} x {bed[2]:g} mm"


def _configured():
    s = _settings()
    if s.configured:
        return "ok", f"{s.printer}, {s.nozzle:g} mm nozzle, {s.material}"
    return "warn", "Claude will ask about your printer"


def _projects():
    from .settings import home

    folder = home()
    probe = folder / ".write-test"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()
    return "ok", str(folder)


def _orca():
    s = _settings()
    info = _slicer_info(s)
    if info is not None and _field(info, "kind") == "orca":
        return "ok", str(_field(info, "exe", "path", "executable") or "found")
    for p in (os.environ.get("FABRICATOR_ORCA"), r"C:\Program Files\OrcaSlicer\orca-slicer.exe"):
        if p and Path(p).exists():
            return "ok", p
    return "optional", "Only needed if you prefer it to Bambu Studio"


def _vscode_ext():
    found = sorted((Path.home() / ".vscode" / "extensions").glob("anthropic.claude-code-*"))
    if found:
        return "ok", found[-1].name
    return "optional", "Install it in VS Code (Extensions, search 'Claude Code'), or run setup again"


def _internet():
    req = urllib.request.Request("https://github.com", method="HEAD")
    try:
        urllib.request.urlopen(req, timeout=3).close()
        return "ok", "reachable"
    except Exception:
        return "optional", "Not reachable; needed only for updates and looking things up"


def run() -> list[tuple[str, str, str]]:
    checks = [
        ("Python", _python), ("CAD engine", _cad), ("Mesh tools", _mesh), ("Git", _git),
        ("Bambu Studio", _bambu), ("Printer profile", _profile), ("Settings saved", _configured),
        ("Projects folder", _projects), ("OrcaSlicer", _orca),
        ("Claude Code in VS Code", _vscode_ext), ("Internet", _internet),
    ]
    return [_check(n, f) for n, f in checks]


# ---- update -------------------------------------------------------------------------------

def _find_uv() -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    for base in (Path.home() / ".local" / "bin", Path(os.environ.get("USERPROFILE", "~")).expanduser() / ".local" / "bin"):
        for exe in ("uv.exe", "uv"):
            if (base / exe).exists():
                return str(base / exe)
    return None


def _run(cmd: list[str]) -> tuple[int, str]:
    r = subprocess.run(cmd, cwd=TOOL_DIR, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600)
    return r.returncode, (r.stdout + r.stderr).strip()


def update() -> str:
    try:
        if not (TOOL_DIR / ".git").exists():
            return ("This copy of Fabricator was downloaded as a ZIP, so it can't update itself.\n"
                    "To update: download the newest ZIP from the project page on GitHub, unzip it, "
                    "put the new folder where the old one was (or beside it), and double-click "
                    "setup.bat in the new folder. Your projects are kept separately in "
                    f"{_projects_path()} and are not touched by this, so they are safe.")
        if not shutil.which("git"):
            return "Git isn't installed, so I can't update. Run setup.bat again to install it."
        code, out = _run(["git", "pull", "--ff-only"])
        if code != 0:
            return ("The update didn't go through. If you changed files in the Fabricator folder, "
                    "that can block it. Details:\n" + out)
        already = "Already up to date" in out
        uv = _find_uv()
        if uv is None:
            return ("Downloaded the update, but couldn't find 'uv' to finish. Run setup.bat again.")
        code, sync_out = _run([uv, "sync"])
        if code != 0:
            return "Downloaded the update, but installing its pieces failed:\n" + sync_out
        return ("You already have the newest version." if already else
                "Updated to the newest version. Your projects were not touched.")
    except Exception as e:
        return f"The update could not be completed ({type(e).__name__}: {e})."


def _projects_path() -> str:
    try:
        from .settings import home

        return str(home())
    except Exception:
        return "your Fabricator Projects folder"

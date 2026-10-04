"""Project folders: one per object, holding the request, measurements and design code.

Layout of a project folder::

    garage-remote-bracket/
        project.yaml     what was asked for, parameters, measurements, notes
        design.py        the design code (the real source of the object)
        sources.yaml     where outside measurements and files came from
        notes.md         the user's own notes about this object (Claude reads it)
        README.md        a page about the object with its pictures, remade after every build
        imports/         downloaded or supplied model files (data only, never run)
        build/           everything the build makes (pictures, parts, checks)
        slices/          sliced print files
        package/         the finished, tidy folder to keep or share

Saved versions use git inside the project folder. Only the source files are saved;
anything in build/, slices/ and package/ can be remade from them.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from . import settings as settings_mod

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"
PROJECT_FILE = "project.yaml"
DESIGN_FILE = "design.py"
NOTES_FILE = "notes.md"
GENERATED = ["build/", "slices/", "package/", "README.md", "__pycache__/", "*.pyc"]

NOTES_TEMPLATE = """# Notes: {name}

Write anything about this object here: why you made it, what you changed, how the print
came out. Claude reads this file when you work on this project, and adds to it when you
ask it to remember something.

"""

DESIGN_TEMPLATE = '''"""{name}

{description}
"""

from fabricator.design import *


def build(p, ctx):
    model = Model("{name}")

    body = Box(p.width, p.depth, p.height)
    body = fillet(body.edges().filter_by(Axis.Z), radius=p.corner_radius)
    model.add(body, "Body")

    return model
'''


class ProjectError(Exception):
    """A problem worth showing the user in plain words."""


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "project"


@dataclass
class Project:
    path: Path

    # ---- finding ------------------------------------------------------------
    @classmethod
    def find(cls, name_or_path: str | None) -> Project:
        """Find a project by folder name, display name, part of a name, or path."""
        root = settings_mod.home()
        if not name_or_path:
            latest = cls.latest()
            if latest is None:
                raise ProjectError("There are no projects yet. Start one with 'fabricator new'.")
            return latest
        direct = Path(name_or_path).expanduser()
        if (direct / PROJECT_FILE).exists():
            return cls(direct.resolve())
        candidates = cls.all()
        slug = slugify(name_or_path)
        for p in candidates:
            if p.path.name == slug or p.name.lower() == name_or_path.lower():
                return p
        loose = [p for p in candidates if slug in p.path.name or name_or_path.lower() in p.name.lower()]
        if len(loose) == 1:
            return loose[0]
        if len(loose) > 1:
            names = ", ".join(p.path.name for p in loose)
            raise ProjectError(f"More than one project matches '{name_or_path}': {names}")
        raise ProjectError(f"No project called '{name_or_path}' in {root}.")

    @classmethod
    def all(cls) -> list[Project]:
        root = settings_mod.home()
        return sorted(
            (cls(p.parent) for p in root.glob(f"*/{PROJECT_FILE}")),
            key=lambda p: p.path.name,
        )

    @classmethod
    def latest(cls) -> Project | None:
        projects = cls.all()
        if not projects:
            return None
        return max(projects, key=lambda p: (p.path / PROJECT_FILE).stat().st_mtime)

    # ---- creating -------------------------------------------------------------
    @classmethod
    def create(
        cls, name: str, description: str = "", parameters: dict | None = None, example: str | None = None
    ) -> Project:
        """Start a project, optionally from one of the examples (its design and parameters)."""
        root = settings_mod.home()
        source = None
        if example:
            source = EXAMPLES_DIR / slugify(example)
            if not (source / DESIGN_FILE).exists():
                known = ", ".join(sorted(p.name for p in EXAMPLES_DIR.iterdir() if p.is_dir()))
                raise ProjectError(f"No example called '{example}'. Examples: {known}")
        path = root / slugify(name)
        if path.exists():
            raise ProjectError(f"A project folder called '{path.name}' already exists.")
        path.mkdir(parents=True)
        (path / "imports").mkdir()
        data = {
            "name": name,
            "created": date.today().isoformat(),
            "request": description,
            "parameters": parameters or {"width": 60.0, "depth": 40.0, "height": 20.0, "corner_radius": 3.0},
            "measurements": {},
            "split": {"joint": "peg", "fit": "snug"},
            "notes": [],
            "feedback": [],
        }
        if source is not None:
            example_data = yaml.safe_load((source / PROJECT_FILE).read_text(encoding="utf-8")) or {}
            data["parameters"] = {**example_data.get("parameters", {}), **(parameters or {})}
            data["split"] = example_data.get("split", data["split"])
            data["example"] = source.name
        (path / PROJECT_FILE).write_text(_dump(data), encoding="utf-8")
        if source is not None:
            shutil.copyfile(source / DESIGN_FILE, path / DESIGN_FILE)
        else:
            (path / DESIGN_FILE).write_text(
                DESIGN_TEMPLATE.format(name=name, description=description or name), encoding="utf-8"
            )
        (path / "sources.yaml").write_text("sources: []\nimports: []\n", encoding="utf-8")
        (path / NOTES_FILE).write_text(NOTES_TEMPLATE.format(name=name), encoding="utf-8")
        (path / ".gitignore").write_text("\n".join(GENERATED) + "\n", encoding="utf-8")
        project = cls(path)
        project._git("init", "-q", "-b", "main")
        return project

    # ---- data -------------------------------------------------------------------
    @property
    def name(self) -> str:
        return self.data.get("name", self.path.name)

    @property
    def data(self) -> dict:
        return yaml.safe_load((self.path / PROJECT_FILE).read_text(encoding="utf-8")) or {}

    def save(self, data: dict) -> None:
        (self.path / PROJECT_FILE).write_text(_dump(data), encoding="utf-8")

    def update(self, **changes) -> dict:
        data = self.data
        data.update(changes)
        self.save(data)
        return data

    @property
    def build_dir(self) -> Path:
        return self.path / "build"

    @property
    def slices_dir(self) -> Path:
        return self.path / "slices"

    @property
    def package_dir(self) -> Path:
        return self.path / "package"

    # ---- versions -----------------------------------------------------------------
    def _git(self, *args: str, check: bool = True) -> str:
        if shutil.which("git") is None:
            raise ProjectError("Git isn't installed, so versions can't be saved. Run setup again.")
        result = subprocess.run(
            [
                "git",
                "-c",
                "user.name=Fabricator",
                "-c",
                "user.email=fabricator@localhost",
                "-c",
                "commit.gpgsign=false",
                *args,
            ],
            cwd=self.path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if check and result.returncode != 0:
            raise ProjectError(f"Saving versions failed: {result.stderr.strip()}")
        return result.stdout

    def versions(self) -> list[dict]:
        """Saved versions, oldest first: [{number, label, date, commit}]."""
        out = self._git("log", "--reverse", "--format=%H%x09%ad%x09%s", "--date=short", check=False)
        found = []
        for line in out.splitlines():
            commit, day, subject = line.split("\t", 2)
            m = re.match(r"v(\d+) — (.*)", subject)
            if m:
                found.append({"number": int(m.group(1)), "label": m.group(2), "date": day, "commit": commit})
        return found

    def save_version(self, label: str) -> dict | None:
        """Save the current source files as the next version. None if nothing changed."""
        self._git("add", "-A")
        if not self._git("status", "--porcelain"):
            return None
        number = len(self.versions()) + 1
        self._git("commit", "-q", "-m", f"v{number} — {label}")
        return self.versions()[-1]

    def restore_version(self, number: int) -> dict:
        """Bring back a saved version's files, saved as a new version on top."""
        match = [v for v in self.versions() if v["number"] == number]
        if not match:
            raise ProjectError(f"There is no version {number}.")
        self._git("checkout", match[0]["commit"], "--", ".")
        saved = self.save_version(f"back to version {number}")
        return saved or match[0]

    def file_at_version(self, number: int, filename: str) -> str:
        match = [v for v in self.versions() if v["number"] == number]
        if not match:
            raise ProjectError(f"There is no version {number}.")
        return self._git("show", f"{match[0]['commit']}:{filename}")


def _dump(data: dict) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)

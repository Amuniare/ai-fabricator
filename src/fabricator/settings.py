"""Where the user's work lives, and their printer, material and fit settings.

Nothing here is stored inside the repository. Everything personal lives in the
projects folder (``FABRICATOR_HOME``, or "Fabricator Projects" in Documents).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DATA_DIR = Path(__file__).parent / "data"
SETTINGS_FILE = "settings.yaml"


def _documents_folder() -> Path:
    """The user's Documents folder, following OneDrive redirection on Windows."""
    if sys.platform == "win32":
        try:
            import ctypes
            import uuid

            # FOLDERID_Documents
            guid = uuid.UUID("{FDD39AD0-238F-46AF-ADB4-6C85480369C7}")

            class GUID(ctypes.Structure):
                _fields_ = [("data", ctypes.c_byte * 16)]

            g = GUID()
            ctypes.memmove(ctypes.byref(g), guid.bytes_le, 16)
            path_ptr = ctypes.c_wchar_p()
            shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
            if shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(path_ptr)) == 0:
                result = Path(path_ptr.value)
                ctypes.windll.ole32.CoTaskMemFree(path_ptr)  # type: ignore[attr-defined]
                return result
        except Exception:
            pass
    docs = Path.home() / "Documents"
    return docs if docs.is_dir() else Path.home()


USER_NOTES_FILE = "My notes.md"
USER_NOTES_TEMPLATE = """# My notes

Things Claude should remember for every project. Write in plain words, for example:

- Screws and parts I have on hand: ...
- My favourite colours or filament: ...
- Things I like (rounded corners, labels on parts, ...): ...

Claude reads this before starting work and adds to it when you say "remember that ...".

"""


def home() -> Path:
    """The projects folder. Created on first use."""
    env = os.environ.get("FABRICATOR_HOME")
    path = Path(env).expanduser() if env else _documents_folder() / "Fabricator Projects"
    path.mkdir(parents=True, exist_ok=True)
    notes = path / USER_NOTES_FILE
    if not notes.exists():
        notes.write_text(USER_NOTES_TEMPLATE, encoding="utf-8")
    return path


def load_data(name: str) -> dict:
    """Read one of the YAML tables shipped with the tool (fits, hardware, printers)."""
    return yaml.safe_load((DATA_DIR / name).read_text(encoding="utf-8")) or {}


@dataclass
class Settings:
    printer: str = "Bambu Lab A1 mini"
    nozzle: float = 0.4
    material: str = "PLA"
    slicer: str = "bambu"  # "bambu" or "orca"
    slicer_path: str | None = None  # only when auto-detection fails
    configured: bool = False
    # Learned fit gaps, keyed "printer|nozzle|material" -> {fit name: gap mm}
    fit_overrides: dict = field(default_factory=dict)

    @property
    def machine_key(self) -> str:
        return f"{self.printer}|{self.nozzle}|{self.material}"

    # ---- fits -------------------------------------------------------------
    def fits(self) -> dict[str, float]:
        """Gap per side, in mm, for each fit name, for the current printer and material.

        Starts from the shipped defaults (known to work on Bambu printers) and applies
        anything learned from the user's feedback on real prints.
        """
        table = load_data("fits.yaml")
        materials = table["materials"]
        base = dict(materials.get(self.material.upper(), materials["PLA"]))
        # Bigger nozzles squash wider lines; scale gaps gently with nozzle size.
        scale = max(1.0, self.nozzle / 0.4) ** 0.5
        gaps = {name: round(value * scale, 3) for name, value in base.items()}
        gaps.update(self.fit_overrides.get(self.machine_key, {}))
        return gaps

    def fit(self, name: str) -> float:
        gaps = self.fits()
        if name not in gaps:
            raise KeyError(f"Unknown fit '{name}'. Known fits: {', '.join(gaps)}")
        return gaps[name]

    # ---- files ------------------------------------------------------------
    @classmethod
    def load(cls) -> Settings:
        path = home() / SETTINGS_FILE
        if not path.exists():
            return cls()
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self) -> Path:
        path = home() / SETTINGS_FILE
        data = {k: getattr(self, k) for k in self.__dataclass_fields__}
        path.write_text(
            "# Fabricator settings. Safe to edit by hand.\n"
            + yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return path

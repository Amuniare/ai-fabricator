"""What a project's ``design.py`` works with.

A design file looks like this::

    from fabricator.design import *

    def build(p, ctx):
        model = Model("Remote bracket")
        plate = Box(p.width, p.depth, p.thickness)
        model.add(plate, "Wall plate")
        return model

``p`` holds the parameters from ``project.yaml`` (``p.width``). ``ctx`` knows the
printer, the material and the fit gaps (``ctx.fit("snug")``). Everything from
build123d is available too (``Box``, ``Cylinder``, ``fillet`` ...).

Parts are placed where they sit in the finished object. The build step works out
how each one lies on the print bed, splits anything too big for the printer, and
adds the joints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

# Joint below deliberately shadows build123d's Joint, so designs get fabricator's.
from build123d import *  # noqa: F403  # pyright: ignore[reportAssignmentType, reportWildcardImportFromLibrary]
from build123d import Vector
from build123d.topology import Shape

__all__ = [name for name in dir() if not name.startswith("_")]  # pyright: ignore[reportUnsupportedDunderAll]
__all__ += ["Context", "Joint", "Model", "Params", "Part3D"]

# Colours handed out to parts in order, so pictures tell pieces apart.
PALETTE = [
    "#4C78A8",
    "#F58518",
    "#54A24B",
    "#E45756",
    "#72B7B2",
    "#EECA3B",
    "#B279A2",
    "#FF9DA6",
    "#9D755D",
    "#BAB0AC",
]

JOINT_KINDS = {
    "peg": "a peg on one piece that pushes into a hole in the other",
    "dowel": "holes in both pieces joined by a separate pin",
    "screw": "a screw through one piece into a nut or insert in the other",
    "dovetail": "a wedge-shaped tongue that slides into a matching slot",
    "tongue-groove": "a ridge along one edge that fits into a groove on the other",
    "snap": "a flexible hook that clicks over a lip",
    "slide": "one piece slides onto or into the other",
    "rest": "one piece simply sits on or against the other",
    "glue": "glued faces with no locating feature",
}


class Params(dict):
    """Parameters from project.yaml, readable as ``p.width`` as well as ``p["width"]``."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"No parameter called '{name}'. Add it under 'parameters:' in project.yaml.") from None


@dataclass
class Part3D:
    """One printed piece (or a reference shape, such as the object being held)."""

    id: str
    name: str
    shape: Shape
    color: str
    printed: bool = True
    hardware: list[str] = field(default_factory=list)
    # Optional hint for how it lies on the bed: None (choose automatically), or a
    # face direction that should point down, such as "-Z" (as modelled) or "+Y".
    face_down: str | None = None
    source_part: str | None = None  # set on pieces made by splitting
    notes: str = ""


@dataclass
class Joint:
    """How two parts connect. Kept so Claude can explain and check the assembly."""

    a: str
    b: str
    kind: str
    fit: str = "snug"
    gap: float | None = None  # mm per side; filled from the fit when not given
    hardware: list[str] = field(default_factory=list)
    at: tuple[float, float, float] | None = None
    axis: tuple[float, float, float] | None = None  # direction one part moves to come apart
    note: str = ""
    # Small solids used by the assembly checks: the male feature of a peg, a screw's
    # tool access path. Filled in by joint builders, not by design files.
    features: dict[str, Shape] = field(default_factory=dict)


class Model:
    """The whole object: named parts, how they join, and any bought hardware."""

    def __init__(self, name: str):
        self.name = name
        self.parts: list[Part3D] = []
        self.joints: list[Joint] = []
        self.extra_hardware: list[str] = []
        self.notes: list[str] = []

    # ---- parts --------------------------------------------------------------
    def add(
        self,
        shape: Shape,
        name: str,
        *,
        id: str | None = None,
        color: str | None = None,
        printed: bool = True,
        hardware: list[str] | None = None,
        face_down: str | None = None,
        notes: str = "",
    ) -> Part3D:
        """Add a part. Ids are handed out as P01, P02 ... unless given."""
        if not isinstance(shape, Shape):
            raise TypeError(f"'{name}' is not a 3D shape (got {type(shape).__name__}).")
        if id is None:
            id = f"P{sum(1 for p in self.parts if p.printed) + 1:02d}" if printed else f"R{len(self.parts) + 1:02d}"
        if any(p.id == id for p in self.parts):
            raise ValueError(f"Two parts share the id {id}.")
        part = Part3D(
            id=id,
            name=name,
            shape=shape,
            color=color or PALETTE[len(self.parts) % len(PALETTE)],
            printed=printed,
            hardware=list(hardware or []),
            face_down=face_down,
            notes=notes,
        )
        self.parts.append(part)
        return part

    def reference(self, shape: Shape, name: str, **kw) -> Part3D:
        """A shape that is shown and checked against but never printed (the drill, the wall)."""
        return self.add(shape, name, printed=False, color=kw.pop("color", "#BBBBBB"), **kw)

    def part(self, id_or_name: str) -> Part3D:
        for p in self.parts:
            if id_or_name in (p.id, p.name):
                return p
        raise KeyError(f"No part '{id_or_name}'. Parts: {', '.join(p.id for p in self.parts)}")

    # ---- joints and hardware ---------------------------------------------------
    def join(
        self,
        a: str | Part3D,
        b: str | Part3D,
        kind: str,
        *,
        fit: str = "snug",
        gap: float | None = None,
        hardware: list[str] | None = None,
        at: tuple[float, float, float] | None = None,
        axis: tuple[float, float, float] | None = None,
        note: str = "",
    ) -> Joint:
        """Record that two parts connect, and how."""
        if kind not in JOINT_KINDS:
            raise ValueError(f"Unknown joint '{kind}'. Use one of: {', '.join(JOINT_KINDS)}")
        joint = Joint(
            a=a.id if isinstance(a, Part3D) else self.part(a).id,
            b=b.id if isinstance(b, Part3D) else self.part(b).id,
            kind=kind,
            fit=fit,
            gap=gap,
            hardware=list(hardware or []),
            at=cast("tuple[float, float, float]", tuple(at)) if at else None,
            axis=cast("tuple[float, float, float]", tuple(axis)) if axis else None,
            note=note,
        )
        self.joints.append(joint)
        return joint

    def hardware(self, item: str, qty: int = 1) -> None:
        """Bought parts needed for assembly, such as 'M3x12 screw'."""
        self.extra_hardware.extend([item] * qty)

    def note(self, text: str) -> None:
        """Something worth telling the user, kept with the build results."""
        self.notes.append(text)


class Context:
    """What a design needs to know about the printer and material."""

    def __init__(self, settings, bed: tuple[float, float, float], project_dir: Path):
        self._settings = settings
        self.printer: str = settings.printer
        self.nozzle: float = settings.nozzle
        self.material: str = settings.material
        self.bed: tuple[float, float, float] = bed
        self.project_dir = project_dir
        self.fits: dict[str, float] = settings.fits()
        from .settings import load_data

        rules = load_data("printers.yaml")
        self.min_wall: float = round(rules["min_wall_lines"] * self.nozzle, 2)
        self.min_feature: float = round(rules["min_feature_lines"] * self.nozzle, 2)

    def fit(self, name: str = "snug") -> float:
        """Gap per side in mm for a fit: press, snug, sliding, loose or hole."""
        if name not in self.fits:
            raise KeyError(f"Unknown fit '{name}'. Known fits: {', '.join(self.fits)}")
        return self.fits[name]

    def imported(self, name: str) -> Shape:
        """Load a file brought in with ``fabricator import`` (STEP or STL) as a shape."""
        from .sources import load_import

        return load_import(self.project_dir, name)

    @property
    def hw(self):
        """The hardware library: screw holes, nut traps, insert holes, magnet pockets."""
        from . import library

        return library.Library(self)


def vec(v) -> Vector:
    return v if isinstance(v, Vector) else Vector(*v)

"""Automatic checks. Code decides whether a design passed, not Claude's judgement."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class Check:
    """One check result, written so Claude can act on it and explain it simply.

    status: "pass", "warn" (printable, but worth mentioning) or "fail" (must be fixed).
    message: one plain sentence, e.g. "Thinnest wall is 0.72 mm; it needs at least 0.80 mm."
    """

    name: str
    status: str
    message: str
    part: str | None = None
    value: float | None = None
    limit: float | None = None
    where: str | None = None  # plain description or "x, y, z" in mm
    fix: str | None = None  # suggested change, in plain words

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


def worst(checks: list[Check]) -> str:
    statuses = {c.status for c in checks}
    return "fail" if "fail" in statuses else "warn" if "warn" in statuses else "pass"

"""Will it print well as it lies on the bed: supports, bridges, grip, wobble, warping."""

from __future__ import annotations

import numpy as np
from build123d import GeomType

from .. import orient
from . import Check
from .geometry import describe_where

LONG_BRIDGE_MM = 40.0
MIN_CONTACT_MM2 = 50.0
MIN_CONTACT_FRACTION = 0.10
TALL_RATIO = 4.0
WARP_MM = 150.0
WARP_MATERIALS = {"ABS", "ASA", "PC", "NYLON", "PA"}
DROOP_DIAMETER = 8.0


def _hull_area(verts) -> float:
    from scipy.spatial import ConvexHull

    try:
        return float(ConvexHull(verts[:, :2]).volume)
    except Exception:
        return float(np.prod(verts[:, :2].max(axis=0) - verts[:, :2].min(axis=0)))


def _horizontal_holes(placed):
    """Round holes lying sideways wider than DROOP_DIAMETER: (diameter, centre) list."""
    found = []
    try:
        faces = [f for f in placed.faces() if f.geom_type == GeomType.CYLINDER]
    except Exception:
        return found
    seen = []
    for f in faces:
        try:
            r = float(f.radius)
            if 2 * r <= DROOP_DIAMETER:
                continue
            ax = f.axis_of_rotation
            d = np.array([ax.direction.X, ax.direction.Y, ax.direction.Z])
            if abs(d[2]) > 0.2:
                continue
            # a hole's surface faces towards its axis; a boss faces away
            c = f.center()
            n = f.normal_at(c)
            p = np.array([ax.position.X, ax.position.Y, ax.position.Z])
            cv = np.array([c.X, c.Y, c.Z])
            radial = cv - p - d * float((cv - p) @ d)
            if float(radial @ np.array([n.X, n.Y, n.Z])) >= 0:
                continue
            key = (round(r, 2), tuple(np.round(p, 0)))
            if key in seen:
                continue
            seen.append(key)
            found.append((2 * r, cv))
        except Exception:
            continue
    return found


def check_part(part, placed, mesh, ctx, choice=None) -> list[Check]:
    if not part.printed:
        return []
    pid = part.id
    out: list[Check] = []
    m = orient.analyze(mesh)
    verts = m["verts"]
    lo, hi = verts.min(axis=0), verts.max(axis=0)
    size = hi - lo

    # ---- overhangs ----
    support = m["support_area"]
    if support > orient.SUPPORT_AREA_MM2:
        mask = m["support_mask"]
        w = m["areas"][mask]
        centre = (m["tri"][mask].mean(axis=1) * w[:, None]).sum(axis=0) / w.sum()
        where = describe_where(centre, lo, hi)
        out.append(Check(
            "overhangs", "warn",
            f"About {support:.0f} mm² hangs out steeper than 45 degrees, so supports will be added under it ({where}).",
            part=pid, value=round(support, 1), limit=orient.SUPPORT_AREA_MM2, where=where,
            fix="Reshape it with a 45 degree slope instead of a flat underside, or turn the piece so that side faces up; "
                "supports leave rough marks where they touch.",
        ))
    else:
        out.append(Check(
            "overhangs", "pass",
            "Nothing hangs out steeper than 45 degrees, so no supports are needed." if support < 1
            else f"Only {support:.0f} mm² hangs out steeply, which prints fine without supports.",
            part=pid, value=round(support, 1), limit=orient.SUPPORT_AREA_MM2,
        ))

    # ---- long bridges ----
    bridges = [g for g in m["flat_groups"] if g["span"] > LONG_BRIDGE_MM]
    if bridges:
        g = max(bridges, key=lambda g: g["span"])
        where = describe_where(g["centre"], lo, hi)
        out.append(Check(
            "long bridges", "warn",
            f"A flat underside spans {g['span']:.0f} mm in the air ({where}), which will sag without supports.",
            part=pid, value=round(g["span"], 1), limit=LONG_BRIDGE_MM, where=where,
            fix="Add a support wall under it, shorten the span, or give it an arched or sloped underside.",
        ))
    else:
        out.append(Check("long bridges", "pass", f"No flat underside spans more than {LONG_BRIDGE_MM:g} mm.", part=pid, limit=LONG_BRIDGE_MM))

    # ---- bed contact ----
    contact = m["contact_area"]
    footprint = _hull_area(verts)
    frac = contact / footprint if footprint > 0 else 1.0
    if contact < MIN_CONTACT_MM2 or frac < MIN_CONTACT_FRACTION:
        out.append(Check(
            "bed contact", "warn",
            f"Only {contact:.0f} mm² touches the bed ({frac * 100:.0f}% of its footprint), so it may come loose during printing.",
            part=pid, value=round(contact, 1), limit=MIN_CONTACT_MM2,
            fix="Give it a flat face to sit on, or a wider base; a brim can also be added in the slicer.",
        ))
    else:
        out.append(Check(
            "bed contact", "pass",
            f"{contact:.0f} mm² sits flat on the bed ({frac * 100:.0f}% of its footprint).",
            part=pid, value=round(contact, 1), limit=MIN_CONTACT_MM2,
        ))

    # ---- tall and thin ----
    base = float(min(size[0], size[1]))
    ratio = float(size[2]) / base if base > 1e-6 else 0.0
    if ratio > TALL_RATIO:
        out.append(Check(
            "tall and thin", "warn",
            f"The piece is {size[2]:.0f} mm tall on a base only {base:.0f} mm across ({ratio:.1f} times as tall), so it may wobble or snap while printing.",
            part=pid, value=round(ratio, 1), limit=TALL_RATIO,
            fix="Lay it on its side, widen the base, or print it in a different orientation.",
        ))
    else:
        out.append(Check(
            "tall and thin", "pass",
            f"The piece is {size[2]:.0f} mm tall on a {base:.0f} mm wide base, a stable shape.",
            part=pid, value=round(ratio, 1), limit=TALL_RATIO,
        ))

    # ---- warping ----
    mat = str(ctx.material).upper()
    if mat in WARP_MATERIALS:
        longest = float(max(size[0], size[1]))
        if longest > WARP_MM:
            out.append(Check(
                "warping", "warn",
                f"The footprint is {longest:.0f} mm long and {ctx.material} tends to curl up at the corners on large flat pieces.",
                part=pid, value=round(longest, 1), limit=WARP_MM,
                fix="Split it into smaller pieces, add rounded corners, use a brim, or print in PLA or PETG.",
            ))
        else:
            out.append(Check(
                "warping", "pass",
                f"The footprint is {longest:.0f} mm long, small enough that {ctx.material} should not curl.",
                part=pid, value=round(longest, 1), limit=WARP_MM,
            ))

    # ---- sideways holes ----
    holes = _horizontal_holes(placed)
    if holes:
        d, c = max(holes, key=lambda h: h[0])
        where = describe_where(c, lo, hi)
        out.append(Check(
            "sideways holes", "warn",
            f"A round hole {d:.0f} mm across lies on its side ({where}), so its top may droop slightly.",
            part=pid, value=round(d, 1), limit=DROOP_DIAMETER, where=where,
            fix="Make the hole teardrop-shaped with a pointed top, or turn the piece so the hole points up.",
        ))
    else:
        out.append(Check(
            "sideways holes", "pass",
            f"No round holes wider than {DROOP_DIAMETER:g} mm lie on their side.",
            part=pid, limit=DROOP_DIAMETER,
        ))
    return out

"""Is the piece a sound solid that fits the bed and has walls the printer can make?"""

from __future__ import annotations

import numpy as np

from . import Check

SAMPLES = 3000
INSIDE_OFFSET = 0.01  # mm: start rays just inside the surface
GRAZE = 0.05  # mm: hits closer than this are edge grazes, not walls
BED_MARGIN = 1.0
TINY_MM3 = 1.0


def describe_where(point, lo, hi) -> str:
    """Say where a point is on a piece, in plain words (as the piece lies on the bed)."""
    p, lo, hi = (np.asarray(v, dtype=float) for v in (point, lo, hi))
    span = np.maximum(hi - lo, 1e-9)
    t = (p - lo) / span
    words = []  # (axis, word)
    if t[2] > 2 / 3:
        words.append("top")
    elif t[2] < 1 / 3:
        words.append("bottom")
    if t[0] < 1 / 3:
        words.append("left")
    elif t[0] > 2 / 3:
        words.append("right")
    if t[1] > 2 / 3:
        words.append("back")
    elif t[1] < 1 / 3:
        words.append("front")
    vertical = [w for w in words if w in ("top", "bottom")]
    side = [w for w in words if w in ("left", "right")]
    depth = [w for w in words if w in ("front", "back")]
    if len(words) == 3:
        return f"near the {vertical[0]} {side[0]} corner of the {depth[0]}"
    if len(words) == 2:
        return f"near the {' '.join(words)} edge"
    if len(words) == 1:
        return f"on the {words[0]} part"
    return "in the middle of the piece"


def _wall(mesh, ctx, part):
    """Ray-cast from surface points inward to find the thinnest wall."""
    import trimesh

    points, face_idx = trimesh.sample.sample_surface(mesh, SAMPLES, seed=0)
    normals = mesh.face_normals[face_idx]
    origins = points - normals * INSIDE_OFFSET
    dirs = -normals
    try:
        locs, ray_idx, _ = mesh.ray.intersects_location(origins, dirs, multiple_hits=False)
    except Exception:
        return None
    if len(ray_idx) == 0:
        return None
    dist = np.linalg.norm(locs - origins[ray_idx], axis=1) + INSIDE_OFFSET
    ok = dist > GRAZE
    if not ok.any():
        return None
    dist, ray_idx = dist[ok], ray_idx[ok]
    k = int(np.argmin(dist))
    r = ray_idx[k]
    mid = (points[r] + locs[ok][k]) / 2
    return float(dist[k]), mid


def check_part(part, placed, mesh, ctx, choice=None) -> list[Check]:
    if not part.printed:
        return []
    pid = part.id
    out: list[Check] = []
    bb = placed.bounding_box()
    lo = np.array([bb.min.X, bb.min.Y, bb.min.Z])
    hi = np.array([bb.max.X, bb.max.Y, bb.max.Z])

    # ---- valid solid ----
    valid = bool(placed.is_valid)
    out.append(Check(
        "valid solid", "pass" if valid else "fail",
        "The shape is a valid solid." if valid else "The shape is not a valid solid, so it cannot be turned into a print file reliably.",
        part=pid,
        fix=None if valid else "Rebuild the shape from simpler pieces; avoid zero-thickness faces and tiny sliver gaps.",
    ))

    # ---- closed mesh ----
    closed = bool(mesh.is_watertight)
    out.append(Check(
        "closed mesh", "pass" if closed else "fail",
        "The surface is fully closed with no holes in it." if closed
        else "The surface has gaps, so the slicer may not know what is inside the piece.",
        part=pid,
        fix=None if closed else "Check for faces that do not meet or cuts that leave a paper-thin edge, and simplify around them.",
    ))

    # ---- bodies ----
    solids = list(placed.solids())
    vols = sorted((abs(s.volume) for s in solids), reverse=True)
    tiny = [v for v in vols if v < TINY_MM3]
    big = [v for v in vols if v >= TINY_MM3]
    if len(big) > 1:
        out.append(Check(
            "one body", "fail",
            f"The piece is {len(big)} separate bodies that are not joined together, so they would print as loose parts.",
            part=pid, value=len(big), limit=1,
            fix="Make the bodies touch or overlap and join them into one, or make them separate parts.",
        ))
    else:
        out.append(Check("one body", "pass", "The piece is one connected body.", part=pid, value=max(len(big), 1), limit=1))
    if tiny:
        out.append(Check(
            "tiny loose bits", "fail",
            f"There {'is' if len(tiny) == 1 else 'are'} {len(tiny)} tiny loose bit{'s' if len(tiny) > 1 else ''} "
            f"(largest {max(tiny):.2f} mm³) left over from a cut.",
            part=pid, value=len(tiny),
            fix="Change the cut so it does not leave a sliver, or move it a little so the leftovers disappear.",
        ))
    else:
        out.append(Check("tiny loose bits", "pass", "No tiny loose bits are left over.", part=pid, value=0))

    # ---- fits the bed ----
    size = hi - lo
    avail = np.array(ctx.bed, dtype=float) - 2 * BED_MARGIN
    over = size - avail
    names = ["left to right", "front to back", "top to bottom"]
    if np.any(over > 1e-6):
        k = int(np.argmax(over))
        out.append(Check(
            "fits the bed", "fail",
            f"As it lies on the bed the piece is {size[0]:.1f} x {size[1]:.1f} x {size[2]:.1f} mm, "
            f"which is {over[k]:.1f} mm too big {names[k]} for the {ctx.bed[0]:g} x {ctx.bed[1]:g} x {ctx.bed[2]:g} mm bed.",
            part=pid, value=float(size[k]), limit=float(avail[k]),
            where=f"too long {names[k]}",
            fix="Make the piece smaller, or split it into pieces that each fit on the bed.",
        ))
    else:
        out.append(Check(
            "fits the bed", "pass",
            f"The piece is {size[0]:.1f} x {size[1]:.1f} x {size[2]:.1f} mm and fits the {ctx.bed[0]:g} x {ctx.bed[1]:g} mm bed.",
            part=pid, value=float(size.max()), limit=float(avail.max()),
        ))

    # ---- walls ----
    res = _wall(mesh, ctx, part) if closed else None
    if res is None:
        out.append(Check(
            "wall thickness", "pass" if closed else "warn",
            "Wall thickness could not be measured." if not closed else "No walls were found to measure.",
            part=pid,
        ))
    else:
        t, mid = res
        where = describe_where(mid, lo, hi)
        if t < ctx.min_feature:
            out.append(Check(
                "wall thickness", "fail",
                f"The thinnest wall is {t:.2f} mm, thinner than the {ctx.min_feature:g} mm the printer can make at all.",
                part=pid, value=round(t, 3), limit=ctx.min_feature, where=where,
                fix=f"Thicken that wall to at least {ctx.min_wall:g} mm, or remove the feature.",
            ))
        elif t < ctx.min_wall:
            out.append(Check(
                "wall thickness", "warn",
                f"The thinnest wall is {t:.2f} mm, thinner than the recommended {ctx.min_wall:g} mm, so it may be weak or print patchy.",
                part=pid, value=round(t, 3), limit=ctx.min_wall, where=where,
                fix=f"Thicken that wall to at least {ctx.min_wall:g} mm.",
            ))
        else:
            out.append(Check(
                "wall thickness", "pass",
                f"The thinnest wall is {t:.2f} mm, above the {ctx.min_wall:g} mm minimum (smallest details are covered by this measurement too).",
                part=pid, value=round(t, 3), limit=ctx.min_wall, where=where,
            ))
    return out

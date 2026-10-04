"""Checks on the whole assembly: do the parts fit together and onto the printer?

- No two printed parts overlap, and no printed part runs into a reference shape (the
  thing being held, the wall).
- Every joint's male feature (peg, tongue, pin, hook lip) is exactly the fit gap away from
  the part it goes into, measured on the real shapes.
- A screwdriver can reach every screw.
- Every printed piece fits the printer in some rotation.
- With more than one printed part, every part is joined to something.
"""

from __future__ import annotations

from build123d.topology import Shape

from . import Check

OVERLAP_LIMIT = 0.5  # mm3 of shared volume that counts as an overlap
GAP_TOLERANCE = 0.02  # mm either side of the intended joint gap
TOOL_LIMIT = 0.05  # mm3


def _where(shape: Shape) -> str:
    c = shape.bounding_box().center()
    return f"{c.X:.1f}, {c.Y:.1f}, {c.Z:.1f}"


def _boxes_touch(a, b, grow: float = 0.0) -> bool:
    amin, amax, bmin, bmax = tuple(a.min), tuple(a.max), tuple(b.min), tuple(b.max)
    return all(amin[k] - grow <= bmax[k] and bmin[k] - grow <= amax[k] for k in range(3))


def _common(a: Shape, b: Shape) -> tuple[float, Shape | None]:
    try:
        r = a.intersect(b)
    except Exception:
        return 0.0, None
    if r is None:
        return 0.0, None
    solids = r.solids()
    vol = sum(s.volume for s in solids)
    return vol, (solids[0] if solids else None)


def _distance(a: Shape, b: Shape) -> float:
    try:
        return float(a.distance_to(b))
    except Exception:
        return float("nan")


def check_model(model, ctx) -> list[Check]:
    from ..split import fits_bed, size_of, usable_bed

    checks: list[Check] = []
    printed = [p for p in model.parts if p.printed]
    refs = [p for p in model.parts if not p.printed]
    boxes = {p.id: p.shape.bounding_box() for p in model.parts}
    min_gap = min(ctx.fits.values()) if ctx.fits else 0.05
    min_gap = max(min_gap, 0.01)

    # ---- printed parts overlapping each other ---------------------------------------
    pairs = 0
    bad = 0
    for i, a in enumerate(printed):
        for b in printed[i + 1 :]:
            if not _boxes_touch(boxes[a.id], boxes[b.id]):
                continue
            pairs += 1
            vol, piece = _common(a.shape, b.shape)
            if vol > OVERLAP_LIMIT:
                bad += 1
                checks.append(
                    Check(
                        "parts overlap",
                        "fail",
                        f"{a.id} and {b.id} overlap by {vol:.1f} mm³, so they can't both be where the design puts them.",
                        part=a.id,
                        value=round(vol, 2),
                        limit=OVERLAP_LIMIT,
                        where=_where(piece) if piece is not None else None,
                        fix=f"Move or shrink {a.id} or {b.id} so they only touch, or cut one from the other.",
                    )
                )
    if printed and not bad:
        checks.append(
            Check(
                "parts overlap",
                "pass",
                f"No printed parts overlap ({pairs} pair{'s' if pairs != 1 else ''} close enough to check).",
            )
        )

    # ---- printed parts against reference shapes --------------------------------------
    for r in refs:
        for p in printed:
            if not _boxes_touch(boxes[r.id], boxes[p.id], grow=min_gap + 0.01):
                continue
            vol, piece = _common(r.shape, p.shape)
            if vol > OVERLAP_LIMIT:
                checks.append(
                    Check(
                        "hits reference",
                        "fail",
                        f"{_the(r.name)} would hit {p.id}: they overlap by {vol:.1f} mm³.",
                        part=p.id,
                        value=round(vol, 2),
                        limit=OVERLAP_LIMIT,
                        where=_where(piece) if piece is not None else None,
                        fix=f"Make room in {p.id} for {r.name.lower()}, with at least {min_gap:.2f} mm to spare.",
                    )
                )
                continue
            dist = _distance(r.shape, p.shape)
            if dist == dist and dist < min_gap - 1e-6:
                checks.append(
                    Check(
                        "reference clearance",
                        "warn",
                        f"{p.id} is only {dist:.2f} mm from {r.name.lower()}; anything that has to go in or out "
                        f"needs at least {min_gap:.2f} mm.",
                        part=p.id,
                        value=round(dist, 3),
                        limit=min_gap,
                        fix="Ignore this if the part is meant to rest against it; otherwise leave a little more room.",
                    )
                )
            else:
                checks.append(
                    Check(
                        "reference clearance",
                        "pass",
                        f"{p.id} clears {r.name.lower()} by {dist:.2f} mm.",
                        part=p.id,
                        value=round(dist, 3),
                    )
                )

    # ---- joint gaps -----------------------------------------------------------------
    parts = {p.id: p for p in model.parts}
    for j in model.joints:
        gap = j.gap if j.gap is not None else (ctx.fits.get(j.fit) if j.fit else None)
        checks += _joint_gap(j, parts, gap)

    # ---- screwdriver access --------------------------------------------------------------
    for j in model.joints:
        tool = j.features.get("tool") if j.features else None
        if tool is None:
            continue
        blocked = []
        for t in tool.solids() or [tool]:
            tb = t.bounding_box()
            for p in model.parts:
                if not _boxes_touch(tb, boxes[p.id]):
                    continue
                vol, _ = _common(t, p.shape)
                if vol > TOOL_LIMIT:
                    blocked.append((t, p))
        if blocked:
            t, p = blocked[0]
            checks.append(
                Check(
                    "tool access",
                    "fail",
                    f"A screwdriver can't reach the screw at {_where(t)}: {p.id} is in the way.",
                    part=j.a,
                    where=_where(t),
                    fix="Move the screw, or open up a path for the screwdriver straight out from the screw head.",
                )
            )
        else:
            n = len(tool.solids()) or 1
            checks.append(
                Check(
                    "tool access",
                    "pass",
                    f"A screwdriver can reach {'both screws' if n == 2 else 'all ' + str(n) + ' screws' if n > 2 else 'the screw'} "
                    f"joining {j.a} and {j.b}.",
                    part=j.a,
                )
            )

    # ---- every printed piece fits the printer ----------------------------------------------
    use = usable_bed(ctx.bed)
    for p in printed:
        size = size_of(p.shape)
        if fits_bed(size, ctx.bed):
            continue
        checks.append(
            Check(
                "fits printer",
                "fail",
                f"{p.id} is {size[0]:.0f} x {size[1]:.0f} x {size[2]:.0f} mm, which doesn't fit the "
                f"{use[0]:.0f} x {use[1]:.0f} x {use[2]:.0f} mm the printer can use, whichever way it is turned.",
                part=p.id,
                value=round(max(size), 1),
                limit=max(use),
                fix="Split it into pieces (add a seam), or make it smaller.",
            )
        )
    if printed and all(fits_bed(size_of(p.shape), ctx.bed) for p in printed):
        checks.append(
            Check(
                "fits printer",
                "pass",
                f"All {len(printed)} printed part{'s fit' if len(printed) != 1 else ' fits'} the "
                f"{use[0]:.0f} x {use[1]:.0f} x {use[2]:.0f} mm the printer can use.",
            )
        )

    # ---- everything is attached --------------------------------------------------------------
    if len(printed) > 1:
        joined = {j.a for j in model.joints} | {j.b for j in model.joints}
        loose = [p for p in printed if p.id not in joined]
        for p in loose:
            checks.append(
                Check(
                    "attached",
                    "warn",
                    f"{p.id} isn't attached to anything.",
                    part=p.id,
                    fix=f"Add a joint between {p.id} and the part it connects to (model.join), "
                    "or ignore this if it is meant to be separate.",
                )
            )
        if not loose:
            checks.append(
                Check(
                    "attached",
                    "pass",
                    f"Every printed part is joined to another ({len(model.joints)} "
                    f"joint{'s' if len(model.joints) != 1 else ''}).",
                )
            )
    return checks


def _the(name: str) -> str:
    low = name[:1].lower() + name[1:]
    return low if low.startswith("the ") else f"the {low}"


_FEATURE_WORDS = {"peg": "peg", "dowel": "pin", "dovetail": "dovetail", "tongue-groove": "tongue", "snap": "hook lip"}


def _joint_gap(j, parts, gap) -> list[Check]:
    feats = j.features or {}
    targets = []
    if "male" in feats and j.b in parts:
        targets.append((feats["male"], parts[j.b]))
    if "pin" in feats:
        for end in (j.a, j.b):
            if end in parts:
                targets.append((feats["pin"], parts[end]))
    if not targets:
        return []
    if gap is None:
        return [
            Check(
                "joint gap",
                "warn",
                f"The {j.kind} joint between {j.a} and {j.b} has no fit gap set, so it couldn't be checked.",
                part=j.a,
                fix="Give the joint a fit, such as fit='snug'.",
            )
        ]
    word = _FEATURE_WORDS.get(j.kind, "feature")
    measured = []
    worst = None
    for feature, part in targets:
        for s in feature.solids() or [feature]:
            d = _distance(s, part.shape)
            measured.append(d)
            off = d != d or abs(d - gap) > GAP_TOLERANCE + 1e-9  # d != d catches NaN
            if off and (worst is None or abs(d - gap) > abs(worst[0] - gap)):
                worst = (d, s, part)
    if worst is not None:
        d, s, part = worst
        if d != d:
            msg = f"The {word} gap between {j.a} and {j.b} couldn't be measured."
        elif d < 1e-6:
            msg = (
                f"The {word} at {_where(s)} touches or runs into {part.id}; it needs a {gap:.2f} mm gap ({j.fit} fit)."
            )
        else:
            msg = (
                f"The {word} at {_where(s)} is {d:.2f} mm from {part.id}; the {j.fit} fit needs "
                f"{gap:.2f} mm (within {GAP_TOLERANCE:.2f})."
            )
        return [
            Check(
                "joint gap",
                "fail",
                msg,
                part=j.a,
                value=round(d, 3) if d == d else None,
                limit=gap,
                where=_where(s),
                fix="Make the hole or slot exactly the fit gap bigger than the part that goes into it, "
                "for example with joints.peg(), which does this for you.",
            )
        ]
    n = len(measured)
    many = f"both {word}s are" if n == 2 else f"all {n} {word}s are" if n > 2 else f"the {word} is"
    return [
        Check(
            "joint gap",
            "pass",
            f"{j.a} + {j.b}: {many} {min(measured):.2f} mm clear, as the {j.fit} fit needs ({gap:.2f} mm).",
            part=j.a,
            value=round(min(measured), 3),
            limit=gap,
        )
    ]

"""Joint geometry: pegs, dowels, screws, dovetails, tongue-and-groove and snap hooks.

Used by ``split.py`` to join the pieces of a part too big for the printer, and by design
files directly::

    from fabricator import joints

    r = joints.peg(base, lid, at=[(-30, 0, 20), (30, 0, 20)], direction=(0, 0, 1), ctx=ctx)
    a = model.add(r.a, "Base")
    b = model.add(r.b, "Lid")
    r.join(model, a, b)          # records the joint, with what the checks need

Conventions for every builder:

- ``a`` carries the male feature (peg, tongue, hook, screw head); ``b`` the female one.
- ``at`` is a point (or a list of points) on the face where the two parts meet;
  ``direction`` points from ``a`` into ``b`` (for screws: from the head into the parts).
- Gaps come from the fit (``ctx.fit("snug")``) so the parts fit on the first print.
- Cutting tools always reach a little past the surfaces they cut, so OpenCascade
  never has to deal with two faces lying exactly on top of each other.

Each builder returns a :class:`JointResult` with the changed shapes (``r.a``, ``r.b``),
any separate printed pieces (``r.parts``, such as dowel pins) and the joint description,
including ``features``: small solids the assembly check measures (``"male"``: the male
feature as placed, ``"tool"``: the space a screwdriver needs).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from build123d import Axis, Compound, Edge, Face, Plane, Shape, Solid, Vector, Wire

__all__ = [
    "JointResult", "peg", "dowel", "screw", "dovetail", "tongue_groove", "snap_hook",
    "peg_size", "fuse", "cut", "one_solid",
]

# Extra depth of a hole beyond the peg tip, so the peg bottoms out on its shoulder,
# not on the end of the hole (and any blob at the hole bottom doesn't matter).
HOLE_EXTRA_DEPTH = 0.5
# How far tools overlap past a surface, and how far a peg sinks into its own part.
OVERLAP = 0.6
# Chamfer at the mouth of a printed hole: takes care of elephant's foot and guides the peg in.
MOUTH_CHAMFER = 0.4


# ---- small geometry helpers ----------------------------------------------------------

def _v(p) -> Vector:
    return p if isinstance(p, Vector) else Vector(*p)


def _unit(p) -> Vector:
    v = _v(p)
    if v.length < 1e-9:
        raise ValueError("A joint direction can't be zero length.")
    return v.normalized()


def _points(at) -> list[Vector]:
    """One point or a list of points."""
    if isinstance(at, Vector):
        return [at]
    at = list(at)
    if at and isinstance(at[0], (int, float)):
        return [_v(at)]
    return [_v(p) for p in at]


def _gap(ctx, fit: str, gap: float | None) -> float:
    if gap is not None:
        return float(gap)
    if ctx is not None:
        return ctx.fit(fit)
    from .settings import Settings

    return Settings().fit(fit)


def _library(ctx):
    from .library import Library

    return Library(ctx)


def one_solid(shape: Shape) -> Shape:
    """A boolean result as a single Solid when it is one, else as it is."""
    solids = shape.solids()
    if len(solids) == 1:
        return solids[0]
    return shape


def fuse(base: Shape, tools: list[Shape]) -> Shape:
    if not tools:
        return base
    return one_solid(base.fuse(*tools).clean())


def cut(base: Shape, tools: list[Shape]) -> Shape:
    if not tools:
        return base
    return one_solid(base.cut(*tools).clean())


def _compound(shapes: list[Shape]) -> Shape:
    return shapes[0] if len(shapes) == 1 else Compound(list(shapes))


def _revolved(profile: list[tuple[float, float]], at: Vector, direction: Vector) -> Solid:
    """Revolve an (r, z) profile about local Z, then put local Z along ``direction`` at ``at``."""
    wire = Wire.make_polygon([Vector(r, 0, z) for r, z in profile], close=True)
    solid = Solid.revolve(Face(wire), 360, Axis.Z)
    return Plane(origin=at, z_dir=direction).location * solid


def _cylinder(d: float, z0: float, z1: float, at: Vector, direction: Vector) -> Solid:
    r = d / 2
    return _revolved([(0, z0), (r, z0), (r, z1), (0, z1)], at, direction)


def _prism(poly: list[tuple[float, float]], y0: float, y1: float, plane: Plane) -> Solid:
    """A prism with cross-section ``poly`` in local (x, z), running from y0 to y1 in local y."""
    wire = Wire.make_polygon([Vector(x, y0, z) for x, z in poly], close=True)
    solid = Solid.extrude(Face(wire), Vector(0, y1 - y0, 0))
    return plane.location * solid


def _box(x0, x1, y0, y1, z0, z1, plane: Plane) -> Solid:
    return _prism([(x0, z0), (x1, z0), (x1, z1), (x0, z1)], y0, y1, plane)


def _frame(at: Vector, direction: Vector, along) -> Plane:
    """Local frame: z = direction, y = ``along`` (made perpendicular to z)."""
    z = _unit(direction)
    y = _unit(along)
    y = (y - z * y.dot(z))
    if y.length < 1e-6:
        raise ValueError("The slide direction must not be parallel to the joint direction.")
    y = y.normalized()
    x = y.cross(z)
    return Plane(origin=at, x_dir=x, z_dir=z)


def _ray_spans(shape: Shape, start: Vector, d: Vector, reach: float = 2000.0) -> list[tuple[float, float]]:
    """Where a ray from ``start`` along ``d`` is inside ``shape``: [(enter, leave)] distances."""
    line = Edge.make_line(start - d * 1.0, start + d * reach)
    spans = []
    try:
        hits = shape.intersect(line)
    except Exception:
        return spans
    if hits is None:
        return spans
    items = hits if isinstance(hits, (list, tuple)) else list(hits.edges()) if hasattr(hits, "edges") else [hits]
    for e in items:
        if not isinstance(e, Edge):
            continue
        t0 = (e.start_point() - start).dot(d)
        t1 = (e.end_point() - start).dot(d)
        spans.append((min(t0, t1), max(t0, t1)))
    return sorted(spans)


# ---- the result ------------------------------------------------------------------------

@dataclass
class JointResult:
    """Changed shapes plus everything ``Model.join`` needs."""

    a: Shape
    b: Shape
    kind: str
    fit: str
    gap: float
    at: tuple[float, float, float] | None = None
    axis: tuple[float, float, float] | None = None
    hardware: list[str] = field(default_factory=list)
    note: str = ""
    features: dict[str, Shape] = field(default_factory=dict)
    # Separate printed pieces the joint needs (dowel pins): (shape, name)
    parts: list[tuple[Shape, str]] = field(default_factory=list)

    def join(self, model, a, b):
        """Record the joint on ``model`` between parts ``a`` and ``b`` (ids or Part3D).

        Separate printed pins are added as parts too, each joined to both parts.
        Returns the list of Joint records made.
        """
        made = []
        if self.parts:
            for shape, name in self.parts:
                pin = model.add(shape, name)
                for other in (a, b):
                    j = model.join(pin, other, self.kind, fit=self.fit, gap=self.gap,
                                   at=_centre(shape), axis=self.axis, note=self.note)
                    j.features = {"male": shape}
                    made.append(j)
            return made
        j = model.join(a, b, self.kind, fit=self.fit, gap=self.gap, hardware=self.hardware,
                       at=self.at, axis=self.axis, note=self.note)
        j.features = dict(self.features)
        made.append(j)
        return made


def _centre(shape: Shape) -> tuple[float, float, float]:
    c = shape.bounding_box().center()
    return (round(c.X, 3), round(c.Y, 3), round(c.Z, 3))


def _tuple(v: Vector) -> tuple[float, float, float]:
    return (round(v.X, 3), round(v.Y, 3), round(v.Z, 3))


# ---- pegs ------------------------------------------------------------------------------

def peg_size(face_thickness: float) -> tuple[float, float]:
    """A sensible peg diameter and length for a meeting face this thick (mm)."""
    d = min(10.0, max(5.0, round(0.4 * face_thickness * 2) / 2))
    length = min(12.0, max(6.0, round(1.2 * d)))
    return d, length


def peg_tools(at: Vector, direction: Vector, diameter: float, length: float, gap: float):
    """(peg to fuse onto a, the peg as it stands out of a, hole tool to cut from b)."""
    r = diameter / 2
    c = min(0.6, 0.12 * diameter)
    male_profile = [(0, 0), (r, 0), (r, length - c), (r - c, length), (0, length)]
    fused_profile = [(0, -OVERLAP)] + [(r, -OVERLAP)] + male_profile[2:]
    R = r + gap
    m = MOUTH_CHAMFER
    hole_profile = [
        (0, -1.0), (R + m + 1.0, -1.0), (R + m, 0.0), (R, m),
        (R, length + HOLE_EXTRA_DEPTH), (0, length + HOLE_EXTRA_DEPTH),
    ]
    return (_revolved(fused_profile, at, direction), _revolved(male_profile, at, direction),
            _revolved(hole_profile, at, direction))


def peg(a: Shape, b: Shape, at, direction, ctx=None, *, diameter: float = 6.0,
        length: float | None = None, fit: str = "snug", gap: float | None = None) -> JointResult:
    """Pegs standing out of ``a`` at each point in ``at``, into holes in ``b``.

    The hole is bigger than the peg by the fit gap on every side and 0.5 mm deeper than the
    peg, with a small chamfer at its mouth. The peg tip is chamfered to guide it in.
    """
    g = _gap(ctx, fit, gap)
    d = _unit(direction)
    length = length or peg_size(diameter / 0.4)[1]
    adds, males, holes = [], [], []
    for p in _points(at):
        add, male, hole = peg_tools(p, d, diameter, length, g)
        adds.append(add), males.append(male), holes.append(hole)
    pts = _points(at)
    n = len(pts)
    return JointResult(
        a=fuse(a, adds), b=cut(b, holes), kind="peg", fit=fit, gap=g,
        at=_tuple(pts[0]) if n == 1 else _centre(_compound(males)), axis=_tuple(d),
        note=f"{n} peg{'s' if n > 1 else ''}, {diameter:g} mm across and {length:g} mm long, "
             f"in holes {diameter + 2 * g:g} mm across",
        features={"male": _compound(males)},
    )


# ---- dowels ----------------------------------------------------------------------------

def dowel_tools(at: Vector, direction: Vector, diameter: float, length: float, gap: float):
    """(pin solid placed half in each part, hole tool for a, hole tool for b).

    ``diameter`` is the pin's; holes are pin + 2 x gap and 0.5 mm deeper than half the pin.
    """
    r = diameter / 2
    h = length / 2
    c = min(0.5, 0.1 * diameter)
    pin = _revolved([(0, -h), (r - c, -h), (r, -h + c), (r, h - c), (r - c, h), (0, h)], at, direction)
    R = r + gap
    m = MOUTH_CHAMFER
    depth = h + HOLE_EXTRA_DEPTH
    prof = [(0, -1.0), (R + m + 1.0, -1.0), (R + m, 0.0), (R, m), (R, depth), (0, depth)]
    hole_b = _revolved(prof, at, direction)
    hole_a = _revolved(prof, at, -direction)
    return pin, hole_a, hole_b


def dowel(a: Shape, b: Shape, at, direction, ctx=None, *, diameter: float = 6.0,
          length: float | None = None, fit: str = "snug", gap: float | None = None,
          pin: str = "printed") -> JointResult:
    """Holes in both parts joined by pins: printed pins (``r.parts``) or bought steel dowels.

    For ``pin="steel"`` the diameter and length should match a stock dowel (6x20 mm and so
    on); the hardware list then names it.
    """
    g = _gap(ctx, fit, gap)
    d = _unit(direction)
    length = length or max(10.0, round(2.5 * diameter))
    pins, ha, hb = [], [], []
    for p in _points(at):
        s, a_tool, b_tool = dowel_tools(p, d, diameter, length, g)
        pins.append(s), ha.append(a_tool), hb.append(b_tool)
    n = len(pins)
    steel = pin == "steel"
    res = JointResult(
        a=cut(a, ha), b=cut(b, hb), kind="dowel", fit=fit, gap=g,
        at=_centre(_compound(pins)), axis=_tuple(d),
        hardware=[f"{diameter:g}x{length:g} mm dowel pin"] * n if steel else [],
        note=(f"{n} {'steel dowel' if steel else 'printed pin'}{'s' if n > 1 else ''}, "
              f"{diameter:g} mm across and {length:g} mm long, in holes {diameter + 2 * g:g} mm across"),
        features={"male": _compound(pins), "pin": _compound(pins)},
    )
    if not steel:
        res.parts = [(s, f"Pin {i + 1}") for i, s in enumerate(pins)]
    return res


# ---- screws ----------------------------------------------------------------------------

def screw(a: Shape, b: Shape, at, direction, ctx=None, *, size: str = "M3", length: float | None = None,
          head: str = "cap", nut: str = "trap", fit: str = "hole") -> JointResult:
    """A screw through ``a`` into a nut or heat-set insert in ``b``.

    ``at`` is the point on the outside of ``a`` where the screw head goes; ``direction``
    points along the screw, from the head into ``a`` and on into ``b``. ``head`` is "cap",
    "button", "countersunk" or None (head sits on the surface). ``nut`` is "trap" (a hex
    pocket on the far side of ``b``) or "insert" (a heat-set insert hole where the screw
    enters ``b``). The screw length is chosen from stock lengths unless given.
    """
    hw = _library(ctx)
    g = hw._gap(fit)
    s = hw.screw(size)
    d = _unit(direction)
    head_kind = head
    head_names = {"cap": "socket head screw", "button": "button head screw",
                  "countersunk": "countersunk screw", None: "socket head screw"}
    if head_kind not in head_names:
        raise ValueError("head must be 'cap', 'button', 'countersunk' or None.")
    recess = {"cap": s["cap_head_h"] + 0.2, "button": s["button_head_h"] + 0.2,
              "countersunk": 0.0, None: 0.0}[head_kind]
    head_d = {"cap": s["cap_head_d"], "button": s["button_head_d"], "countersunk": s["csk_head_d"],
              None: s["cap_head_d"]}[head_kind]
    a_tools, b_tools, tools, hardware = [], [], [], []
    for p in _points(at):
        span_a = [sp for sp in _ray_spans(a, p, d) if sp[1] > 0.05]
        span_b = [sp for sp in _ray_spans(b, p, d) if sp[1] > 0.05]
        if not span_a or not span_b:
            raise ValueError("The screw line at that point doesn't pass through both parts. "
                             "Check 'at' is on the outside of the first part and 'direction' points into the second.")
        t_a = span_a[0][1]
        s_b, e_b = next(((x, y) for x, y in span_b if y > t_a - 0.05), span_b[0])
        a_tools.append(hw.screw_hole(size, length=t_a + 0.5, head=head_kind, at=p, direction=d, fit=fit,
                                     head_recess=recess if head_kind in ("cap", "button") else None))
        if nut == "insert":
            ins = hw.insert(size)
            entry = p + d * s_b
            b_tools.append(hw.insert_hole(size, at=entry, direction=d))
            need = s_b - recess + ins["length"] - 0.5
            longest = s_b - recess + ins["hole_depth"] - 0.3
            nut_name = f"{size} heat-set insert"
        else:
            nt = hw.nut(size)
            longest = e_b - recess
            # Pick the longest stock screw that stays inside b, and sink the nut pocket from
            # b's far side until the nut sits at the screw tip (leaving 1.5 mm of b at the entry).
            L = float(length) if length is not None else None
            if L is None:
                for cand in sorted(s["lengths"], reverse=True):
                    nut_start = recess + cand - nt["thickness"] - 0.3
                    if cand <= longest and nut_start >= s_b + 1.5:
                        L = float(cand)
                        break
            if L is None:  # nothing fits inside: shortest screw that reaches through a nut at the far face
                L = hw.screw_length(size, e_b - recess)
            n_depth = max(nt["thickness"] + 0.3, e_b - (recess + L - nt["thickness"] - 0.3))
            far = p + d * e_b
            b_tools.append(hw.nut_trap(size, at=far, direction=-d, depth=n_depth, fit=fit))
            b_tools.append(hw.screw_hole(size, length=e_b - s_b + 1.0, head=None, at=p + d * s_b,
                                         direction=d, fit=fit))
            need = L
            nut_name = f"{size} nut"
        if length is not None:
            L = float(length)
        elif nut == "insert":
            L = hw.screw_length(size, need)
        if L > longest + 1.0:
            note_extra = f" The {L:g} mm screw sticks out {L - longest:.1f} mm past the nut side."
        else:
            note_extra = ""
        hardware += [f"{size}x{L:g} {head_names[head_kind]}", nut_name]
        tools.append(_tool_path(p, d, recess, head_d, s["hex_key"]))
    n = len(_points(at))
    res = JointResult(
        a=cut(a, a_tools), b=cut(b, b_tools), kind="screw", fit=fit, gap=g,
        at=_tuple(_points(at)[0]), axis=_tuple(-d), hardware=hardware,
        note=f"{n} {size} screw{'s' if n > 1 else ''} into {'heat-set inserts' if nut == 'insert' else 'nuts'}."
             + note_extra,
        features={"tool": _compound(tools)},
    )
    return res


def _tool_path(p: Vector, d: Vector, recess: float, head_d: float, key: float, reach: float = 100.0) -> Solid:
    """The space a driver needs: a hex key down the counterbore, then a hand-sized cylinder outward."""
    parts = [_cylinder(head_d + 1.0, -reach, -0.2, p, d)]
    if recess > 0.3:
        parts.append(_cylinder(key * 1.2, -0.1, recess - 0.2, p, d))
    return fuse(parts[0], parts[1:])


# ---- dovetail and tongue-and-groove --------------------------------------------------------

def _offset_trapezoid(half_root: float, half_tip: float, height: float, g: float, extra_depth: float,
                      below: float):
    """The groove outline: the tongue outline grown by ``g`` on the slanted sides,
    ``extra_depth`` past the tip, and continued ``below`` under the root."""
    slope = (half_tip - half_root) / height  # dx/dz of each side
    # Offsetting a line by g perpendicular widens it by g / cos(theta) horizontally.
    widen = g * math.sqrt(1 + slope * slope)
    top = height + extra_depth

    def hw(z):
        return half_root + slope * z + widen

    return [(-hw(-below), -below), (hw(-below), -below), (hw(top), top), (-hw(top), top)]


def _ridge(a: Shape, b: Shape, at, direction, along, ctx, *, width, height, length, angle, fit, gap,
           kind: str) -> JointResult:
    g = _gap(ctx, fit, gap)
    p = _points(at)[0]
    d = _unit(direction)
    frame = _frame(p, d, along)
    slide = frame.y_dir
    half_root = width / 2
    half_tip = half_root + height * math.tan(math.radians(angle))
    if length is None:
        y0, y1 = -2000.0, 2000.0
        open_both = True
    else:
        y0, y1 = -length / 2, length / 2
        open_both = False
    slope = (half_tip - half_root) / height
    tongue_poly = [(-(half_root - slope * OVERLAP), -OVERLAP), (half_root - slope * OVERLAP, -OVERLAP),
                   (half_tip, height), (-half_tip, height)]
    male_poly = [(-half_root, 0.0), (half_root, 0.0), (half_tip, height), (-half_tip, height)]
    tongue = _prism(tongue_poly, y0, y1, frame)
    male = _prism(male_poly, y0, y1, frame)
    if open_both:
        # Clip the tongue to the face of a it stands on, so it never sticks out past a's outline.
        footprint = _face_prism(a, p, d, height)
        if footprint is None:
            raise ValueError("Couldn't find a flat face of the first part at 'at' facing 'direction'.")
        tongue = one_solid(tongue.intersect(footprint))
        male = one_solid(male.intersect(footprint))
    groove_poly = _offset_trapezoid(half_root, half_tip, height, g, HOLE_EXTRA_DEPTH, 1.0)
    if open_both:
        gy0, gy1 = -2000.0, 2000.0
    else:
        gy0, gy1 = y0 - g, 2000.0  # open toward +slide so b slides on from that side
    groove = _prism(groove_poly, gy0, gy1, frame)
    word = "dovetail" if kind == "dovetail" else "tongue"
    return JointResult(
        a=fuse(a, [tongue]), b=cut(b, [groove]), kind=kind, fit=fit, gap=g,
        at=_tuple(p), axis=_tuple(slide if kind == "dovetail" else d),
        note=f"a {width:g} mm wide, {height:g} mm tall {word} with {g:g} mm clearance each side",
        features={"male": male},
    )


def _face_prism(shape: Shape, p: Vector, d: Vector, height: float) -> Solid | None:
    """The flat face of ``shape`` through ``p`` with outward normal ``d``, extruded ``height`` along d
    and ``OVERLAP`` back into the part."""
    best = None
    for f in shape.faces():
        try:
            n = f.normal_at(f.center())
        except Exception:
            continue
        if n.dot(d) < 0.999:
            continue
        if abs((f.center() - p).dot(d)) > 1e-3:
            continue
        best = f if best is None or f.area > best.area else best
    if best is None:
        return None
    from build123d import Pos

    base = Pos(*(-d * OVERLAP)) * best
    return Solid.extrude(base, d * (height + OVERLAP))


def dovetail(a: Shape, b: Shape, at, direction, slide, ctx=None, *, width: float = 10.0,
             height: float = 6.0, length: float | None = None, angle: float = 15.0,
             fit: str = "sliding", gap: float | None = None) -> JointResult:
    """A dovetail tongue on ``a`` sliding into a matching slot in ``b``.

    ``at`` is the middle of the tongue's root on the meeting face; ``direction`` points from
    ``a`` into ``b``; the tongue runs along ``slide``. ``width`` is the root width (the tip is
    wider by ``angle`` degrees per side). With ``length=None`` the tongue runs right across
    the face of ``a`` and the slot runs right through ``b``; otherwise the slot is open toward
    +``slide`` so ``b`` slides on from that side.
    """
    return _ridge(a, b, at, direction, slide, ctx, width=width, height=height, length=length,
                  angle=angle, fit=fit, gap=gap, kind="dovetail")


def tongue_groove(a: Shape, b: Shape, at, direction, along, ctx=None, *, width: float = 4.0,
                  height: float = 4.0, length: float | None = None, fit: str = "snug",
                  gap: float | None = None) -> JointResult:
    """A straight ridge on ``a`` along ``along`` that pushes into a groove in ``b``."""
    return _ridge(a, b, at, direction, along, ctx, width=width, height=height, length=length,
                  angle=0.0, fit=fit, gap=gap, kind="tongue-groove")


# ---- snap hooks --------------------------------------------------------------------------

def snap_hook(a: Shape, b: Shape, at, direction, hook, ctx=None, *, length: float = 12.0,
              width: float = 6.0, thickness: float | None = None, undercut: float | None = None,
              fit: str = "sliding", gap: float | None = None) -> JointResult:
    """A flexible cantilever hook on ``a`` whose lip clicks into a recess in ``b``'s wall.

    The beam starts at ``at`` on ``a`` and runs ``length`` mm along ``direction``. Its outer
    face lies in the plane through ``at`` facing ``hook``; the inside of ``b``'s wall should
    sit in that plane. The lip at the tip sticks out ``undercut`` mm along ``hook`` into a
    recess cut into ``b``. The undercut defaults to what PLA can bend through without
    breaking (about 2% strain), capped at 1.2 mm.
    """
    g = _gap(ctx, fit, gap)
    p = _points(at)[0]
    d = _unit(direction)
    hk = _unit(hook)
    t = thickness or max(1.6, round(length / 7, 1))
    # Allowed deflection of a cantilever: y = 2/3 * strain * L^2 / t (strain 2% for PLA).
    y_allowed = 2 / 3 * 0.02 * length ** 2 / t
    u = undercut or round(min(1.2, max(0.4, y_allowed)), 2)
    lip_h = max(2.0, 1.5 * u)  # lip length along the beam
    # Local frame: z = hook (outward), y = direction (along the beam).
    frame = _frame(p, hk, d)
    # beam: x across width, y along length, z from -t (inside) to 0 (outer face)
    beam = _box(-width / 2, width / 2, -OVERLAP, length, -t, 0.0, frame)
    # lip profile in (y, z): retaining face square at y0, ramp to the tip at y = length
    y_ret = length - lip_h
    lip_pts = [(y_ret, -0.1), (y_ret, u), (y_ret + 0.3 * lip_h, u), (length, -0.1)]
    lip_wire = Wire.make_polygon([Vector(-width / 2, y, z) for y, z in lip_pts], close=True)
    lip_local = Solid.extrude(Face(lip_wire), Vector(width, 0, 0))
    lip = frame.location * lip_local
    male_pts = [(y_ret, 0.0), (y_ret, u), (y_ret + 0.3 * lip_h, u), (length, 0.0)]
    male_wire = Wire.make_polygon([Vector(-width / 2, y, z) for y, z in male_pts], close=True)
    male = frame.location * Solid.extrude(Face(male_wire), Vector(width, 0, 0))
    recess = _box(-width / 2 - g, width / 2 + g, y_ret - g, length + g, -1.0, u + HOLE_EXTRA_DEPTH, frame)
    return JointResult(
        a=fuse(a, [beam, lip]), b=cut(b, [recess]), kind="snap", fit=fit, gap=g,
        at=_tuple(p), axis=_tuple(-d),
        note=f"a {length:g} mm snap hook, {t:g} mm thick, with a {u:g} mm lip",
        features={"male": male},
    )

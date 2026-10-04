"""Splitting parts that are too big for the printer into pieces that join together.

``split_oversized(model, ctx, options)`` is called by the build right after the design
runs. Parts that fit the printer are left alone. Each part that doesn't is cut with flat
planes into the fewest pieces that fit, and joints (pegs by default) are added on every
cut face so the pieces fit together on the first print.

How cuts are chosen:

1. Work out how many pieces are needed along each axis, trying every way the piece could
   be turned on the bed (x, y and z swapped), and keep the plan with the fewest pieces.
2. Along the axis that needs the most pieces, measure the cross-section (area and
   outline length) every few millimetres from a triangle mesh of the part.
3. Score every position: a big, solid, compact cross-section is good (room for joints);
   a place where the cross-section changes nearby (a hole, a rib, the edge of a feature)
   is bad. Pieces of about equal length are preferred.
4. Pick the cheapest set of positions with dynamic programming, with every piece no
   longer than the printer allows. Repeat on each piece until everything fits.

``options`` (from ``split:`` in project.yaml):

- ``joint``: "peg" (default), "dowel", "screw", "dovetail" or "tongue-groove".
- ``fit``: fit name for the joints (default "snug").
- ``seams``: exact cuts, e.g. ``[{"axis": "x", "at": 300.0}]`` in model coordinates.
  A seam with ``part: P02`` cuts that part even if it fits.
- ``dowel``: "printed" (default) or "steel" pins.
- ``screw``: screw size for screw joints (default "M3").
"""

from __future__ import annotations

import colorsys
import itertools
import math

import numpy as np
from build123d import Compound, GeomType, Keep, Plane, Shape, Solid, Vector

from . import joints as J
from .design import Joint, Model, Part3D

EDGE_MARGIN = 2.0  # mm kept clear of the bed edges on each side
AXES = "xyz"
SIDE_WORDS = {0: ("left", "right"), 1: ("front", "back"), 2: ("bottom", "top")}
SIZE_WORDS = {0: "long", 1: "deep", 2: "tall"}
KNOWN_JOINTS = ("peg", "dowel", "screw", "dovetail", "tongue-groove")
STEEL_DOWELS = {"d": [3, 4, 5, 6, 8, 10], "l": [10, 12, 16, 20, 25, 30, 40]}


# ---- fitting the bed ----------------------------------------------------------------

def usable_bed(bed) -> tuple[float, float, float]:
    """Space a piece may use: bed minus a margin at the edges, and a little off the height."""
    return (bed[0] - 2 * EDGE_MARGIN, bed[1] - 2 * EDGE_MARGIN, bed[2] - EDGE_MARGIN)


def size_of(shape: Shape) -> tuple[float, float, float]:
    bb = shape.bounding_box()
    return (bb.size.X, bb.size.Y, bb.size.Z)


def fits_bed(size, bed, tol: float = 1e-6) -> bool:
    """True when the size fits the usable bed in some axis-aligned rotation."""
    use = usable_bed(bed)
    return any(all(size[i] <= use[p[i]] + tol for i in range(3)) for p in itertools.permutations(range(3)))


def _plan(size, bed, reserve: float = 0.0) -> tuple[tuple[int, int, int], tuple[float, float, float]]:
    """Pieces needed per axis and the length allowed per axis, for the best rotation.

    ``reserve`` is room kept along a cut axis for what a joint adds (a peg standing out).
    """
    use = usable_bed(bed)
    best = None
    for perm in itertools.permutations(range(3)):
        allowed = tuple(use[perm[i]] if size[i] <= use[perm[i]] + 1e-6 else use[perm[i]] - reserve
                        for i in range(3))
        counts = tuple(max(1, math.ceil(size[i] / allowed[i] - 1e-9)) for i in range(3))
        # fewest pieces, then smallest total cut area, then a fixed order
        cut_area = sum((counts[i] - 1) * size[(i + 1) % 3] * size[(i + 2) % 3] for i in range(3))
        key = (math.prod(counts), cut_area)
        if best is None or key < best[0]:
            best = (key, counts, allowed)
    return best[1], best[2]


# ---- choosing where to cut ---------------------------------------------------------------

def _profile(mesh, axis: int, xs: np.ndarray):
    """Cross-section area and outline length of a mesh at each position along an axis."""
    import trimesh

    normal = np.eye(3)[axis]
    lines, tfs, faces = trimesh.intersections.mesh_multiplane(mesh, np.zeros(3), normal, heights=xs)
    areas = np.zeros(len(xs))
    perims = np.zeros(len(xs))
    for i, (seg, tf, fidx) in enumerate(zip(lines, tfs, faces)):
        if len(seg) == 0:
            continue
        n_local = (tf[:3, :3].T @ mesh.face_normals[fidx].T).T[:, :2]
        e = seg[:, 1] - seg[:, 0]
        outward = np.stack([e[:, 1], -e[:, 0]], 1)
        flip = (outward * n_local).sum(1) < 0
        p0 = np.where(flip[:, None], seg[:, 1], seg[:, 0])
        p1 = np.where(flip[:, None], seg[:, 0], seg[:, 1])
        areas[i] = 0.5 * np.sum(p0[:, 0] * p1[:, 1] - p1[:, 0] * p0[:, 1])
        perims[i] = np.linalg.norm(e, axis=1).sum()
    return np.maximum(areas, 0.0), perims


def choose_cuts(shape: Shape, axis: int, pieces: int, max_len: float) -> list[float]:
    """Positions along ``axis`` that cut ``shape`` into ``pieces`` pieces no longer than ``max_len``."""
    from .meshing import to_mesh

    bb = shape.bounding_box()
    lo, hi = tuple(bb.min)[axis], tuple(bb.max)[axis]
    span = hi - lo
    mesh = to_mesh(shape, tolerance=0.1)
    for n in range(pieces, pieces + 4):
        # Sample positions are an exact fraction of the length, so equal pieces are possible.
        k = n * max(1, int(round(span / min(4.0, max(0.5, span / 250)) / n)))
        step = span / k
        xs = lo + step * np.arange(1, k)
        cost = _cut_costs(mesh, axis, xs, step)
        cuts = _best_cuts(xs, cost, lo, hi, n, max_len)
        if cuts is not None:
            return cuts
    raise ValueError("Couldn't find places to cut this part so every piece fits.")


def _cut_costs(mesh, axis: int, xs: np.ndarray, step: float) -> np.ndarray:
    """How bad a cut is at each position (lower is better)."""
    # A tiny odd offset keeps sample planes off exact vertex positions.
    areas, perims = _profile(mesh, axis, xs + 0.0137)
    a_max = max(areas.max(), 1e-9)
    compact = np.where(perims > 0, 4 * math.pi * areas / np.maximum(perims, 1e-9) ** 2, 0.0)
    c_max = max(compact.max(), 1e-9)
    # How much the cross-section changes within a peg's depth either side of each position:
    # a hole, a rib or the edge of a feature nearby.
    w = max(1, int(math.ceil(10.0 / step)))
    change = np.zeros(len(xs))
    for i in range(len(xs)):
        window = areas[max(0, i - w): i + w + 1]
        change[i] = np.abs(window - areas[i]).max() / a_max
    cost = 2.0 * (1 - areas / a_max) + 1.0 * (1 - compact / c_max) + 4.0 * change
    return np.where(areas <= 1e-6, 1e6, cost)


def _best_cuts(xs, cost, lo, hi, n, max_len):
    """Dynamic programming over sample positions: n - 1 cuts, every piece <= max_len."""
    if n <= 1:
        return [] if hi - lo <= max_len else None
    ideal = (hi - lo) / n
    min_len = max(5.0, 0.25 * ideal)

    def balance(length):
        return 1.5 * ((length - ideal) / ideal) ** 2

    m = len(xs)
    inf = float("inf")
    first = xs - lo
    dp = np.where((first <= max_len) & (first >= min_len), cost + balance(first), inf)
    back = []
    diff = xs[None, :] - xs[:, None]  # diff[i, j] = xs[j] - xs[i]
    ok = (diff <= max_len) & (diff >= min_len)
    trans = np.where(ok, balance(np.where(ok, diff, ideal)), inf)
    for _ in range(n - 2):
        total = dp[:, None] + trans  # from i to j
        arg = np.argmin(total, axis=0)
        dp = total[arg, np.arange(m)] + cost
        back.append(arg)
    last = hi - xs
    final = np.where((last <= max_len) & (last >= min_len), dp + balance(last), inf)
    j = int(np.argmin(final))
    if not np.isfinite(final[j]):
        return None
    cuts = [j]
    for arg in reversed(back):
        j = int(arg[j])
        cuts.append(j)
    return sorted(round(float(xs[k]), 2) for k in cuts)


# ---- cutting -----------------------------------------------------------------------------

def _plane(axis: int, at: float) -> Plane:
    origin = [0.0, 0.0, 0.0]
    origin[axis] = at
    return Plane(origin=tuple(origin), z_dir=tuple(np.eye(3)[axis]))


def _cut_along(shape: Shape, axis: int, cuts: list[float]) -> list[list[Solid]]:
    """Cut at each position; returns the solids of each slab, low to high."""
    slabs: list[list[Solid]] = []
    rest = shape
    for at in sorted(cuts):
        result = rest.split(_plane(axis, at), keep=Keep.BOTH)
        below, above = [], []
        for part in result if isinstance(result, (list, tuple)) else [result]:
            if part is None:
                continue
            for s in part.solids():
                (below if tuple(s.center())[axis] < at else above).append(s)
        slabs.append(below)
        rest = Compound(above) if len(above) != 1 else above[0]
    slabs.append(list(rest.solids()))
    return [sorted(s, key=_solid_key) for s in slabs]


def _solid_key(s: Shape):
    c = s.center()
    return (round(c.X, 2), round(c.Y, 2), round(c.Z, 2))


class _Piece:
    def __init__(self, shape, path):
        self.shape = shape
        self.path = path  # [(axis, index, count, solid_no, solids)] from each cutting level


def _partition(shape: Shape, bed, seams: list[tuple[int, float]], cuts_log: list, reserve: float = 0.0,
               depth: int = 0, path=()) -> list[_Piece]:
    """Cut ``shape`` until every piece fits. Manual seams are applied first."""
    size = size_of(shape)
    if seams:
        bb = shape.bounding_box()
        mine = [(a, x) for a, x in seams
                if tuple(bb.min)[a] + 0.5 < x < tuple(bb.max)[a] - 0.5]
        if mine:
            axis = mine[0][0]
            here = sorted(x for a, x in mine if a == axis)
            others = [(a, x) for a, x in seams if a != axis]
            return _apply(shape, axis, here, bed, others, cuts_log, reserve, depth, path, manual=True)
    if fits_bed(size, bed) or depth > 6:
        return [_Piece(shape, list(path))]
    counts, allowed = _plan(size, bed, reserve)
    axis = max(range(3), key=lambda i: (counts[i], size[i], -i))
    cuts = choose_cuts(shape, axis, counts[axis], allowed[axis])
    return _apply(shape, axis, cuts, bed, [], cuts_log, reserve, depth, path, manual=False)


def _apply(shape, axis, cuts, bed, seams, cuts_log, reserve, depth, path, manual):
    for x in cuts:
        cuts_log.append((axis, x, manual))
    slabs = _cut_along(shape, axis, cuts)
    out = []
    for i, solids in enumerate(slabs):
        for k, s in enumerate(solids):
            step = (axis, i, len(slabs), k, len(solids))
            out += _partition(s, bed, seams, cuts_log, reserve, depth + 1, path + (step,))
    return out


# ---- the joint faces ------------------------------------------------------------------------

def _on_plane(f, axis: int, at: float) -> bool:
    return abs(tuple(f.center())[axis] - at) <= 1e-3 and tuple(f.bounding_box().size)[axis] <= 1e-3


def _faces_on_plane(shape: Shape, axis: int, at: float):
    return [f for f in shape.faces() if _on_plane(f, axis, at)]


def _contacts(pieces: list[_Piece], cuts_log) -> list[dict]:
    """Every pair of pieces touching across a cut plane, with the shared faces."""
    planes = sorted({(a, x) for a, x, _ in cuts_log})
    boxes = [p.shape.bounding_box() for p in pieces]
    found = []
    for axis, at in planes:
        lows = [i for i, b in enumerate(boxes) if abs(tuple(b.max)[axis] - at) < 1e-3]
        highs = [i for i, b in enumerate(boxes) if abs(tuple(b.min)[axis] - at) < 1e-3]
        for i in lows:
            for j in highs:
                bi, bj = boxes[i], boxes[j]
                if any(tuple(bi.max)[k] <= tuple(bj.min)[k] + 1e-3 or
                       tuple(bj.max)[k] <= tuple(bi.min)[k] + 1e-3 for k in range(3) if k != axis):
                    continue
                shared = []
                for fi in _faces_on_plane(pieces[i].shape, axis, at):
                    for fj in _faces_on_plane(pieces[j].shape, axis, at):
                        try:
                            common = fi.intersect(fj)
                        except Exception:
                            common = None
                        if common:
                            shared += [f for f in common if hasattr(f, "area") and f.area > 0.5]
                area = sum(f.area for f in shared)
                if area > 1.0:
                    found.append({"low": i, "high": j, "axis": axis, "at": at, "faces": shared, "area": area})
    return found


def _in_plane_axes(axis: int) -> tuple[int, int]:
    return tuple(k for k in range(3) if k != axis)  # type: ignore[return-value]


class _Section2D:
    """A cut face flattened to 2D (the two other axes) for placing joints."""

    def __init__(self, faces, axis: int):
        self.axis = axis
        self.u, self.v = _in_plane_axes(axis)
        tris, segs = [], []
        for f in faces:
            verts, triangles = f.tessellate(0.05, 0.2)
            pts = np.array([(tuple(p)[self.u], tuple(p)[self.v]) for p in verts])
            t = np.array(triangles, dtype=int).reshape(-1, 3)
            tris.append(pts[t])
            edges = {}
            for a, b, c in t:
                for e in ((a, b), (b, c), (c, a)):
                    key = (min(e), max(e))
                    edges[key] = edges.get(key, 0) + 1
            segs += [(pts[a], pts[b]) for (a, b), n in edges.items() if n == 1]
        self.tris = np.concatenate(tris) if tris else np.zeros((0, 3, 2))
        self.segs = np.array(segs) if segs else np.zeros((0, 2, 2))
        allp = self.tris.reshape(-1, 2)
        self.lo = allp.min(0)
        self.hi = allp.max(0)

    def inside(self, p: np.ndarray) -> np.ndarray:
        a, b, c = self.tris[:, 0], self.tris[:, 1], self.tris[:, 2]
        v0, v1 = b - a, c - a
        den = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]
        den = np.where(np.abs(den) < 1e-12, 1e-12, den)
        d = p[:, None, :] - a[None]
        s = (d[..., 0] * v1[None, :, 1] - v1[None, :, 0] * d[..., 1]) / den
        t = (v0[None, :, 0] * d[..., 1] - d[..., 0] * v0[None, :, 1]) / den
        return ((s >= -1e-9) & (t >= -1e-9) & (s + t <= 1 + 1e-9)).any(1)

    def edge_distance(self, p: np.ndarray) -> np.ndarray:
        a, b = self.segs[:, 0], self.segs[:, 1]
        ab = b - a
        L2 = np.maximum((ab ** 2).sum(1), 1e-12)
        ap = p[:, None, :] - a[None]
        t = np.clip((ap * ab[None]).sum(2) / L2, 0, 1)
        closest = a[None] + t[..., None] * ab[None]
        return np.sqrt(((p[:, None, :] - closest) ** 2).sum(2)).min(1)

    def grid(self):
        ext = self.hi - self.lo
        h = float(np.clip(min(ext) / 16, 0.4, 3.0))
        def axis_points(lo, hi):  # odd count, centred, so the middle is always a candidate
            n = int((hi - lo) / h) | 1
            return lo + (hi - lo - (n - 1) * h) / 2 + h * np.arange(n)

        us = axis_points(self.lo[0], self.hi[0])
        vs = axis_points(self.lo[1], self.hi[1])
        P = np.array([(u, v) for u in us for v in vs])
        if len(P) == 0:
            return P, np.zeros(0), h
        keep = self.inside(P)
        P = P[keep]
        return P, self.edge_distance(P) if len(P) else np.zeros(0), h

    def to3d(self, p, at: float) -> Vector:
        xyz = [0.0, 0.0, 0.0]
        xyz[self.axis] = at
        xyz[self.u], xyz[self.v] = float(p[0]), float(p[1])
        return Vector(*xyz)


def _spread(P: np.ndarray, k: int, min_sep: float, edge: np.ndarray | None = None,
            weight: float = 1.5) -> list[int]:
    """Order points so each next one is far from those already picked and well inside the face.

    Score = distance to the nearest point already picked (or to the middle, for the first)
    + ``weight`` x distance to the face edge. Stops after ``k`` points or when nothing is
    at least ``min_sep`` from the points picked.
    """
    if len(P) == 0:
        return []
    bonus = weight * (edge if edge is not None else np.zeros(len(P)))
    centre = P.mean(0)
    dmin = np.sqrt(((P - centre) ** 2).sum(1))
    chosen: list[int] = []
    while len(chosen) < k:
        score = np.where(dmin >= (min_sep if chosen else 0.0), dmin + bonus, -np.inf)
        if chosen:
            score[chosen] = -np.inf
        j = int(np.argmax(score))
        if not np.isfinite(score[j]):
            break
        chosen.append(j)
        dmin = np.minimum(dmin, np.sqrt(((P - P[j]) ** 2).sum(1))) if len(chosen) > 1 else \
            np.sqrt(((P - P[j]) ** 2).sum(1))
    return chosen


def _contained(container: Shape, tool: Shape, share: float = 0.995) -> bool:
    """True when ``tool`` lies (almost) entirely inside ``container``."""
    cb, tb = container.bounding_box(), tool.bounding_box()
    if any(tuple(tb.min)[k] < tuple(cb.min)[k] - 1e-6 or tuple(tb.max)[k] > tuple(cb.max)[k] + 1e-6
           for k in range(3)):
        return False
    try:
        common = container.intersect(tool)
    except Exception:
        return False
    vol = sum(s.volume for s in common.solids()) if common is not None else 0.0
    return vol >= share * tool.volume


def _boxes_clash(bb, others, tag=None) -> bool:
    """True when ``bb`` overlaps a keep-out box from another cut face (same-face spacing is
    handled exactly in 2D)."""
    for o, otag in others:
        if tag is not None and otag == tag:
            continue
        if all(tuple(bb.min)[k] < tuple(o.max)[k] and tuple(o.min)[k] < tuple(bb.max)[k]
               for k in range(3)):
            return True
    return False


# ---- placing joints on one contact -------------------------------------------------------------

class _Joiner:
    def __init__(self, pieces, ctx, options, messages, face_down):
        self.pieces = pieces
        self.ctx = ctx
        self.kind = options.get("joint", "peg") or "peg"
        self.fit = options.get("fit", "snug") or "snug"
        self.gap = ctx.fit(self.fit)
        self.dowel = options.get("dowel", "printed")
        self.screw_size = options.get("screw", "M3")
        self.wall = max(2 * ctx.min_wall + self.gap, 2.0)
        self.messages = messages
        self.face_down = face_down
        self.adds = {i: [] for i in range(len(pieces))}
        self.subs = {i: [] for i in range(len(pieces))}
        self.keepouts = {i: [] for i in range(len(pieces))}
        self.joints: list[dict] = []
        self.pins: list[Shape] = []
        self.notes: list[str] = []
        self.cut_planes: set = set()
        self.male_on: set = set()
        self.tag = 0  # which cut face is being joined (keep-outs from the same face don't clash)

    # which piece carries the male feature
    def _male_low(self, c) -> bool:
        axis = c["axis"]
        up = np.eye(3)[axis]  # direction a peg on the low piece points
        if self.face_down:
            sign = -1.0 if self.face_down.startswith("-") else 1.0
            down = np.eye(3)["XYZ".index(self.face_down[-1].upper())] * sign
            if float(np.dot(up, down)) > 0.5:
                return False
            return True
        low, high = self.pieces[c["low"]].shape, self.pieces[c["high"]].shape
        low_big = _cut_face_is_largest(low, axis, c["at"])
        high_big = _cut_face_is_largest(high, axis, c["at"])
        if low_big and not high_big:
            return False
        return True

    def add(self, c) -> None:
        before = len(self.joints)
        self._add(c)
        for jd in self.joints[before:]:
            jd["plane"] = (c["axis"], c["at"])
            jd["out"] = tuple(self.out)  # from the male piece toward the other

    def _add(self, c) -> None:
        self.tag += 1
        kind = self.kind
        if kind not in KNOWN_JOINTS:
            self.notes.append(f"unknown:{kind}")
            kind = "peg"
        male_low = self._male_low(c)
        # A piece never gets pegs standing out on both ends along one axis: the room kept
        # for a peg when choosing the cuts covers one end only.
        a_i, b_i = (c["low"], c["high"]) if male_low else (c["high"], c["low"])
        if (a_i, c["axis"]) in self.male_on and (b_i, c["axis"]) not in self.male_on:
            male_low = not male_low
            a_i, b_i = b_i, a_i
        self.male_on.add((a_i, c["axis"]))
        sign = 1.0 if male_low else -1.0
        d = Vector(*(np.eye(3)[c["axis"]] * sign))
        self.out = d
        sec = _Section2D(c["faces"], c["axis"])
        done = False
        if kind in ("dovetail", "tongue-groove"):
            done = self._ridge(c, sec, a_i, b_i, d, kind)
            if not done:
                self.notes.append(f"ridge_fallback:{kind}")
                kind = "peg"
        if kind == "screw":
            done = self._screws(c, sec, a_i, b_i, d)
            if not done:
                self.notes.append("screw_fallback")
                kind = "peg"
        if not done and kind in ("peg", "dowel"):
            done = self._pegs(c, sec, a_i, b_i, d, kind)
        if not done:
            self.joints.append(dict(a=a_i, b=b_i, kind="glue", features={}, hardware=[],
                                    at=_centre_of(c["faces"]), axis=d,
                                    note=f"the meeting face is too small for {kind}s; glue the faces together"))
            self.notes.append("glue")

    # pegs and dowels
    def _pegs(self, c, sec, a_i, b_i, d, kind) -> bool:
        P, dist, h = sec.grid()
        if len(P) == 0:
            return False
        m = float(dist.max())
        best_dia, length = J.peg_size(2 * m)
        g, wall = self.gap, self.wall
        A, B = self.pieces[a_i].shape, self.pieces[b_i].shape

        def plan2d(dia):
            """Valid points for this peg size, best-spread first, and how many fit apart."""
            R = dia / 2 + g
            ok = dist >= R + wall
            Pv = P[ok]
            if len(Pv) == 0:
                return None
            extent = float(np.sqrt(((Pv.max(0) - Pv.min(0)) ** 2).sum()))
            k = int(np.clip(1 + extent // (4 * dia), 2, 4))
            sep = 2 * (R + wall)
            order = _spread(Pv, len(Pv), 0.0, dist[ok])
            fits = len(_spread(Pv, k, sep, dist[ok]))
            return Pv, order, k, sep, fits

        # The biggest peg that still allows two pegs (one round peg alone lets pieces twist).
        sizes = [d for d in np.arange(best_dia, 3.99, -0.5)]
        plans = [(d, plan2d(d)) for d in sizes]
        plans = [(d, pl) for d, pl in plans if pl is not None]
        if not plans:
            return False
        dia, (Pv, order, k, min_sep, _) = next(((d, pl) for d, pl in plans if pl[4] >= 2), plans[0])
        dia = float(dia)
        chosen: list[Vector] = []
        chosen2d: list[np.ndarray] = []
        lengths = []
        for idx in order:
            if len(chosen) >= k:
                break
            q = Pv[idx]
            if any(np.hypot(*(q - cq)) < min_sep for cq in chosen2d):
                continue
            p3 = sec.to3d(q, c["at"])
            placed = self._fit_site(p3, d, dia, length, A, B, a_i, b_i, kind)
            if placed is None:
                continue
            chosen.append(p3)
            chosen2d.append(q)
            lengths.append(placed)
        if not chosen:
            return False
        L = min(lengths)
        g = self.gap
        if kind == "peg":
            males = []
            for p3 in chosen:
                add, male, hole = J.peg_tools(p3, d, dia, L, g)
                self.adds[a_i].append(add)
                self.subs[b_i].append(hole)
                males.append(male)
            self.joints.append(dict(
                a=a_i, b=b_i, kind="peg", hardware=[], at=_centre_of(c["faces"]), axis=d,
                features={"male": J._compound(males)},
                note=f"{len(chosen)} peg{'s' if len(chosen) > 1 else ''} {dia:g} mm across and {L:g} mm long "
                     f"in holes {dia + 2 * g:g} mm across",
                count=len(chosen)))
            if len(chosen) == 1:
                self.notes.append("single")
            return True
        # dowels: pin length 2 x L, holes half that (+0.5) in each piece
        steel = self.dowel == "steel"
        pin_d, pin_len = dia, 2 * L
        if steel:
            pin_d = max([s for s in STEEL_DOWELS["d"] if s <= dia] or [STEEL_DOWELS["d"][0]])
            pin_len = max([s for s in STEEL_DOWELS["l"] if s <= 2 * L] or [STEEL_DOWELS["l"][0]])
        pins = []
        for p3 in chosen:
            pin, hole_a, hole_b = J.dowel_tools(p3, d, pin_d, pin_len, g)
            self.subs[a_i].append(hole_a)
            self.subs[b_i].append(hole_b)
            pins.append(pin)
        if steel:
            self.joints.append(dict(
                a=a_i, b=b_i, kind="dowel", at=_centre_of(c["faces"]), axis=d,
                hardware=[f"{pin_d:g}x{pin_len:g} mm dowel pin"] * len(pins),
                features={"pin": J._compound(pins)},
                note=f"{len(pins)} steel dowel pin{'s' if len(pins) > 1 else ''} {pin_d:g} x {pin_len:g} mm "
                     f"in holes {pin_d + 2 * g:g} mm across", count=len(pins)))
        else:
            for pin in pins:
                self.joints.append(dict(a=a_i, b=b_i, kind="dowel", at=_centre_of(c["faces"]), axis=d,
                                        hardware=[], features={"pin": pin}, pin=pin,
                                        note=f"a printed pin {pin_d:g} mm across and {pin_len:g} mm long, "
                                             f"in holes {pin_d + 2 * g:g} mm across", count=1))
        return True

    def _fit_site(self, p3, d, dia, length, A, B, a_i, b_i, kind):
        """Check a joint site in 3D: enough material around the hole(s). Returns the length used."""
        g, wall = self.gap, self.wall
        R = dia / 2 + g
        for L in (length, max(5.0, round(0.65 * length))):
            depth_b = L + J.HOLE_EXTRA_DEPTH + wall
            keep_b = J._cylinder(2 * (R + wall), 0.0, depth_b, p3, d)
            if kind == "dowel":
                keep_a = J._cylinder(2 * (R + wall), 0.0, depth_b, p3, -d)
            else:
                keep_a = J._cylinder(2 * (dia / 2 + wall), 0.0, wall + J.OVERLAP + 1.0, p3, -d)
            kb, ka = keep_b.bounding_box(), keep_a.bounding_box()
            if _boxes_clash(kb, self.keepouts[b_i], self.tag) or _boxes_clash(ka, self.keepouts[a_i], self.tag):
                return None
            if _contained(B, keep_b) and _contained(A, keep_a):
                self.keepouts[b_i].append((kb, self.tag))
                self.keepouts[a_i].append((ka, self.tag))
                return L
        return None

    # dovetail / tongue-and-groove
    def _ridge(self, c, sec, a_i, b_i, d, kind) -> bool:
        P, dist, h = sec.grid()
        if len(P) == 0:
            return False
        ext = sec.hi - sec.lo
        A, B = self.pieces[a_i].shape, self.pieces[b_i].shape
        g, wall = self.gap if kind == "tongue-groove" else self.ctx.fit("sliding"), self.wall
        # slide along the shorter in-plane direction whose ends are not against another piece
        options = sorted(range(2), key=lambda k: (ext[k], k))
        for k in options:
            axis3 = (sec.u, sec.v)[k]
            lo3, hi3 = sec.lo[k], sec.hi[k]
            if any(a == axis3 and lo3 - 1e-3 <= x <= hi3 + 1e-3 for a, x in self.cut_planes):
                continue
            across = 1 - k
            width = float(np.clip(0.3 * ext[across], 5.0, 16.0))
            height = float(np.clip(0.6 * width, 3.0, 8.0))
            angle = 15.0 if kind == "dovetail" else 0.0
            half_tip = width / 2 + height * math.tan(math.radians(angle))
            need = half_tip + g + wall
            # candidate lines across the face, nearest the middle first
            centre = (sec.lo[across] + sec.hi[across]) / 2
            coords = np.unique(np.round(P[:, across] / h) * h)
            for cc in sorted(coords, key=lambda x: (abs(x - centre), x)):
                strip = np.abs(P[:, across] - cc) <= need
                along_vals = P[strip][:, k]
                if len(along_vals) == 0:
                    continue
                # every grid point in the strip, across the face's full length, must be inside
                us = np.arange(sec.lo[k] + h / 2, sec.hi[k], h)
                vs = np.arange(cc - need, cc + need + 1e-9, h)
                test = np.array([(u, v) if k == 0 else (v, u) for u in us for v in vs])
                if len(test) == 0 or not sec.inside(test).all():
                    continue
                # depth into b
                bb = B.bounding_box()
                depth = abs(tuple(bb.max - bb.min)[c["axis"]])
                if depth < height + J.HOLE_EXTRA_DEPTH + wall:
                    return False
                q = np.zeros(2)
                q[across] = cc
                q[k] = (sec.lo[k] + sec.hi[k]) / 2
                p3 = sec.to3d(q, c["at"])
                along = Vector(*np.eye(3)[axis3])
                try:
                    res = J._ridge(A, B, p3, d, along, self.ctx, width=width, height=height, length=None,
                                   angle=angle, fit="sliding" if kind == "dovetail" else self.fit, gap=None,
                                   kind=kind)
                except Exception:
                    return False
                self.pieces[a_i].shape = res.a
                self.pieces[b_i].shape = res.b
                self.joints.append(dict(a=a_i, b=b_i, kind=kind, hardware=[], at=_centre_of(c["faces"]),
                                        axis=Vector(*res.axis), features=res.features, note=res.note,
                                        gap=res.gap, fit=res.fit, count=1))
                return True
        return False

    # screws across the cut, with pockets open to the side
    def _screws(self, c, sec, a_i, b_i, d) -> bool:
        hw = J._library(self.ctx)
        size = self.screw_size
        try:
            s, nt = hw.screw(size), hw.nut(size)
        except Exception:
            return False
        g = hw._gap("hole")
        wall = self.wall
        head_h, head_d = s["cap_head_h"], s["cap_head_d"]
        L = hw.screw_length(size, 5.0 + 3.0 + nt["thickness"] + 0.6)
        wall_a = 5.0
        win_len = L + head_h + 1.5
        win_w = head_d + 1.0 + 2 * g
        nut_w = nt["across_corners"] + 2 * g if "across_corners" in nt else nt["across_flats"] * 1.155 + 2 * g
        nut_h = nt["thickness"] + 0.4
        half = max(win_w, nut_w) / 2
        P, dist, h = sec.grid()
        ok = dist >= half + wall
        Pv = P[ok]
        if len(Pv) == 0:
            return False
        A, B = self.pieces[a_i].shape, self.pieces[b_i].shape
        chosen = []
        for idx in _spread(Pv, len(Pv), 0.0, dist[ok]):
            if len(chosen) >= 2:
                break
            q = Pv[idx]
            if any(np.hypot(*(q - ch[0])) < 2 * (half + wall) + 4 for ch in chosen):
                continue
            e2 = _exit_direction(sec, q)
            if e2 is None:
                continue
            p3 = sec.to3d(q, c["at"])
            e3 = [0.0, 0.0, 0.0]
            e3[sec.u], e3[sec.v] = float(e2[0]), float(e2[1])
            e3 = Vector(*e3)
            frame = Plane(origin=p3, x_dir=e3, z_dir=d)
            out = float(np.abs(np.where(e2 > 0, sec.hi - q, q - sec.lo)).dot(np.abs(e2))) + wall
            ka = _window(frame, win_w + 2 * wall, -wall_a - win_len - wall, 0, out).bounding_box()
            kb = _window(frame, nut_w + 2 * wall, 0, L - wall_a + 0.2 + wall, out).bounding_box()
            if _boxes_clash(ka, self.keepouts[a_i], self.tag) or _boxes_clash(kb, self.keepouts[b_i], self.tag):
                continue
            # material behind the window in a, and past the nut in b
            spans_a = J._ray_spans(A, p3, -d)
            spans_b = J._ray_spans(B, p3, d)
            if not spans_a or not spans_b or spans_a[0][1] < wall_a + win_len + wall \
                    or spans_b[0][1] < L - wall_a + 2 + wall:
                continue
            chosen.append((q, p3, e3, ka, kb))
        if not chosen:
            return False
        tools, hardware = [], []
        reach = 400.0
        for q, p3, e3, ka, kb in chosen:
            frame = Plane(origin=p3, x_dir=e3, z_dir=d)
            # a: clearance hole from the window to the cut face, and the window
            self.subs[a_i].append(J._cylinder(s["clearance"] + 2 * g, -wall_a - 0.5, 1.0, p3, d))
            self.subs[a_i].append(_window(frame, win_w, -wall_a - win_len, -wall_a, reach))
            # b: clearance hole, nut slot open to the same side
            tip = -wall_a + L
            n1 = tip + 0.2
            n0 = n1 - nut_h
            self.subs[b_i].append(J._cylinder(s["clearance"] + 2 * g, -1.0, tip + 2.0, p3, d))
            self.subs[b_i].append(_window(frame, nut_w, n0, n1, reach))
            hardware += [f"{size}x{L:g} socket head screw", f"{size} nut"]
            # tool: a hex key in the window, from the head back along the screw
            tools.append(J._cylinder(s["hex_key"] * 1.2, -wall_a - win_len + 0.3, -wall_a - head_h - 0.3, p3, d))
            self.keepouts[a_i].append((ka, self.tag))
            self.keepouts[b_i].append((kb, self.tag))
        self.joints.append(dict(
            a=a_i, b=b_i, kind="screw", at=_centre_of(c["faces"]), axis=d, hardware=hardware,
            features={"tool": J._compound(tools)},
            note=f"{len(chosen)} {size}x{L:g} screw{'s' if len(chosen) > 1 else ''} into nuts; each screw and nut "
                 "drops into a pocket open to the side, and is tightened with a hex key",
            count=len(chosen)))
        return True


def _window(frame: Plane, width: float, z0: float, z1: float, reach: float) -> Solid:
    """A slot of ``width`` from the joint line out along the frame's x (to the outside)."""
    return J._box(-width / 2, reach, -width / 2, width / 2, z0, z1, frame)


def _exit_direction(sec: _Section2D, q: np.ndarray):
    """The in-plane direction (+-u, +-v) that leaves the face soonest, crossing the least material."""
    best = None
    for e in ((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0)):
        e = np.array(e)
        far = np.abs(np.where(e > 0, sec.hi - q, q - sec.lo)).dot(np.abs(e))
        steps = np.arange(0.25, far + 1.0, 0.5)
        pts = q[None] + steps[:, None] * e[None]
        inside = sec.inside(pts)
        material = inside.sum() * 0.5
        key = (material, far)
        if best is None or key < best[0]:
            best = (key, e)
    return best[1] if best else None


def _cut_face_is_largest(shape: Shape, axis: int, at: float) -> bool:
    cut = sum(f.area for f in _faces_on_plane(shape, axis, at))
    others = [f.area for f in shape.faces() if f.geom_type == GeomType.PLANE and not _on_plane(f, axis, at)]
    biggest = max(others or [0.0])
    return cut >= 0.9 * biggest


def _opposite_face(shape: Shape, axis: int, out) -> str | None:
    """Face-down hint ("-X", "+Z" ...) for the flat face opposite a joint face pointing ``out``,
    when that face is about as big as the biggest flat face."""
    sign = 1.0 if out[axis] > 0 else -1.0
    planar = [f for f in shape.faces() if f.geom_type == GeomType.PLANE]
    biggest = max((f.area for f in planar), default=0.0)
    want = -sign
    area = 0.0
    for f in planar:
        n = f.normal_at(f.center())
        if tuple(n)[axis] * want > 0.999:
            area = max(area, f.area)
    if biggest > 0 and area >= 0.85 * biggest:
        return f"{'+' if want > 0 else '-'}{'XYZ'[axis]}"
    return None


def _centre_of(faces) -> Vector:
    total = sum(f.area for f in faces)
    acc = Vector(0, 0, 0)
    for f in faces:
        acc += f.center() * f.area
    return acc * (1.0 / total) if total > 0 else acc


# ---- naming and colours ----------------------------------------------------------------------

def _shade(color: str, k: int) -> str:
    try:
        r, g, b = (int(color[i: i + 2], 16) / 255 for i in (1, 3, 5))
    except (ValueError, IndexError):
        return color
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    shifts = [0.0, 0.14, -0.12, 0.24, -0.2, 0.08, -0.06, 0.3, -0.26]
    l = min(0.88, max(0.18, l + shifts[k % len(shifts)] + 0.02 * (k // len(shifts))))
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return "#{:02X}{:02X}{:02X}".format(round(r * 255), round(g * 255), round(b * 255))


def _piece_names(name: str, pieces: list[_Piece]) -> list[str]:
    n = len(pieces)
    levels = [p.path for p in pieces]
    simple = all(all(step[2] == 2 and step[4] == 1 for step in path) for path in levels)
    axes_ok = all(len({s[0] for s in path}) == len(path) for path in levels)
    if simple and axes_ok:
        names = []
        for path in levels:
            words = [SIDE_WORDS[s[0]][s[1]] for s in sorted(path, key=lambda s: (-s[0]))]
            names.append(f"{name}, {' '.join(words)} piece")
        if len(set(names)) == n:
            return names
    return [f"{name}, piece {i + 1} of {n}" for i in range(n)]


def _the(name: str) -> str:
    low = name[:1].lower() + name[1:]
    return name if name.lower().startswith("the ") else f"the {low}"


def _dims(size) -> str:
    return " x ".join(f"{v:.0f}" for v in size)


# ---- main entry --------------------------------------------------------------------------------

def split_oversized(model: Model, ctx, options: dict | None = None):
    """Split every printed part bigger than the printer; returns (model, messages)."""
    options = dict(options or {})
    bed = ctx.bed
    use = usable_bed(bed)
    seams = []
    for s in options.get("seams") or []:
        try:
            axis = AXES.index(str(s["axis"]).lower())
            seams.append((axis, float(s["at"]), s.get("part")))
        except (KeyError, ValueError, TypeError):
            raise ValueError("Each seam needs an axis (x, y or z) and a position 'at' in mm, "
                             "for example {axis: x, at: 300}.") from None

    todo = []
    for part in model.parts:
        if not part.printed:
            continue
        size = size_of(part.shape)
        named = [(a, x) for a, x, p in seams if p in (part.id, part.name)]
        general = [(a, x) for a, x, p in seams if p is None]
        if named or not fits_bed(size, bed):
            todo.append((part, named + (general if not fits_bed(size, bed) else [])))
    if not todo:
        return model, []

    messages: list[str] = []
    new_parts: list[Part3D] = []
    split_map: dict[str, list[Part3D]] = {}
    all_joints: list[Joint] = []
    pin_parts: list[tuple[Part3D, list[int]]] = []

    for part in model.parts:
        entry = next((t for t in todo if t[0] is part), None)
        if entry is None:
            new_parts.append(part)
            continue
        part, part_seams = entry
        size = size_of(part.shape)
        cuts_log: list = []
        kind = options.get("joint", "peg")
        reserve = {"peg": 13.0, "dovetail": 9.0, "tongue-groove": 9.0}.get(kind, 0.0 if kind in KNOWN_JOINTS else 13.0)
        pieces = _partition(part.shape, bed, part_seams, cuts_log, reserve)
        if len(pieces) == 1:
            new_parts.append(part)
            continue
        joiner = _Joiner(pieces, ctx, options, messages, part.face_down)
        joiner.cut_planes = {(a, x) for a, x, _ in cuts_log}
        contacts = _contacts(pieces, cuts_log)
        for c in contacts:
            joiner.add(c)
        # apply all cuts and additions to each piece in one go
        for i, pc in enumerate(pieces):
            shape = pc.shape
            shape = J.fuse(shape, joiner.adds[i])
            shape = J.cut(shape, joiner.subs[i])
            pc.shape = shape
        # If a piece's peg face is also its biggest flat face, it might be printed lying on it
        # with the pegs pointing down. When a matching flat face is opposite, ask for that one
        # to go down instead, so the pegs point up.
        hints: dict[int, str] = {}
        for jd in joiner.joints:
            if part.face_down or jd["kind"] not in ("peg", "dovetail", "tongue-groove"):
                continue
            axis, at = jd["plane"]
            shape = pieces[jd["a"]].shape
            if not _cut_face_is_largest(shape, axis, at):
                continue
            out = tuple(jd.get("out", jd["axis"]))
            hint = _opposite_face(shape, axis, out)
            if hint and hints.get(jd["a"], hint) == hint:
                hints[jd["a"]] = hint
            else:
                joiner.notes.append("peg_down")
        names = _piece_names(part.name, pieces)
        made = []
        for i, (pc, nm) in enumerate(zip(pieces, names)):
            p = Part3D(id=f"{part.id}.{i + 1}", name=nm, shape=pc.shape, color=_shade(part.color, i),
                       printed=True, hardware=list(part.hardware) if i == 0 else [],
                       face_down=part.face_down or hints.get(i), source_part=part.id, notes=part.notes)
            made.append(p)
        new_parts += made
        split_map[part.id] = made
        # joints
        pin_count = 0
        for jd in joiner.joints:
            a, b = made[jd["a"]], made[jd["b"]]
            axis = jd["axis"]
            joint = Joint(a=a.id, b=b.id, kind=jd["kind"], fit=jd.get("fit", joiner.fit),
                          gap=jd.get("gap", joiner.gap) if jd["kind"] != "glue" else None,
                          hardware=list(jd["hardware"]), at=_vt(jd["at"]), axis=_vt(axis),
                          note=jd["note"], features=dict(jd["features"]))
            if "pin" in jd:
                pin_count += 1
                pin = Part3D(id=f"{part.id}.pin{pin_count}", name=f"{part.name}, pin {pin_count}",
                             shape=jd["pin"], color=_shade(part.color, len(made) + pin_count),
                             printed=True, source_part=part.id)
                pin_parts.append((pin, [a.id, b.id]))
                joint.note += f" (the pin is {pin.id})"
                joint.features = {"pin": jd["pin"]}
                joint.a, joint.b = pin.id, a.id
                j2 = Joint(a=pin.id, b=b.id, kind="dowel", fit=joint.fit, gap=joint.gap, hardware=[],
                           at=joint.at, axis=joint.axis, note=joint.note, features={"male": jd["pin"]})
                joint.features = {"male": jd["pin"]}
                all_joints += [joint, j2]
                continue
            all_joints.append(joint)
        messages += _messages(part, size, use, pieces, joiner, contacts, part_seams)
        if part_seams and any(not manual for _, _, manual in cuts_log):
            messages.append("Part of it was still too big for the printer between your seams, "
                            "so that part was cut again automatically.")

    # Re-point the design's own joints at the piece nearest each joint.
    kept = []
    for j in model.joints:
        ok = True
        for end in ("a", "b"):
            pid = getattr(j, end)
            if pid not in split_map:
                continue
            spot = j.at
            if spot is None and j.features:
                spot = next(iter(j.features.values())).bounding_box().tuple(center())
            if spot is None:
                messages.append(f"The joint between {j.a} and {j.b} has no position, so it was dropped after "
                                f"{pid} was split; give it 'at=' in the design to keep it.")
                ok = False
                break
            pt = Vector(*spot)
            dists = sorted((p.shape.distance_to(pt), k) for k, p in enumerate(split_map[pid]))
            if len(dists) > 1 and abs(dists[0][0] - dists[1][0]) < 1e-6 and dists[0][0] > 1e-6:
                messages.append(f"The joint between {j.a} and {j.b} sits exactly between two pieces of {pid}, "
                                "so it was dropped; move it a little.")
                ok = False
                break
            setattr(j, end, split_map[pid][dists[0][1]].id)
        if ok:
            kept.append(j)
    model.joints = kept + all_joints

    # Pins go after all other parts; then every printed part is numbered P01, P02 ...
    model.parts = new_parts + [p for p, _ in pin_parts]
    rename = {}
    n = 0
    for p in model.parts:
        if p.printed:
            n += 1
            rename[p.id] = f"P{n:02d}"
    for p in model.parts:
        if p.id in rename:
            p.id = rename[p.id]
    for j in model.joints:
        j.a = rename.get(j.a, j.a)
        j.b = rename.get(j.b, j.b)
        for old, new in sorted(rename.items(), key=lambda kv: -len(kv[0])):
            j.note = j.note.replace(f"(the pin is {old})", f"(the pin is {new})")
    final = []
    for msg in messages:
        final.append(msg)
    return model, final


def _vt(v):
    if v is None:
        return None
    t = tuple(v) if isinstance(v, Vector) else tuple(v)
    return tuple(round(float(x), 3) for x in t)


def _messages(part, size, use, pieces, joiner, contacts, seams) -> list[str]:
    out = []
    n = len(pieces)
    over = [i for i in range(3) if size[i] > sorted(use)[-1] + 1e-6] or \
           [max(range(3), key=lambda i: size[i])]
    kinds = sorted({j["kind"] for j in joiner.joints})
    joined = {"peg": "pegs", "dowel": "printed pins" if joiner.dowel != "steel" else "steel dowel pins",
              "screw": "screws and nuts", "dovetail": "sliding dovetails", "tongue-groove": "tongue-and-groove",
              "glue": "glue"}
    how = " and ".join(joined[k] for k in kinds) if kinds else "glue"
    what = _the(part.name)
    if seams:
        out.append(f"{what[0].upper() + what[1:]} is cut at the seams you chose into {n} pieces joined by {how}.")
    elif len(over) == 1:
        i = over[0]
        out.append(f"{what[0].upper() + what[1:]} is {size[i]:.0f} mm {SIZE_WORDS[i]}, more than the "
                   f"{max(use[0], use[1]):.0f} mm the printer allows, so it is split into {n} pieces joined by {how}.")
    else:
        out.append(f"{what[0].upper() + what[1:]} is {_dims(size)} mm, more than the {use[0]:.0f} x {use[1]:.0f} x "
                   f"{use[2]:.0f} mm the printer allows, so it is split into {n} pieces joined by {how}.")
    notes = joiner.notes
    for k in sorted({x.split(":", 1)[1] for x in notes if x.startswith("unknown:")}):
        out.append(f"'{k}' isn't a joint the splitter can make, so pegs were used instead. "
                   f"Use one of: {', '.join(KNOWN_JOINTS)}.")
    for k in sorted({x.split(":", 1)[1] for x in notes if x.startswith("ridge_fallback:")}):
        out.append(f"A {k} didn't fit on every cut face (it needs a clear straight run across the face), "
                   "so pegs were used there instead.")
    if "screw_fallback" in notes:
        out.append("Some cut faces had no room for a screw and nut pocket, so pegs were used there instead.")
    if "glue" in notes:
        small = [c for c in contacts if any(j["kind"] == "glue" for j in joiner.joints)]
        out.append(f"{notes.count('glue')} cut face{'s are' if notes.count('glue') > 1 else ' is'} too small for a "
                   "joint (it needs room for a peg at least 4 mm across with walls around it), so those pieces "
                   "are glued. Make the part thicker there for a stronger joint." if small else "")
    if "single" in notes:
        out.append("One cut face only had room for a single peg, so those pieces could twist; add a little glue.")
    if "peg_down" in notes:
        out.append("On some pieces the cut face is also the biggest flat face, so the pegs may end up pointing "
                   "down when printed; check the print orientation of those pieces.")
    return [m for m in out if m]

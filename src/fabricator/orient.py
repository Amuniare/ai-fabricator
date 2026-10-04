"""How each piece lies on the print bed.

``candidates`` tries the six "this side down" turns plus the largest flat faces,
scores each one (supports first, then how much of the piece touches the bed, then
height) and returns them best first. ``choose`` picks the best one that fits.

The same overhang measurements are reused by ``checks/printability.py`` through
``analyze``, so what is chosen here and what is reported there always agree.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
from build123d import Rotation, Pos, Shape
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial.transform import Rotation as _SciRotation

from .meshing import to_mesh

OVERHANG_NZ = -np.cos(np.radians(45))  # faces pointing down more steeply than this need support
FLAT_NZ = -0.99  # "horizontal and facing down"
BED_TOL = 0.15  # mm above the lowest point still counts as lying on the bed
SUPPORT_AREA_MM2 = 25.0  # overhang area that is worth adding supports for
SHORT_BRIDGE_MM = 8.0  # a flat ceiling this narrow prints fine unsupported
SMALL_ARCH_MM = 10.0  # a curved overhang this narrow (hole tops, peg undersides) needs no support
SMALL_ARCH_RISE_MM = 3.0  # ...as long as it rises no more than this
BED_MARGIN = 1.0  # mm kept clear on each side of the bed

_AXIS_WORDS = {  # which face is down -> words (front = -Y, left = -X)
    (0, 0, -1): "as modelled", (0, 0, 1): "upside down",
    (-1, 0, 0): "left side down", (1, 0, 0): "right side down",
    (0, -1, 0): "front side down", (0, 1, 0): "back side down",
}
_DIRS = {"-X": (-1, 0, 0), "+X": (1, 0, 0), "-Y": (0, -1, 0), "+Y": (0, 1, 0),
         "-Z": (0, 0, -1), "+Z": (0, 0, 1)}


@dataclass
class Orientation:
    label: str = "as modelled"
    rotation: list = field(default_factory=lambda: np.eye(3).tolist())  # 3x3, row-major
    face_down: str = "-Z"
    overhang_area_mm2: float = 0.0
    contact_area_mm2: float = 0.0
    height_mm: float = 0.0
    fits: bool = True
    support_needed: bool = False
    score: float = 0.0

    def matrix(self) -> np.ndarray:
        return np.array(self.rotation, dtype=float).reshape(3, 3)

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "rotation": [[round(float(v), 6) for v in row] for row in self.rotation],
            "face_down": self.face_down,
            "overhang_area_mm2": round(float(self.overhang_area_mm2), 1),
            "contact_area_mm2": round(float(self.contact_area_mm2), 1),
            "height_mm": round(float(self.height_mm), 2),
            "fits": bool(self.fits),
            "support_needed": bool(self.support_needed),
            "score": round(float(self.score), 2),
        }


# ---- measuring a mesh in a given turn ---------------------------------------------

def _components(mesh, mask: np.ndarray) -> list[np.ndarray]:
    """Groups of touching faces among the faces picked by ``mask`` (indices)."""
    idx = np.flatnonzero(mask)
    if len(idx) == 0:
        return []
    adj = mesh.face_adjacency
    if len(adj):
        remap = -np.ones(len(mesh.faces), dtype=np.int64)
        remap[idx] = np.arange(len(idx))
        a, b = remap[adj[:, 0]], remap[adj[:, 1]]
        ok = (a >= 0) & (b >= 0)
        a, b = a[ok], b[ok]
    else:
        a = b = np.array([], dtype=np.int64)
    g = coo_matrix((np.ones(len(a)), (a, b)), shape=(len(idx), len(idx)))
    n, labels = connected_components(g, directed=False)
    return [idx[labels == k] for k in range(n)]


def _span(tris_xy: np.ndarray, step: float = 0.25) -> float:
    """How far a flat ceiling reaches across at its widest point, in mm.

    That's what decides whether it sags: a U-shaped ledge 1.6 mm deep is easy to print
    even when the U is 60 mm wide. Found by drawing the ceiling on a fine grid and
    measuring the widest circle that fits inside it.
    """
    from PIL import Image, ImageDraw
    from scipy.ndimage import distance_transform_edt

    lo = tris_xy.reshape(-1, 2).min(axis=0) - 2 * step
    hi = tris_xy.reshape(-1, 2).max(axis=0) + 2 * step
    size = np.ceil((hi - lo) / step).astype(int) + 1
    if size.prod() > 16_000_000:  # very large ceiling: coarser grid
        return _span(tris_xy, step * 2)
    img = Image.new("1", (int(size[0]), int(size[1])), 0)
    draw = ImageDraw.Draw(img)
    for t in (tris_xy - lo) / step:
        draw.polygon([tuple(p) for p in t], fill=1, outline=1)
    inside = np.array(img, dtype=bool)
    return float(2 * distance_transform_edt(inside).max() * step)


def analyze(mesh, R: np.ndarray | None = None) -> dict:
    """Overhang, bed contact and size of ``mesh`` after turning it by ``R`` and setting it down.

    Returns overhang area (needing support, bridges short enough to print excluded
    from ``support_area``), flat ceiling groups, contact area and the turned size.
    """
    R = np.eye(3) if R is None else R
    verts = mesh.vertices @ R.T
    zmin = verts[:, 2].min()
    tri = verts[mesh.faces]  # (F,3,3)
    normals = mesh.face_normals @ R.T
    areas = mesh.area_faces
    face_zmax = tri[:, :, 2].max(axis=1) - zmin
    on_bed = face_zmax <= BED_TOL
    down = normals[:, 2] < OVERHANG_NZ
    overhang = down & ~on_bed
    contact = (normals[:, 2] < -0.9) & on_bed
    flat = (normals[:, 2] < FLAT_NZ) & ~on_bed

    # Flat ceilings: split into groups, narrow ones are bridges that print without support.
    support_mask = overhang & ~flat
    groups = []
    for comp in _components(mesh, flat):
        pts = tri[comp].reshape(-1, 3)
        ext = pts.max(axis=0)[:2] - pts.min(axis=0)[:2]
        span = _span(tri[comp][:, :, :2])
        area = float(areas[comp].sum())
        centre = (tri[comp].mean(axis=1) * areas[comp, None]).sum(axis=0) / max(area, 1e-9)
        groups.append({"area": area, "span": span, "length": float(ext.max()), "centre": centre, "faces": comp})
        if span > SHORT_BRIDGE_MM:
            support_mask[comp] = True
    # Small arches print without support: the top of a sideways hole, the underside of a
    # sideways peg. Supporting them would waste plastic and be hard to remove.
    for comp in _components(mesh, support_mask):
        pts = tri[comp].reshape(-1, 3)
        ext = pts.max(axis=0) - pts.min(axis=0)
        if float(ext[:2].min()) <= SMALL_ARCH_MM and float(ext[2]) <= SMALL_ARCH_RISE_MM:
            support_mask[comp] = False
    support_area = float(areas[support_mask].sum())
    size = verts.max(axis=0) - verts.min(axis=0)
    return {
        "verts": verts, "tri": tri, "zmin": zmin, "normals": normals, "areas": areas,
        "overhang_mask": overhang, "support_mask": support_mask,
        "overhang_area": float(areas[overhang].sum()), "support_area": support_area,
        "contact_area": float(areas[contact].sum()), "flat_groups": groups,
        "size": size, "height": float(size[2]),
    }


# ---- candidate turns ---------------------------------------------------------------------

def _rot_down(n: np.ndarray) -> np.ndarray:
    """A turn that takes direction ``n`` to straight down (-Z)."""
    n = n / np.linalg.norm(n)
    t = np.array([0.0, 0.0, -1.0])
    c = float(n @ t)
    if c > 1 - 1e-9:
        return np.eye(3)
    if c < -1 + 1e-9:  # pointing straight up: flip about X
        return np.diag([1.0, -1.0, -1.0])
    v = np.cross(n, t)
    s = np.linalg.norm(v)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * ((1 - c) / s**2)


def _clean(R: np.ndarray) -> np.ndarray:
    R = np.where(np.abs(R) < 1e-9, 0.0, R)
    return np.where(np.abs(np.abs(R) - 1) < 1e-9, np.sign(R), R)


_RZ90 = np.array([[0.0, -1, 0], [1, 0, 0], [0, 0, 1]])


def _bed_limits(bed) -> np.ndarray:
    return np.array(bed, dtype=float) - 2 * BED_MARGIN


def _planar_directions(mesh, limit: int = 6) -> list[np.ndarray]:
    """Normals of the largest flat faces, biggest first (near-identical ones merged)."""
    key = np.round(mesh.face_normals, 2)
    keys, inverse = np.unique(key, axis=0, return_inverse=True)
    area = np.bincount(inverse.ravel(), weights=mesh.area_faces)
    out: list[np.ndarray] = []
    for k in np.argsort(-area, kind="stable"):
        n = keys[k] / max(np.linalg.norm(keys[k]), 1e-9)
        if area[k] < 1e-6:
            break
        if any(float(n @ m) > 0.999 for m in out):
            continue
        out.append(n)
        if len(out) >= limit:
            break
    return out


def _word_for(n: np.ndarray) -> str | None:
    for d, words in _AXIS_WORDS.items():
        if np.allclose(n, d, atol=1e-3):
            return words
    return None


def _face_name(n: np.ndarray) -> str:
    for name, d in _DIRS.items():
        if np.allclose(n, d, atol=1e-3):
            return name
    return "angled"


def candidates(part, bed, ctx=None, mesh=None) -> list[Orientation]:
    """Ways to lay the part on the bed, best first."""
    shape = part.shape if hasattr(part, "shape") else part
    if mesh is None:
        mesh = to_mesh(shape, tolerance=0.05)
    limits = _bed_limits(bed)

    # the six axis turns, as modelled first; then the biggest flat faces
    dirs = [np.array(_DIRS[k], dtype=float) for k in ("-Z", "+Z", "-Y", "+Y", "-X", "+X")]
    for n in _planar_directions(mesh):
        if not any(float(n @ m) > 0.999 for m in dirs):
            dirs.append(n)

    wanted = getattr(part, "face_down", None)
    wanted_dir = np.array(_DIRS[wanted.upper().replace(" ", "")], dtype=float) if wanted and wanted.upper() in _DIRS else None

    result: list[Orientation] = []
    for n in dirs:
        R = _clean(_rot_down(n))
        turned = False
        m = analyze(mesh, R)
        fits = bool(np.all(m["size"] <= limits))
        if not fits:  # a quarter turn about the vertical may be what makes it fit
            m2 = analyze(mesh, _clean(_RZ90 @ R))
            if np.all(m2["size"] <= limits):
                R, m, fits, turned = _clean(_RZ90 @ R), m2, True, True
        word = _word_for(n)
        h = m["height"]
        foot = float(max(m["size"][:2]))
        if word == "as modelled":
            label = word
        elif h > 1.5 * foot:
            label = "standing on its end"
        elif word:
            label = word
        else:
            label = "largest flat face down"
        if turned:
            label += ", turned 90 degrees to fit"
        support_needed = m["support_area"] > SUPPORT_AREA_MM2
        score = m["support_area"] * 5.0 + (m["overhang_area"] - m["support_area"]) * 0.5 \
            - m["contact_area"] * 0.5 + h
        requested = wanted_dir is not None and float(n @ wanted_dir) > 0.999
        result.append(Orientation(
            label=label, rotation=R.tolist(), face_down=_face_name(n),
            overhang_area_mm2=m["support_area"], contact_area_mm2=m["contact_area"],
            height_mm=h, fits=fits, support_needed=support_needed, score=score,
        ))
        result[-1]._requested = requested  # type: ignore[attr-defined]

    result.sort(key=lambda o: (not o.fits, not getattr(o, "_requested", False), round(o.score, 6), o.label))
    return result


def choose(part, bed, ctx=None) -> Orientation:
    """The best way to lay the part down that fits the bed (or the best anyway)."""
    cands = candidates(part, bed, ctx)
    for c in cands:
        if c.fits:
            return c
    return cands[0]


def place_on_bed(shape: Shape, orientation: Orientation) -> Shape:
    """Turn the shape, set its lowest point on z = 0 and centre it on x = y = 0."""
    R = orientation.matrix()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # gimbal lock at 90 degrees is harmless here
        x, y, z = _SciRotation.from_matrix(R).as_euler("XYZ", degrees=True)
    turned = Rotation(float(x), float(y), float(z)) * shape
    bb = turned.bounding_box()
    c = bb.center()
    return Pos(-c.X, -c.Y, -bb.min.Z) * turned

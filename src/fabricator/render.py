"""Pictures of the model: clean shaded PNGs made with matplotlib (no GPU needed).

Each picture is drawn by projecting every triangle straight onto the page, sorting
them far to near across all parts together (so parts hide each other correctly),
and filling them with simple Lambert shading in one collection.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
logging.getLogger("matplotlib").setLevel(logging.ERROR)  # silences "Ignoring fixed limits" notices
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import trimesh  # noqa: E402
from matplotlib.collections import PolyCollection  # noqa: E402
from matplotlib.colors import to_rgb  # noqa: E402

from . import orient  # noqa: E402
from .meshing import to_mesh  # noqa: E402
from .project import ProjectError  # noqa: E402

PICTURE_TOLERANCE = 0.1
REFERENCE_GREY = "#B8B8B8"
REFERENCE_ALPHA = 0.35
TRANSPARENT_ALPHA = 0.4
DPI = 110

# Direction from the object towards the viewer, and which way is up on the page.
VIEWS = {
    "front": ((0, -1, 0), (0, 0, 1)),
    "back": ((0, 1, 0), (0, 0, 1)),
    "left": ((-1, 0, 0), (0, 0, 1)),
    "right": ((1, 0, 0), (0, 0, 1)),
    "top": ((0, 0, 1), (0, 1, 0)),
    "bottom": ((0, 0, -1), (0, -1, 0)),
    "angled": ((0.62, -0.78, 0.70), (0, 0, 1)),
    "print": ((0.35, -0.75, 0.95), (0, 0, 1)),
    "plates": ((0.35, -0.75, 0.95), (0, 0, 1)),
}
VIEW_TITLES = {"front": "Front", "back": "Back", "left": "Left side", "right": "Right side",
               "top": "Top", "bottom": "Bottom", "angled": "Angled"}


@dataclass
class Item:
    """One part ready to draw: triangles in world space and how to colour them."""

    id: str
    tris: np.ndarray  # (F,3,3)
    normals: np.ndarray  # (F,3)
    color: str
    alpha: float = 1.0
    label: bool = True

    @property
    def lo(self):
        return self.tris.reshape(-1, 3).min(axis=0)

    @property
    def hi(self):
        return self.tris.reshape(-1, 3).max(axis=0)

    def moved(self, offset) -> "Item":
        return Item(self.id, self.tris + np.asarray(offset), self.normals, self.color, self.alpha, self.label)


def item_from_mesh(pid: str, mesh, color: str, alpha: float = 1.0, label: bool = True) -> Item:
    # Pictures sort triangles by depth. A long thin triangle (a flat lid, a shelf top)
    # has one depth for its whole length and can be drawn over things in front of it, so
    # break big triangles into small ones first.
    import trimesh

    normals = mesh.face_normals
    max_edge = max(float(mesh.extents.max()) / 20, 2.0)
    try:
        verts, faces, index = trimesh.remesh.subdivide_to_size(
            mesh.vertices, mesh.faces, max_edge=max_edge, max_iter=8, return_index=True)
        tris, normals = verts[faces], normals[index]
    except Exception:
        tris = mesh.vertices[mesh.faces]
    return Item(pid, tris, normals, color, alpha, label)


def _basis(view: str):
    v, up = (np.array(a, dtype=float) for a in VIEWS[view])
    v /= np.linalg.norm(v)
    forward = -v
    right = np.cross(forward, up)
    right /= np.linalg.norm(right)
    upv = np.cross(right, forward)
    return v, right, upv


def size_text(items: list[Item]) -> str:
    solid = [i for i in items if i.alpha >= 1.0 or i.label] or items
    lo = np.min([i.lo for i in solid], axis=0)
    hi = np.max([i.hi for i in solid], axis=0)
    s = hi - lo
    return " × ".join(f"{x:.0f}" if x >= 10 else f"{x:.1f}" for x in s) + " mm"


def draw(ax, items: list[Item], view: str, extra_polys=None, labels: bool = False, transparent: bool = False,
         pad: float = 0.04) -> None:
    """Draw items into ``ax`` as seen from ``view`` (a key of VIEWS)."""
    v, right, up = _basis(view)
    light = -0.35 * right + 0.55 * up + 0.8 * v
    light /= np.linalg.norm(light)

    polys, colors, depths = [], [], []
    for it in items:
        alpha = TRANSPARENT_ALPHA if transparent else it.alpha
        dots = it.normals @ v
        keep = np.ones(len(dots), dtype=bool) if alpha < 1 else dots > 1e-6
        tris = it.tris[keep]
        n = it.normals[keep]
        if not len(tris):
            continue
        shade = 0.45 + 0.55 * np.clip(np.abs(n @ light) if alpha < 1 else n @ light, 0, 1)
        base = np.array(to_rgb(it.color))
        rgb = np.clip(base[None, :] * shade[:, None] + (shade[:, None] - 0.8).clip(0) * 0.3, 0, 1)
        rgba = np.concatenate([rgb, np.full((len(rgb), 1), alpha)], axis=1)
        xy = np.stack([tris @ right, tris @ up], axis=2)  # (F,3,2)
        polys.append(xy)
        colors.append(rgba)
        depths.append((tris @ v).mean(axis=1))
    xs, ys = [], []
    if extra_polys:
        for poly, fc, ec in extra_polys:
            p = np.stack([poly @ right, poly @ up], axis=1)
            ax.add_patch(plt.Polygon(p, closed=True, facecolor=fc, edgecolor=ec, linewidth=1.2, zorder=0))
            xs.append(p[:, 0]); ys.append(p[:, 1])
    if polys:
        polys = np.concatenate(polys)
        colors = np.concatenate(colors)
        depth = np.concatenate(depths)
        span = max(float(depth.max() - depth.min()), 1e-6)
        near = (depth - depth.min()) / span  # 0 far .. 1 near: far surfaces a little darker
        colors[:, :3] *= (0.78 + 0.22 * near)[:, None]
        order = np.argsort(depth, kind="stable")  # far first
        coll = PolyCollection(polys[order], facecolors=colors[order], edgecolors=colors[order],
                              linewidths=0.35, antialiaseds=True, zorder=2)
        ax.add_collection(coll)
        xs.append(polys[..., 0].ravel()); ys.append(polys[..., 1].ravel())
    if xs:
        x = np.concatenate(xs); y = np.concatenate(ys)
        w, h = max(x.max() - x.min(), 1e-6), max(y.max() - y.min(), 1e-6)
        ax.set_xlim(x.min() - pad * w, x.max() + pad * w)
        ax.set_ylim(y.min() - pad * h, y.max() + pad * h)
    ax.set_aspect("equal", adjustable="datalim")
    ax.axis("off")
    if labels:
        for it in items:
            if not it.label:
                continue
            c = (it.lo + it.hi) / 2
            # nudge the label towards the viewer so it sits on the visible side
            c = c + v * (np.abs((it.hi - it.lo) @ v) / 2)
            ax.text(c @ right, c @ up, it.id, ha="center", va="center", fontsize=8, fontweight="bold",
                    color="#111111", zorder=5,
                    bbox=dict(boxstyle="round,pad=0.18", facecolor="white", edgecolor="none", alpha=0.8))


def _figure(w=7.0, h=5.6):
    fig = plt.figure(figsize=(w, h), dpi=DPI, facecolor="white")
    return fig


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor="white", bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    return path


def _single(items, view, path, title, labels=False, transparent=False, extra=None) -> Path:
    fig = _figure()
    ax = fig.add_axes([0.02, 0.02, 0.96, 0.88])
    draw(ax, items, view, extra_polys=extra, labels=labels, transparent=transparent)
    fig.suptitle(title, fontsize=13, y=0.97)
    return _save(fig, path)


# ---- layout helpers ---------------------------------------------------------------------

def exploded_items(items: list[Item]) -> list[Item]:
    """Pull printed parts apart so every seam shows.

    The biggest part stays where it is; every other part moves away from it, further the
    further away it already was, and always at least a third of its own size (a lid on a
    box lifts clear; a row of shelf pieces spreads out evenly).
    """
    moving = [i for i in items if i.alpha >= 1.0]
    if len(moving) < 2:
        return items
    anchor = max(moving, key=lambda i: float(np.prod(np.maximum(i.hi - i.lo, 1e-3))))
    origin = (anchor.lo + anchor.hi) / 2
    out = []
    for i in items:
        if i.alpha < 1.0 or i is anchor:
            out.append(i)
            continue
        d = (i.lo + i.hi) / 2 - origin
        n = float(np.linalg.norm(d))
        direction = d / n if n > 1e-6 else np.array([0, 0, 1.0])
        if n > 1e-6 and abs(direction[2]) > 0.35:  # mostly above or below: go straight up/down
            direction = np.array([0, 0, np.sign(direction[2])])
        least = 0.35 * float((i.hi - i.lo).max())
        out.append(i.moved(direction * max(0.6 * n, least)))
    return out


def pack_on_plates(items: list[Item], bed, spacing: float = 6.0):
    """Lay pieces out left to right, front to back, on as many bed outlines as needed.

    Each item must already be centred on x = y = 0 and sit on z = 0. Returns moved
    items and a list of bed outlines (as 4-corner polygons in 3D).
    """
    bw, bd = float(bed[0]), float(bed[1])
    plates: list[list[Item]] = [[]]
    x = y = row_h = 0.0
    out = []
    plate = 0
    for it in sorted(items, key=lambda i: -(i.hi[1] - i.lo[1])):
        w, d = it.hi[0] - it.lo[0], it.hi[1] - it.lo[1]
        if x + w > bw and x > 0:
            x, y, row_h = 0.0, y + row_h + spacing, 0.0
        if y + d > bd and y > 0:
            plate += 1
            x = y = row_h = 0.0
        ox = plate * (bw + 25) + x - it.lo[0]
        oy = y - it.lo[1]
        out.append(it.moved([ox, oy, 0]))
        x += w + spacing
        row_h = max(row_h, d)
    n_plates = plate + 1
    # centre what is on each plate
    placed_on = {}
    for it in out:
        k = int(max(0, np.floor((it.lo[0] + it.hi[0]) / 2 / (bw + 25))))
        placed_on.setdefault(k, []).append(it)
    centred = []
    for k, group in sorted(placed_on.items()):
        lo = np.min([g.lo for g in group], axis=0)
        hi = np.max([g.hi for g in group], axis=0)
        dx = k * (bw + 25) + bw / 2 - (lo[0] + hi[0]) / 2
        dy = bd / 2 - (lo[1] + hi[1]) / 2
        centred += [g.moved([dx, dy, 0]) for g in group]
    out = centred
    outlines = [np.array([[p * (bw + 25), 0, 0], [p * (bw + 25) + bw, 0, 0],
                          [p * (bw + 25) + bw, bd, 0], [p * (bw + 25), bd, 0]]) for p in range(n_plates)]
    return out, outlines


def _print_picture(items: list[Item], bed, path: Path, title: str) -> Path:
    moved, outlines = pack_on_plates(items, bed)
    extra = [(o, "#F4F4F4", "#888888") for o in outlines]
    fig = _figure(7.5, 5.6)
    ax = fig.add_axes([0.02, 0.02, 0.96, 0.88])
    draw(ax, moved, "print", extra_polys=extra, labels=True)
    for k, o in enumerate(outlines):
        if len(outlines) > 1:
            _, right, up = _basis("print")
            p = o[3] + np.array([0, 4, 0])
            ax.text(p @ right, p @ up, f"Plate {k + 1}", fontsize=8, color="#555555", va="bottom")
    fig.suptitle(title, fontsize=13, y=0.97)
    return _save(fig, path)


# ---- the build's pictures ----------------------------------------------------------------

def _model_items(model, tolerance=PICTURE_TOLERANCE) -> list[Item]:
    items = []
    for p in model.parts:
        mesh = to_mesh(p.shape, tolerance=tolerance)
        if p.printed:
            items.append(item_from_mesh(p.id, mesh, p.color))
        else:
            items.append(item_from_mesh(p.id, mesh, REFERENCE_GREY, REFERENCE_ALPHA, label=False))
    return items


def _printed_items(model, bed, ctx) -> list[Item]:
    items = []
    for p in model.parts:
        if not p.printed:
            continue
        choice = orient.choose(p, bed, ctx)
        placed = orient.place_on_bed(p.shape, choice)
        items.append(item_from_mesh(p.id, to_mesh(placed, tolerance=PICTURE_TOLERANCE), p.color))
    return items


def build_pictures(model, out_dir, ctx) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    if not model.parts:
        return paths
    items = _model_items(model)
    printed = [p for p in model.parts if p.printed]
    many = len(model.parts) > 1
    title = size_text(items)

    fig = plt.figure(figsize=(9.5, 8.2), dpi=DPI, facecolor="white")
    for k, view in enumerate(("front", "right", "top", "angled")):
        ax = fig.add_subplot(2, 2, k + 1)
        draw(ax, items, view, labels=many)
        ax.set_title(VIEW_TITLES[view], fontsize=10, color="#555555")
    fig.suptitle(f"{model.name}  •  {title}", fontsize=13)
    paths.append(_save(fig, out_dir / "overview.png"))

    if len(printed) > 1:
        paths.append(_single(exploded_items(items), "angled", out_dir / "exploded.png",
                             f"{model.name}, pulled apart", labels=True))
    if printed:
        pitems = _printed_items(model, ctx.bed, ctx)
        paths.append(_print_picture(pitems, ctx.bed, out_dir / "print.png",
                                    f"As printed on the {ctx.bed[0]:g} × {ctx.bed[1]:g} mm bed"))
    return paths


# ---- pictures from the last build ----------------------------------------------------------

def _resolve(info: list[dict], names, what: str) -> set[str]:
    ids = set()
    for n in names or []:
        match = [p["id"] for p in info if n == p["id"] or n.lower() == str(p["name"]).lower() or n.upper() == p["id"]]
        if not match:
            known = ", ".join(f"{p['id']} ({p['name']})" for p in info)
            raise ProjectError(f"There is no part called '{n}'. The parts are: {known}.")
        ids.add(match[0])
    return ids


def _load_stl(path: Path):
    mesh = trimesh.load(str(path), force="mesh")
    return mesh


def _cut(mesh, cut, axis: str):
    """Keep one half of the mesh with a flat closed face where it was cut.

    Uses a boolean with a big box (manifold3d); falls back to an open cut.
    """
    idx = "xyz".index(axis)
    sign = -1 if axis in ("x", "z") else 1  # x and z keep the low side, y keeps the high side
    lo, hi = mesh.bounds
    pad = float(np.max(hi - lo)) + 10
    ext = np.full(3, 2 * pad)
    ext[idx] = pad
    centre = np.array(cut, dtype=float)
    centre[idx] = cut[idx] + sign * pad / 2
    # other axes: centre the box on the mesh so it covers all of it
    for k in range(3):
        if k != idx:
            centre[k] = (lo[k] + hi[k]) / 2
    box = trimesh.creation.box(extents=ext, transform=trimesh.transformations.translation_matrix(centre))
    try:
        res = trimesh.boolean.intersection([mesh, box], engine="manifold")
        if res is not None and len(res.faces):
            return res
    except Exception:
        pass
    normal = [0, 0, 0]
    normal[idx] = sign
    try:
        return mesh.slice_plane(cut, normal, cap=False)
    except Exception:
        return mesh


def pictures(project, parts=None, exploded=False, views=None, hide=None, transparent=False, section=None) -> list[Path]:
    model_file = project.build_dir / "model.json"
    if not model_file.exists():
        raise ProjectError("This project hasn't been built yet. Run 'fabricator build' first.")
    info = json.loads(model_file.read_text(encoding="utf-8"))
    plist = info["parts"]
    show = _resolve(plist, parts, "show")
    hidden = _resolve(plist, hide, "hide")
    chosen = [p for p in plist if (not show or p["id"] in show) and p["id"] not in hidden]
    if not chosen:
        raise ProjectError("Nothing to show: every part was left out.")
    views = list(views or ["angled"])
    bed = info.get("bed_mm", [180, 180, 180])

    def load(kind: str):
        out = []
        for p in chosen:
            f = project.build_dir / kind / f"{p['id']}.stl"
            if not f.exists():
                continue
            out.append((p, _load_stl(f)))
        return out

    # Where to cut, for a section: through the middle of everything shown.
    cut = None
    if section:
        loaded = load("parts")
        if loaded:
            allv = np.concatenate([m.vertices for _, m in loaded])
            cut = (allv.min(axis=0) + allv.max(axis=0)) / 2
    keep_normal = {"x": (-1, 0, 0), "y": (0, 1, 0), "z": (0, 0, -1)}

    def to_item(p, mesh, section_ok=True) -> Item:
        printed = p["printed"]
        if section and section_ok and cut is not None:
            mesh = _cut(mesh, cut, section)
            if mesh is None or len(mesh.faces) == 0:
                return None
        if printed:
            return item_from_mesh(p["id"], mesh, p["color"])
        return item_from_mesh(p["id"], mesh, REFERENCE_GREY, REFERENCE_ALPHA, label=False)

    tag = "+".join(p["id"] for p in chosen) if show else "all"
    if hidden and not show:
        tag = "all-without-" + "+".join(sorted(hidden))
    suffix = ("-section-" + section if section else "") + ("-transparent" if transparent else "")
    out_dir = project.build_dir / "pictures"
    out: list[Path] = []
    for view in views:
        if view in ("print", "plates"):
            items = [to_item(p, m, section_ok=False) for p, m in load("print") if p["printed"]]
            items = [i for i in items if i]
            if not items:
                raise ProjectError("There are no printed pieces to show for that view.")
            path = out_dir / f"show-{tag}-{view}{suffix}.png"
            ttl = "As printed" if view == "print" else "Print plates"
            out.append(_print_picture(items, bed, path, f"{ttl} on the {bed[0]:g} × {bed[1]:g} mm bed"))
            continue
        items = [to_item(p, m) for p, m in load("parts")]
        items = [i for i in items if i]
        if not items:
            raise ProjectError("The saved build has no pictures to work from. Run 'fabricator build' again.")
        title = size_text(items)
        if section:
            title += f", cut through the middle ({section.upper()})"
        multi = len([i for i in items if i.label]) > 1
        if exploded:
            items = exploded_items(items)
        name = f"show-{tag}-{'exploded-' if exploded else ''}{view}{suffix}.png"
        out.append(_single(items, view, out_dir / name, title, labels=multi or exploded, transparent=transparent))
    return out

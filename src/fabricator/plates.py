"""Print-plate packer: deterministic MaxRects (best short side fit).

Pieces keep ``margin`` mm from the bed edges. A piece that only fits with the smaller
margin the splitter allows (2 mm, see split.EDGE_MARGIN) is printed alone, centred.
"""
from __future__ import annotations

MIN_MARGIN = 2.0  # same as split.EDGE_MARGIN


def _fmt(v: float) -> str:
    return f"{v:g}"


class _Plate:
    """One bed, packed with MaxRects. Works in 'enlarged' space: the usable
    area plus one spacing, with each piece inflated by one spacing."""

    def __init__(self, w: float, h: float):
        self.free = [(0.0, 0.0, w, h)]
        self.placed: list[tuple[str, float, float, float, float, bool]] = []

    def find(self, w: float, h: float):
        best = None  # (short, long, y, x, rotated, w, h)
        for fx, fy, fw, fh in self.free:
            for rot, (pw, ph) in ((False, (w, h)), (True, (h, w))):
                if rot and w == h:
                    continue
                if pw <= fw + 1e-9 and ph <= fh + 1e-9:
                    key = (min(fw - pw, fh - ph), max(fw - pw, fh - ph), fy, fx, rot)
                    if best is None or key < best[0]:
                        best = (key, fx, fy, pw, ph, rot)
        return best

    def place(self, pid: str, best) -> None:
        _, x, y, pw, ph, rot = best
        self.placed.append((pid, x, y, pw, ph, rot))
        new = []
        for fx, fy, fw, fh in self.free:
            if x >= fx + fw - 1e-9 or x + pw <= fx + 1e-9 or y >= fy + fh - 1e-9 or y + ph <= fy + 1e-9:
                new.append((fx, fy, fw, fh))
                continue
            if x > fx:
                new.append((fx, fy, x - fx, fh))
            if x + pw < fx + fw:
                new.append((x + pw, fy, fx + fw - x - pw, fh))
            if y > fy:
                new.append((fx, fy, fw, y - fy))
            if y + ph < fy + fh:
                new.append((fx, y + ph, fw, fy + fh - y - ph))
        new = [r for r in new if r[2] > 1e-9 and r[3] > 1e-9]
        self.free = [
            r for i, r in enumerate(new)
            if not any(
                j != i and o[0] <= r[0] + 1e-9 and o[1] <= r[1] + 1e-9
                and o[0] + o[2] >= r[0] + r[2] - 1e-9 and o[1] + o[3] >= r[1] + r[3] - 1e-9
                and (o != r or j < i)
                for j, o in enumerate(new)
            )
        ]


def layout(pieces, bed, spacing: float = 6.0, margin: float = 5.0) -> list[list[dict]]:
    """Pack pieces onto plates. Returns per-plate lists of
    {"id", "x", "y", "rotated"}; x, y are the piece centre relative to the bed
    centre. Within a plate, entries follow input order."""
    bx, by, bz = bed
    ux, uy = bx - 2 * margin, by - 2 * margin
    items = []
    solo = []  # pieces that only fit with the smaller edge margin split.py allows: own plate, centred
    for idx, p in enumerate(pieces):
        sx, sy, sz = p["size"]
        fits = (sx <= ux and sy <= uy) or (sy <= ux and sx <= uy)
        sux, suy = bx - 2 * MIN_MARGIN, by - 2 * MIN_MARGIN
        if not fits and sz <= bz and ((sx <= sux and sy <= suy) or (sy <= sux and sx <= suy)):
            solo.append((idx, p["id"], not (sx <= sux and sy <= suy)))
            items.append((idx, p["id"], None, None))
            continue
        if not fits or sz > bz:
            if fits:
                why = f"{p['id']} is {_fmt(sz)} mm tall, more than the {_fmt(bz)} mm the printer can build, so it can't be printed in one go."
            else:
                why = (f"{p['id']} is {_fmt(sx)} x {_fmt(sy)} mm on the bed, more than the "
                       f"{_fmt(bx - 2 * MIN_MARGIN)} x {_fmt(by - 2 * MIN_MARGIN)} mm the printer can use, so it can't be printed in one go.")
            raise ValueError(why)
        items.append((idx, p["id"], float(sx), float(sy)))

    order = sorted((t for t in items if t[2] is not None),
                   key=lambda t: (-(t[2] * t[3]), -max(t[2], t[3]), t[1], t[0]))
    plates: list[_Plate] = []
    first: list[int] = []
    members: list[list[int]] = []
    W, H = ux + spacing, uy + spacing
    for idx, pid, sx, sy in order:
        if sx is None:
            continue
        w, h = sx + spacing, sy + spacing
        for k, plate in enumerate(plates):
            best = plate.find(w, h)
            if best:
                plate.place(pid, best)
                break
        else:
            plate = _Plate(W, H)
            best = plate.find(w, h)
            plate.place(pid, best)
            plates.append(plate)
            first.append(idx)
            members.append([])
            k = len(plates) - 1
        members[k].append(idx)
        first[k] = min(first[k], idx)

    out = []
    groups = [(first[k], k) for k in range(len(plates))] + [(idx, ("solo", pid, rot)) for idx, pid, rot in solo]
    for _, k in sorted(groups, key=lambda g: g[0]):
        if isinstance(k, tuple):
            out.append([{"id": k[1], "x": 0.0, "y": 0.0, "rotated": k[2]}])
            continue
        rows = []
        pos = {(m): None for m in members[k]}
        # map ids back to input index (ids are expected unique; fall back by order)
        by_id: dict[str, list[int]] = {}
        for m in sorted(members[k]):
            by_id.setdefault(items[m][1], []).append(m)
        for pid, x, y, pw, ph, rot in plates[k].placed:
            m = by_id[pid].pop(0)
            fw, fh = pw - spacing, ph - spacing
            rows.append((m, {
                "id": pid,
                "x": margin + x + fw / 2 - bx / 2,
                "y": margin + y + fh / 2 - by / 2,
                "rotated": rot,
            }))
        out.append([r for _, r in sorted(rows, key=lambda t: t[0])])
    return out


def plan(pieces, bed, spacing: float = 6.0, margin: float = 5.0) -> list[list[str]]:
    """Group piece ids onto as few plates as possible (ids in input order)."""
    return [[p["id"] for p in plate] for plate in layout(pieces, bed, spacing, margin)]

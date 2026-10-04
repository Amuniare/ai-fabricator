"""Built-in hardware: screws, nuts, heat-set inserts, magnets, bearings, wall screws.

The ``Library`` gives you real dimensions and solids to subtract from a part (holes,
pockets, traps). Every tool is built with its mouth at the point ``at`` on the part
surface and pointing into the material along ``direction``. Each one also pokes
``extend`` mm out of the surface so cutting never leaves a paper-thin skin.
"""

from __future__ import annotations

import math
import re

from build123d import Axis, Cone, Cylinder, Face, Plane, Solid, Vector, Wire, Align, Location

from .settings import load_data

_PLA_FITS = {"press": 0.05, "snug": 0.10, "sliding": 0.20, "loose": 0.35, "hole": 0.15}
_OVERLAP = 0.05

CATEGORIES = {
    "screws": "Metric machine screws (M2 to M8): thread pitch, head sizes and the hole sizes for them.",
    "nuts": "Metric hex nuts: width across flats and thickness, used to make nut traps.",
    "inserts": "Heat-set brass threaded inserts, pressed into a plain hole with a soldering iron.",
    "magnets": "Round disc magnets, given as diameter x thickness in mm (for example 6x3).",
    "bearings": "Ball bearings: bore, outer diameter and width in mm.",
    "wood screws": "Wood screws for hanging a part on a wall (#6 and #8).",
}
_ALIASES = {"screw": "screws", "nut": "nuts", "insert": "inserts", "magnet": "magnets",
            "bearing": "bearings", "wood screw": "wood screws", "wall screws": "wood screws",
            "wall screw": "wood screws"}


def _names(keys) -> str:
    return ", ".join(str(k) for k in keys)


def _pick(table: dict, key, what: str):
    if key not in table:
        raise KeyError(f"Unknown {what} '{key}'. Available: {_names(table)}.")
    return table[key]


def _fmt(v) -> str:
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


# ---- geometry helpers ----------------------------------------------------------
def _cyl(d: float, z0: float, z1: float) -> Solid:
    """Cylinder of diameter d along +Z from z0 to z1."""
    c = Cylinder(d / 2, z1 - z0, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return Solid(c.wrapped).moved(Location((0, 0, z0)))


def _cone(d0: float, d1: float, z0: float, z1: float) -> Solid:
    """Cone along +Z, diameter d0 at z0 and d1 at z1."""
    c = Cone(d0 / 2, max(d1 / 2, 1e-3), z1 - z0, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return Solid(c.wrapped).moved(Location((0, 0, z0)))


def _hex(af: float, z0: float, z1: float, angle: float = 0.0) -> Solid:
    r = af / 2 / math.cos(math.pi / 6)
    pts = [Vector(r * math.cos(angle + math.radians(60 * i)), r * math.sin(angle + math.radians(60 * i)), z0)
           for i in range(6)]
    face = Face(Wire.make_polygon(pts, close=True))
    return Solid.extrude(face, Vector(0, 0, z1 - z0))


def _box(x0, x1, y0, y1, z0, z1) -> Solid:
    pts = [Vector(x0, y0, z0), Vector(x1, y0, z0), Vector(x1, y1, z0), Vector(x0, y1, z0)]
    return Solid.extrude(Face(Wire.make_polygon(pts, close=True)), Vector(0, 0, z1 - z0))


def _fuse(parts: list[Solid]) -> Solid:
    result = parts[0]
    for p in parts[1:]:
        result = result + p
    result = result.clean()
    solids = result.solids()
    if len(solids) != 1:
        raise ValueError("The hardware cutter came out in more than one piece; check the sizes given.")
    return solids[0]


def _place(solid: Solid, at, direction) -> Solid:
    d = Vector(*direction)
    if d.length < 1e-9:
        raise ValueError("The direction must point somewhere; (0, 0, 0) has no direction.")
    plane = Plane(origin=tuple(at), z_dir=d.normalized())
    return plane.location * solid


class Library:
    def __init__(self, ctx=None):
        self.ctx = ctx
        self.data = load_data("hardware.yaml")

    # ---- settings -------------------------------------------------------------
    def _gap(self, fit: str) -> float:
        if self.ctx is None:
            return _pick(_PLA_FITS, fit, "fit")
        return self.ctx.fit(fit)

    # ---- data lookups ---------------------------------------------------------
    def screw(self, size: str) -> dict:
        s = dict(_pick(self.data["screws"], size, "screw size"))
        return {"size": size, "pitch": s["pitch"], "clearance": s["clearance"],
                "clearance_close": s["clearance_close"], "clearance_coarse": s["clearance_coarse"],
                "cap_head_d": s["cap_head_d"], "cap_head_h": s["cap_head_h"],
                "csk_head_d": s["csk_head_d"], "button_head_d": s["button_head_d"],
                "button_head_h": s["button_head_h"], "hex_key": s["hex_key"],
                "lengths": list(s["lengths"])}

    def nut(self, size: str) -> dict:
        n = _pick(self.data["nuts"], size, "nut size")
        af = float(n["across_flats"])
        return {"size": size, "across_flats": af,
                "across_corners": round(af / math.cos(math.pi / 6), 3),
                "thickness": float(n["thickness"])}

    def insert(self, size: str, kind: str = "standard") -> dict:
        kinds = _pick(self.data["inserts"], size, "insert size")
        if kind not in kinds:
            raise KeyError(f"The {size} insert comes in these kinds: {_names(kinds)}. '{kind}' is not one of them.")
        i = kinds[kind]
        return {"size": size, "kind": kind, "hole_d": i["hole_d"], "length": i["length"],
                "hole_depth": i["hole_depth"], "min_wall": i["min_wall"]}

    def magnet(self, diameter: float, thickness: float) -> dict:
        if diameter <= 0 or thickness <= 0:
            raise ValueError("A magnet needs a diameter and thickness above zero.")
        key = f"{diameter:g}x{thickness:g}"
        return {"diameter": float(diameter), "thickness": float(thickness),
                "tolerance": self.data["magnets"]["tolerance"],
                "common_size": key in self.data["magnets"]["sizes"]}

    def bearing(self, code: str) -> dict:
        b = _pick(self.data["bearings"], str(code), "bearing")
        return {"code": str(code), "bore": b["bore"], "od": b["od"], "width": b["width"]}

    def wall_screw(self, size: str = "#8") -> dict:
        w = _pick(self.data["wood_screws"], size, "wood screw size")
        return {"size": size, "clearance": w["clearance"], "head_d": w["head_d"]}

    def screw_length(self, size: str, at_least: float) -> float:
        lengths = self.screw(size)["lengths"]
        for n in sorted(lengths):
            if n >= at_least:
                return float(n)
        raise ValueError(f"No {size} screw is long enough for {at_least:g} mm. "
                         f"The longest common length is {max(lengths):g} mm.")

    # ---- cutting tools --------------------------------------------------------
    def screw_hole(self, size="M3", length=10.0, head="cap", at=(0, 0, 0), direction=(0, 0, -1),
                   fit="hole", head_recess=None, extend=1.0) -> Solid:
        """Hole for a screw to pass through (or start into).

        Printed holes come out small, so the hole diameter is the ISO 273 normal
        clearance plus 2 x the fit gap. ``head`` is "cap" (counterbore), "button"
        (counterbore), "countersunk" (90 degree cone) or None (plain hole).
        ``length`` is the total depth from the surface.
        """
        s = self.screw(size)
        g = self._gap(fit)
        d = s["clearance"] + 2 * g
        parts = [_cyl(d, -extend, length)]
        if head in ("cap", "button"):
            hd = s["cap_head_d"] if head == "cap" else s["button_head_d"]
            hh = s["cap_head_h"] if head == "cap" else s["button_head_h"]
            depth = head_recess if head_recess is not None else hh + 0.2
            parts.append(_cyl(hd + 0.5 + 2 * g, -extend, depth))
        elif head == "countersunk":
            top = s["csk_head_d"] + 2 * g
            h = (top - d) / 2
            parts.append(_cone(top, d, 0.0, h))
            parts.append(_cyl(top, -extend, 0.0 + _OVERLAP))
        elif head is not None:
            raise ValueError(f"Unknown head style '{head}'. Choose cap, countersunk, button or None.")
        return _place(_fuse(parts), at, direction)

    def nut_trap(self, size="M3", at=(0, 0, 0), direction=(0, 0, -1), depth=None, fit="hole",
                 slot=None, extend=1.0, slot_length=30.0) -> Solid:
        """Hexagonal pocket for a nut, across flats + 2 x gap, nut thickness + 0.3 deep.

        ``slot`` is an optional direction (in the surface plane) to also cut a slot of
        the same width so the nut can be slid in from the side, ``slot_length`` long.
        """
        n = self.nut(size)
        g = self._gap(fit)
        af = n["across_flats"] + 2 * g
        dep = depth if depth is not None else n["thickness"] + 0.3
        parts = [_hex(af, -extend, dep)]
        if slot is not None:
            plane = Plane(origin=tuple(at), z_dir=Vector(*direction).normalized())
            v = Vector(*slot)
            lx, ly = v.dot(plane.x_dir), v.dot(plane.y_dir)
            if math.hypot(lx, ly) < 1e-9:
                raise ValueError("The slot direction must run along the surface, not straight into it.")
            a = math.degrees(math.atan2(ly, lx))
            box = _box(0.0, slot_length, -af / 2, af / 2, -extend, dep)
            box = box.rotate(Axis.Z, a)
            parts.append(box)
        return _place(_fuse(parts), at, direction)

    def insert_hole(self, size="M3", at=(0, 0, 0), direction=(0, 0, -1), kind="standard",
                    depth=None, extend=1.0) -> Solid:
        """Hole for a heat-set insert: the recommended diameter (it melts in), no gap added."""
        i = self.insert(size, kind)
        dep = depth if depth is not None else i["hole_depth"]
        return _place(_cyl(i["hole_d"], -extend, dep), at, direction)

    def magnet_pocket(self, diameter, thickness, at=(0, 0, 0), direction=(0, 0, -1),
                      fit="hole", extend=1.0) -> Solid:
        """Round pocket: diameter + 2 x gap, thickness + gap deep."""
        m = self.magnet(diameter, thickness)
        g = self._gap(fit)
        return _place(_cyl(m["diameter"] + 2 * g, -extend, m["thickness"] + g), at, direction)

    def bearing_seat(self, code="608", at=(0, 0, 0), direction=(0, 0, -1), fit="press",
                     shaft_clearance=True, through=0.0, extend=1.0) -> Solid:
        """Pocket for a bearing (outer diameter + 2 x gap, as deep as it is wide).

        With ``shaft_clearance`` and ``through`` above zero, a hole of bore + 2 mm
        continues ``through`` mm past the bottom of the pocket.
        """
        b = self.bearing(code)
        g = self._gap(fit)
        parts = [_cyl(b["od"] + 2 * g, -extend, b["width"])]
        if shaft_clearance and through > 0:
            parts.append(_cyl(b["bore"] + 2, b["width"] - _OVERLAP, b["width"] + through))
        return _place(_fuse(parts), at, direction)

    def wall_screw_hole(self, size="#8", at=(0, 0, 0), direction=(0, 0, -1), length=10.0,
                        fit="hole", extend=1.0) -> Solid:
        """Clearance hole (shank + 2 x gap) with a 90 degree countersink at the mouth."""
        w = self.wall_screw(size)
        g = self._gap(fit)
        d = w["clearance"] + 2 * g
        top = w["head_d"] + 2 * g
        h = (top - d) / 2
        parts = [_cyl(d, -extend, max(length, h + 0.1)), _cone(top, d, 0.0, h),
                 _cyl(top, -extend, _OVERLAP)]
        return _place(_fuse(parts), at, direction)

    # ---- names ----------------------------------------------------------------
    def parse(self, item: str) -> dict:
        """Understand a hardware name such as "M3x16 socket head screw" or "608 bearing"."""
        text = " ".join(str(item).strip().split())
        low = text.lower()
        if not low:
            raise ValueError("Say which hardware you mean, for example 'M3 nut' or '608 bearing'.")
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*(?:mm\s*)?dowel(?: pin)?s?", low)
        if m:
            return {"kind": "dowel", "diameter": float(m[1]), "length": float(m[2])}
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*(?:mm\s*)?(?:disc |round )?magnets?", low)
        if m:
            return {"kind": "magnet", "diameter": float(m[1]), "thickness": float(m[2])}
        m = re.fullmatch(r"(#\d+)(?:\s*(?:wood|wall)?\s*screws?)?", low)
        if m:
            return {"kind": "wood_screw", "size": m[1]}
        m = re.fullmatch(r"(\d{3,4})(?:\s*(?:zz\s*)?(?:ball )?bearings?)?", low)
        if m:
            return {"kind": "bearing", "code": m[1]}
        m = re.fullmatch(r"m(\d+(?:\.\d+)?)(?:\s*x\s*(\d+(?:\.\d+)?))?\s*(.*)", low)
        if m:
            size = "M" + m[1]
            rest = m[3].replace("mm", "").strip()
            if m[2] is None and rest in ("nut", "hex nut", "nuts"):
                return {"kind": "nut", "size": size}
            if m[2] is None and rest in ("insert", "heat-set insert", "heat set insert", "inserts"):
                return {"kind": "insert", "size": size}
            head = None
            if rest in ("", "screw", "socket head screw", "socket screw", "cap screw",
                        "socket head cap screw", "cap head screw"):
                head = "cap"
            elif "button" in rest and rest.endswith("screw"):
                head = "button"
            elif ("countersunk" in rest or "flat head" in rest) and rest.endswith("screw"):
                head = "countersunk"
            if head is not None:
                out = {"kind": "screw", "size": size, "head": head}
                if m[2] is not None:
                    n = float(m[2])
                    out["length"] = int(n) if n == int(n) else n
                return out
        raise ValueError(f"I don't know what '{item}' is. Try something like 'M3', 'M3 nut', 'M3x16 socket head "
                         f"screw', 'M3 insert', '6x3 magnet', '608 bearing', '#8 wood screw' or '6x20 mm dowel pin'.")


# ---- describe ----------------------------------------------------------------
def _table_text(title: str, rows: list[str]) -> str:
    return title + "\n" + "\n".join("  " + r for r in rows)


def _category_text(lib: Library, cat: str) -> tuple[str, dict]:
    d = lib.data
    if cat == "screws":
        rows = [f"{k}: thread pitch {_fmt(v['pitch'])} mm, head {_fmt(v['cap_head_d'])} mm wide and "
                f"{_fmt(v['cap_head_h'])} mm tall, hole {_fmt(v['clearance'])} mm, hex key {_fmt(v['hex_key'])} mm"
                for k, v in d["screws"].items()]
        data = {k: lib.screw(k) for k in d["screws"]}
    elif cat == "nuts":
        rows = [f"{k}: {_fmt(v['across_flats'])} mm across flats, {_fmt(v['thickness'])} mm thick"
                for k, v in d["nuts"].items()]
        data = {k: lib.nut(k) for k in d["nuts"]}
    elif cat == "inserts":
        rows, data = [], {}
        for k, kinds in d["inserts"].items():
            for kind in kinds:
                i = lib.insert(k, kind)
                rows.append(f"{k} {kind}: {_fmt(i['length'])} mm long, drill a {_fmt(i['hole_d'])} mm hole "
                            f"{_fmt(i['hole_depth'])} mm deep")
                data[f"{k} {kind}"] = i
    elif cat == "magnets":
        rows = [f"{s} mm (diameter x thickness)" for s in d["magnets"]["sizes"]]
        rows.append(f"Disc magnets are usually made to within +-{_fmt(d['magnets']['tolerance'])} mm.")
        data = {"sizes": list(d["magnets"]["sizes"]), "tolerance": d["magnets"]["tolerance"]}
    elif cat == "bearings":
        rows = [f"{k}: bore {_fmt(v['bore'])} mm, outer diameter {_fmt(v['od'])} mm, width {_fmt(v['width'])} mm"
                for k, v in d["bearings"].items()]
        data = {k: lib.bearing(k) for k in d["bearings"]}
    else:
        rows = [f"{k}: shank {_fmt(v['clearance'])} mm, head {_fmt(v['head_d'])} mm wide"
                for k, v in d["wood_screws"].items()]
        data = {k: lib.wall_screw(k) for k in d["wood_screws"]}
    return _table_text(f"{cat.capitalize()}: {CATEGORIES[cat]}", rows), data


def describe(item: str | None = None) -> tuple[str, dict]:
    """Plain-language description of the hardware library, a category, or one item."""
    lib = Library(None)
    d = lib.data
    if item is None:
        sizes = {
            "screws": list(d["screws"]), "nuts": list(d["nuts"]), "inserts": list(d["inserts"]),
            "magnets": list(d["magnets"]["sizes"]), "bearings": list(d["bearings"]),
            "wood screws": list(d["wood_screws"]),
        }
        lines = [f"{c}: {CATEGORIES[c]}\n    Available: {_names(sizes[c])}" for c in CATEGORIES]
        text = "Hardware Fabricator knows about:\n" + "\n".join(lines) + \
            "\nAsk about a category (such as 'screws') or one item (such as 'M3 nut' or '608')."
        return text, {"categories": sizes}

    key = " ".join(str(item).strip().lower().split())
    key = _ALIASES.get(key, key)
    if key in CATEGORIES:
        return _category_text(lib, key)

    p = lib.parse(item)
    kind = p["kind"]
    g = _PLA_FITS["hole"]
    if kind == "screw":
        s = lib.screw(p["size"])
        hole = round(s["clearance"] + 2 * g, 3)
        lines = [f"{s['size']} screw: thread pitch {_fmt(s['pitch'])} mm, hex key {_fmt(s['hex_key'])} mm.",
                 f"Socket head: {_fmt(s['cap_head_d'])} mm wide, {_fmt(s['cap_head_h'])} mm tall. "
                 f"Button head: {_fmt(s['button_head_d'])} mm wide, {_fmt(s['button_head_h'])} mm tall. "
                 f"Countersunk head: {_fmt(s['csk_head_d'])} mm wide.",
                 f"Common lengths: {_names(_fmt(x) for x in s['lengths'])} mm.",
                 f"Standard clearance hole is {_fmt(s['clearance'])} mm; Fabricator cuts {_fmt(hole)} mm "
                 f"with the default PLA gap, with a {_fmt(round(s['cap_head_d'] + 0.5 + 2 * g, 3))} mm "
                 f"counterbore for a socket head."]
        data = {**s, "hole_d": hole}
        if "length" in p:
            if p["length"] not in s["lengths"]:
                lines.append(f"Note: {_fmt(p['length'])} mm is not a common length for {s['size']}.")
            data["length"] = p["length"]
            data["head"] = p["head"]
        return "\n".join(lines), data
    if kind == "nut":
        n = lib.nut(p["size"])
        af = round(n["across_flats"] + 2 * g, 3)
        dep = round(n["thickness"] + 0.3, 3)
        text = (f"{n['size']} hex nut: {_fmt(n['across_flats'])} mm across flats, {_fmt(n['across_corners'])} mm "
                f"across corners, {_fmt(n['thickness'])} mm thick.\nFabricator cuts a nut trap {_fmt(af)} mm "
                f"across flats and {_fmt(dep)} mm deep.")
        return text, {**n, "trap_across_flats": af, "trap_depth": dep}
    if kind == "insert":
        out, lines = {}, []
        for kd in _pick(d["inserts"], p["size"], "insert size"):
            i = lib.insert(p["size"], kd)
            out[kd] = i
            lines.append(f"{p['size']} {kd} heat-set insert: {_fmt(i['length'])} mm long. Fabricator drills a "
                         f"{_fmt(i['hole_d'])} mm hole {_fmt(i['hole_depth'])} mm deep; leave at least "
                         f"{_fmt(i['min_wall'])} mm of wall around it.")
        return "\n".join(lines), out
    if kind == "magnet":
        m = lib.magnet(p["diameter"], p["thickness"])
        dia, dep = round(m["diameter"] + 2 * g, 3), round(m["thickness"] + g, 3)
        note = "" if m["common_size"] else " (not one of the common sizes)"
        text = (f"{_fmt(m['diameter'])}x{_fmt(m['thickness'])} mm disc magnet{note}, made to within "
                f"+-{_fmt(m['tolerance'])} mm.\nFabricator cuts a pocket {_fmt(dia)} mm wide and {_fmt(dep)} mm deep.")
        return text, {**m, "pocket_d": dia, "pocket_depth": dep}
    if kind == "bearing":
        b = lib.bearing(p["code"])
        seat = round(b["od"] + 2 * _PLA_FITS["press"], 3)
        text = (f"{b['code']} ball bearing: bore {_fmt(b['bore'])} mm, outer diameter {_fmt(b['od'])} mm, "
                f"width {_fmt(b['width'])} mm.\nFabricator cuts a seat {_fmt(seat)} mm wide and "
                f"{_fmt(b['width'])} mm deep, with a {_fmt(b['bore'] + 2)} mm shaft hole past it.")
        return text, {**b, "seat_d": seat}
    if kind == "wood_screw":
        w = lib.wall_screw(p["size"])
        hole, head = round(w["clearance"] + 2 * g, 3), round(w["head_d"] + 2 * g, 3)
        text = (f"{w['size']} wood screw: {_fmt(w['clearance'])} mm shank, {_fmt(w['head_d'])} mm head.\n"
                f"Fabricator cuts a {_fmt(hole)} mm hole with a 90 degree countersink {_fmt(head)} mm wide.")
        return text, {**w, "hole_d": hole, "countersink_d": head}
    # dowel
    text = (f"{_fmt(p['diameter'])} x {_fmt(p['length'])} mm dowel pin. Fabricator cuts a snug "
            f"{_fmt(round(p['diameter'] + 2 * 0.10, 3))} mm hole for it.")
    return text, p

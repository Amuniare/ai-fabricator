import pytest
from build123d import Box, Location

from fabricator import library
from fabricator.library import Library

UP = (0, 0, 1)  # drilling up the +Z axis keeps local and world coordinates the same
PLA = {"press": 0.05, "snug": 0.10, "sliding": 0.20, "loose": 0.35, "hole": 0.15}


class FakeCtx:
    min_wall = 0.8

    def fit(self, name):
        return PLA[name]


@pytest.fixture
def lib():
    return Library(FakeCtx())


def bb(s):
    return s.bounding_box()


def test_clearance_hole_diameter(lib):
    b = bb(lib.screw_hole("M3", length=10, head=None, direction=UP))
    assert b.size.X == pytest.approx(3.4 + 0.3, abs=1e-3)
    assert b.size.Y == pytest.approx(3.7, abs=1e-3)
    assert b.min.Z == pytest.approx(-1.0, abs=1e-3)
    assert b.max.Z == pytest.approx(10.0, abs=1e-3)


def test_counterbore(lib):
    s = lib.screw_hole("M3", length=10, head="cap", direction=UP)
    assert bb(s).size.X == pytest.approx(5.5 + 0.5 + 0.3, abs=1e-3)
    probe = Box(20, 20, 1).moved(Location((0, 0, 4.0)))
    assert (s & probe).bounding_box().size.X == pytest.approx(3.7, abs=1e-3)
    probe = Box(20, 20, 1).moved(Location((0, 0, 2.0)))
    assert (s & probe).bounding_box().size.X == pytest.approx(6.3, abs=1e-3)


def test_countersunk(lib):
    b = bb(lib.screw_hole("M3", length=10, head="countersunk"))
    assert b.size.X == pytest.approx(6.72 + 0.3, abs=1e-3)


def test_nut_trap(lib):
    b = bb(lib.nut_trap("M3", direction=UP))
    assert min(b.size.X, b.size.Y) == pytest.approx(5.5 + 0.3, abs=1e-3)
    assert b.max.Z == pytest.approx(2.4 + 0.3, abs=1e-3)


def test_nut_trap_slot(lib):
    b = bb(lib.nut_trap("M3", slot=(1, 0, 0), slot_length=20, direction=UP))
    assert b.max.X == pytest.approx(20, abs=1e-3)
    assert b.size.Y > 5.0


@pytest.mark.parametrize("direction,axis,sign", [((1, 0, 0), "X", 1), ((0, -1, 0), "Y", -1),
                                                 ((0, 0, -1), "Z", -1)])
def test_direction(lib, direction, axis, sign):
    at = (10, 20, 30)
    b = bb(lib.screw_hole("M3", length=10, head=None, at=at, direction=direction))
    i = "XYZ".index(axis)
    lo, hi = tuple(b.min)[i], tuple(b.max)[i]
    far, near = (hi, lo) if sign > 0 else (lo, hi)
    assert far == pytest.approx(at[i] + sign * 10, abs=1e-3)
    assert near == pytest.approx(at[i] - sign * 1.0, abs=1e-3)


def test_other_tools(lib):
    assert bb(lib.magnet_pocket(6, 3)).size.X == pytest.approx(6.3, abs=1e-3)
    assert bb(lib.insert_hole("M3", direction=UP)).max.Z == pytest.approx(6.7, abs=1e-3)
    seat = bb(lib.bearing_seat("608", through=5, direction=UP))
    assert seat.size.X == pytest.approx(22.1, abs=1e-3)
    assert seat.max.Z == pytest.approx(12.0, abs=1e-3)
    assert bb(lib.wall_screw_hole("#8")).size.X == pytest.approx(8.2 + 2 * 0.15, abs=1e-3)


def test_default_direction_is_down(lib):
    b = bb(lib.screw_hole("M3", length=10, head=None))
    assert b.min.Z == pytest.approx(-10.0, abs=1e-3)
    assert b.max.Z == pytest.approx(1.0, abs=1e-3)


def test_no_ctx_defaults():
    assert bb(Library(None).screw_hole("M3", head=None)).size.X == pytest.approx(3.7, abs=1e-3)


def test_parse(lib):
    assert lib.parse("M3x16 socket head screw") == {"kind": "screw", "size": "M3", "length": 16, "head": "cap"}
    assert lib.parse("M3 nut") == {"kind": "nut", "size": "M3"}
    assert lib.parse("6x20 mm dowel pin") == {"kind": "dowel", "diameter": 6, "length": 20}
    assert lib.parse("6x3 magnet") == {"kind": "magnet", "diameter": 6, "thickness": 3}
    assert lib.parse("608 bearing") == {"kind": "bearing", "code": "608"}
    assert lib.parse("M3 insert") == {"kind": "insert", "size": "M3"}
    assert lib.parse("#8 wood screw") == {"kind": "wood_screw", "size": "#8"}
    assert lib.parse("M4x10 button head screw")["head"] == "button"


def test_lookups(lib):
    assert lib.screw("M3")["cap_head_d"] == 5.5
    assert lib.nut("M3")["across_flats"] == 5.5
    assert lib.insert("M3", "short")["length"] == 3.0
    assert lib.bearing("608")["od"] == 22
    assert lib.screw_length("M3", 13) == 14
    assert lib.screw_length("M3", 16) == 16


def test_errors(lib):
    with pytest.raises(ValueError, match="long enough"):
        lib.screw_length("M3", 500)
    with pytest.raises(KeyError, match="Available"):
        lib.screw("M7")
    with pytest.raises(ValueError, match="don't know"):
        library.describe("flux capacitor")
    with pytest.raises(KeyError, match="kinds"):
        lib.insert("M4", "short")


@pytest.mark.parametrize("item", [None, "screws", "nuts", "inserts", "magnets", "bearings", "wood screws",
                                  "M3", "M3 nut", "608", "6x3 magnet", "#8", "M3x16 socket head screw",
                                  "M3 insert", "6x20 mm dowel pin"])
def test_describe(item):
    text, data = library.describe(item)
    assert text.strip() and isinstance(data, dict) and data

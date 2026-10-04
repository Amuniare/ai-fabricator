import numpy as np
from build123d import Box, Cylinder, Pos, Rot

from fabricator import orient
from fabricator.design import Part3D

BED = (180, 180, 180)


def part(shape, **kw):
    return Part3D("P01", "test", shape, "#4C78A8", **kw)


def l_bracket():
    return Box(60, 40, 4) + Pos(-28, 0, 20) * Box(4, 40, 40)


def t_shape():
    return Pos(0, 0, 25) * Box(60, 20, 10) + Pos(0, 0, 10) * Box(10, 20, 20)


def test_l_bracket_lies_on_big_flat_side_without_supports():
    c = orient.choose(part(l_bracket()), BED)
    assert c.fits and not c.support_needed
    assert c.contact_area_mm2 > 2000
    assert c.overhang_area_mm2 == 0


def test_t_flat_needs_no_support_but_standing_on_stem_does():
    cands = orient.candidates(part(t_shape()), BED)
    best = cands[0]
    assert not best.support_needed
    on_stem = [c for c in cands if c.height_mm > 55]
    assert on_stem and all(c.support_needed for c in on_stem if c.face_down in ("-X", "+X", "-Y", "+Y") and c.height_mm > 55 and c.contact_area_mm2 < 300)


def test_tall_pillar_is_laid_down():
    c = orient.choose(part(Box(10, 10, 100)), BED)
    assert c.height_mm < 15


def test_cylinder_stands_on_its_end():
    c = orient.choose(part(Cylinder(10, 50)), BED)
    assert c.label in ("as modelled", "standing on its end") and not c.support_needed


def test_face_down_hint_is_respected():
    c = orient.choose(part(l_bracket(), face_down="-X"), BED)
    assert c.face_down == "-X"


def test_place_on_bed_matches_orientation():
    shape = t_shape()
    c = orient.choose(part(shape), BED)
    placed = orient.place_on_bed(shape, c)
    bb = placed.bounding_box()
    assert abs(bb.min.Z) < 1e-6
    assert abs((bb.min.X + bb.max.X) / 2) < 1e-6 and abs((bb.min.Y + bb.max.Y) / 2) < 1e-6
    assert abs(bb.size.Z - c.height_mm) < 0.1
    assert abs(placed.volume - shape.volume) < 1e-3 * shape.volume


def test_quarter_turn_about_z_to_fit():
    # 175 mm long fits a 180 bed only along an axis that has the room; bed is narrow in X.
    shape = Box(30, 175, 5)
    c = orient.choose(part(shape), (100, 180, 180))
    assert c.fits
    big = orient.choose(part(Box(300, 20, 5)), BED)
    assert not big.fits


def test_choose_is_deterministic_and_json_safe():
    import json

    a = orient.choose(part(l_bracket()), BED)
    b = orient.choose(part(l_bracket()), BED)
    assert a.to_dict() == b.to_dict()
    json.dumps(a.to_dict())


def test_rotation_matrix_applies_exactly():
    shape = Box(10, 20, 30)
    for c in orient.candidates(part(shape), BED):
        placed = orient.place_on_bed(shape, c)
        size = placed.bounding_box().size
        expected = np.abs(c.matrix()) @ np.array([10, 20, 30])
        assert np.allclose([size.X, size.Y, size.Z], expected, atol=1e-3)

from pathlib import Path

import pytest
from build123d import Box, Cylinder, Pos

from fabricator import split
from fabricator.checks import assembly
from fabricator.design import Context, Model
from fabricator.settings import Settings


@pytest.fixture(scope="module")
def ctx():
    return Context(Settings(), (180.0, 180.0, 180.0), Path("."))


def _bar():
    m = Model("Bar")
    m.add(Box(600, 100, 20), "Bar")
    return m


def _fails(checks):
    return [c for c in checks if c.status == "fail"]


def test_bar_splits_into_fewest_pieces_with_pegs(ctx):
    model, messages = split.split_oversized(_bar(), ctx, {"joint": "peg", "fit": "snug"})
    assert [p.id for p in model.parts] == ["P01", "P02", "P03", "P04"]
    assert all(p.source_part == "P01" for p in model.parts)
    assert len({p.color for p in model.parts}) == 4
    for p in model.parts:
        assert split.fits_bed(split.size_of(p.shape), ctx.bed)
        assert len(p.shape.solids()) == 1 and p.shape.is_valid
    assert len(model.joints) == 3
    assert {j.kind for j in model.joints} == {"peg"}
    assert [(j.a, j.b) for j in model.joints] == [("P01", "P02"), ("P02", "P03"), ("P03", "P04")]
    # cuts land exactly at even spacing for a plain bar
    assert sorted(j.at[0] for j in model.joints) == pytest.approx([-150, 0, 150], abs=0.01)
    assert "600 mm long" in messages[0] and "4 pieces" in messages[0] and "pegs" in messages[0]

    checks = assembly.check_model(model, ctx)
    assert not _fails(checks), _fails(checks)
    gaps = [c for c in checks if c.name == "joint gap"]
    assert len(gaps) == 3
    for c in gaps:
        assert c.status == "pass" and c.value == pytest.approx(0.10, abs=0.02)
    # every individual peg measured
    for j in model.joints:
        for peg in j.features["male"].solids():
            assert model.part(j.b).shape.distance_to(peg) == pytest.approx(0.10, abs=0.02)


def test_split_is_deterministic(ctx):
    def run():
        m, _ = split.split_oversized(_bar(), ctx, {})
        return ([(p.id, p.name, round(p.shape.volume, 3)) for p in m.parts],
                [(j.a, j.b, j.at, j.note) for j in m.joints])

    assert run() == run()


def test_part_that_fits_is_untouched(ctx):
    m = Model("Small")
    part = m.add(Box(170, 50, 20), "Small")
    out, messages = split.split_oversized(m, ctx, {})
    assert out.parts == [part] and messages == [] and out.joints == []


def test_rotated_part_counts_as_fitting(ctx):
    assert split.fits_bed((20, 177, 30), ctx.bed)  # 177 fits standing up (178 mm height)
    assert not split.fits_bed((20, 179, 30), ctx.bed)


def test_plate_splits_on_two_axes(ctx):
    m = Model("Plate")
    m.add(Box(300, 300, 10), "Plate")
    model, messages = split.split_oversized(m, ctx, {})
    assert [p.id for p in model.parts] == ["P01", "P02", "P03", "P04"]
    assert all(", " in p.name and "piece" in p.name for p in model.parts)
    assert {tuple(abs(v) for v in j.axis) for j in model.joints} == {(1.0, 0.0, 0.0), (0.0, 1.0, 0.0)}
    assert len(model.joints) == 4
    assert not _fails(assembly.check_model(model, ctx))


def test_manual_seams_are_used_exactly(ctx):
    m = Model("Beam")
    m.add(Box(400, 60, 20), "Beam")
    model, messages = split.split_oversized(m, ctx, {"seams": [{"axis": "x", "at": -60}, {"axis": "x", "at": 80}]})
    assert len(model.parts) == 3
    assert sorted(j.at[0] for j in model.joints) == pytest.approx([-60, 80], abs=1e-6)
    assert "seams you chose" in messages[0]
    assert not _fails(assembly.check_model(model, ctx))


def test_tiny_face_falls_back_to_glue(ctx):
    m = Model("Rod")
    m.add(Box(300, 5, 5), "Rod")
    model, messages = split.split_oversized(m, ctx, {})
    assert len(model.parts) == 2
    assert [j.kind for j in model.joints] == ["glue"]
    assert any("glue" in msg and "too small" in msg for msg in messages)


def test_cut_avoids_a_hole(ctx):
    m = Model("Bar")
    # 280 mm needs 2 pieces; the even cut at x = 0 would go straight through the hole.
    m.add(Box(280, 60, 20) - Pos(0, 0, 0) * Cylinder(8, 30), "Bar")
    model, _ = split.split_oversized(m, ctx, {})
    assert len(model.parts) == 2
    cut_x = model.joints[0].at[0]
    assert abs(cut_x) > 8 + 5  # not through the hole or right next to it
    assert not [c for c in assembly.check_model(model, ctx) if c.status == "fail"]


@pytest.mark.parametrize("kind,expect", [("dowel", "dowel"), ("screw", "screw"), ("dovetail", "dovetail"),
                                         ("tongue-groove", "tongue-groove")])
def test_other_joint_kinds(ctx, kind, expect):
    model, messages = split.split_oversized(_bar(), ctx, {"joint": kind})
    assert {j.kind for j in model.joints} == {expect}
    checks = assembly.check_model(model, ctx)
    assert not _fails(checks), _fails(checks)
    if kind == "screw":
        assert any("M3x" in h for j in model.joints for h in j.hardware)
        assert any(c.name == "tool access" and c.status == "pass" for c in checks)
    if kind == "dowel":  # printed pins are parts of their own, numbered after the pieces
        pins = [p for p in model.parts if "pin" in p.name]
        assert pins and pins[0].id == "P05"


def test_steel_dowels_are_hardware(ctx):
    model, _ = split.split_oversized(_bar(), ctx, {"joint": "dowel", "dowel": "steel"})
    assert len(model.parts) == 4
    assert all(h.endswith("mm dowel pin") for j in model.joints for h in j.hardware)
    assert not _fails(assembly.check_model(model, ctx))


def test_unknown_joint_falls_back_to_pegs(ctx):
    model, messages = split.split_oversized(_bar(), ctx, {"joint": "rivet"})
    assert {j.kind for j in model.joints} == {"peg"}
    assert any("rivet" in m for m in messages)


def test_references_and_design_joints_are_kept(ctx):
    m = Model("Stand")
    beam = m.add(Box(400, 60, 15), "Beam")
    knob = m.add(Pos(185, 0, 22.5) * Box(30, 30, 30), "Knob")
    m.reference(Pos(0, 0, 40) * Cylinder(10, 20), "Drill")
    m.join(beam, knob, "rest", at=(185, 0, 7.5))
    model, _ = split.split_oversized(m, ctx, {})
    ids = [p.id for p in model.parts]
    assert ids[:3] == ["P01", "P02", "P03"] and "P04" in ids and any(i.startswith("R") for i in ids)
    assert model.part("P04").name == "Knob"
    rest = [j for j in model.joints if j.kind == "rest"][0]
    assert (rest.a, rest.b) == ("P03", "P04")  # re-pointed to the piece under the knob


def test_z_split_puts_pegs_on_the_bottom_piece(ctx):
    m = Model("Tower")
    m.add(Box(120, 120, 300), "Tower")
    model, _ = split.split_oversized(m, ctx, {})
    assert [p.name for p in model.parts] == ["Tower, bottom piece", "Tower, top piece"]
    j = model.joints[0]
    assert j.a == "P01" and j.axis == (0.0, 0.0, 1.0)  # pegs point up out of the bottom piece


def test_round_rod_pieces_ask_to_stand_on_the_face_without_pegs(ctx):
    from build123d import Rotation

    m = Model("Rod")
    m.add(Rotation(0, 90, 0) * Cylinder(25, 400), "Rod")
    model, messages = split.split_oversized(m, ctx, {})
    assert len(model.parts) == 3
    # the pegs point +x out of P01 and P02, so their -x end should go on the bed
    assert [p.face_down for p in model.parts] == ["-X", "-X", None]
    assert not [c for c in assembly.check_model(model, ctx) if c.status == "fail"]

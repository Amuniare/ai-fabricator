from pathlib import Path

import pytest
from build123d import Box, Pos

from fabricator import joints
from fabricator.design import Context, Model
from fabricator.settings import Settings


@pytest.fixture(scope="module")
def ctx():
    return Context(Settings(), (180.0, 180.0, 180.0), Path("."))


def _common(a, b):
    r = a.intersect(b)
    return 0.0 if r is None else sum(s.volume for s in r.solids())


A = Pos(-50, 0, 0) * Box(100, 40, 20)
B = Pos(50, 0, 0) * Box(100, 40, 20)


def test_peg_gap_matches_fit(ctx):
    r = joints.peg(A, B, at=[(0, -10, 0), (0, 10, 0)], direction=(1, 0, 0), ctx=ctx, diameter=6)
    assert r.kind == "peg" and r.gap == ctx.fit("snug")
    assert r.a.volume > A.volume and r.b.volume < B.volume
    males = r.features["male"].solids()
    assert len(males) == 2
    for m in males:
        assert r.b.distance_to(m) == pytest.approx(ctx.fit("snug"), abs=1e-6)
    assert _common(r.a, r.b) < 1e-6
    # hole is deeper than the peg: the peg tip doesn't reach the bottom
    peg_tip = max(m.bounding_box().max.X for m in males)
    assert peg_tip < 100


def test_peg_fit_names(ctx):
    r = joints.peg(A, B, at=(0, 0, 0), direction=(1, 0, 0), ctx=ctx, fit="loose")
    assert r.b.distance_to(r.features["male"]) == pytest.approx(ctx.fit("loose"), abs=1e-6)


def test_dowel_printed_and_steel(ctx):
    r = joints.dowel(A, B, at=[(0, -10, 0), (0, 10, 0)], direction=(1, 0, 0), ctx=ctx)
    assert len(r.parts) == 2 and r.hardware == []
    for pin, _ in r.parts:
        assert r.a.distance_to(pin) == pytest.approx(ctx.fit("snug"), abs=1e-6)
        assert r.b.distance_to(pin) == pytest.approx(ctx.fit("snug"), abs=1e-6)
    s = joints.dowel(A, B, at=(0, 0, 0), direction=(1, 0, 0), ctx=ctx, diameter=6, length=20, pin="steel")
    assert s.parts == [] and s.hardware == ["6x20 mm dowel pin"]


def test_screw_with_nut_and_insert(ctx):
    top = Pos(0, 0, 5) * Box(40, 40, 10)
    bottom = Pos(0, 0, -5) * Box(40, 40, 10)
    r = joints.screw(top, bottom, at=(0, 0, 10), direction=(0, 0, -1), ctx=ctx)
    assert r.hardware[0].startswith("M3x") and r.hardware[1] == "M3 nut"
    length = float(r.hardware[0].split("x")[1].split()[0])
    assert length <= 20 - 3.2  # stays inside the parts
    assert _common(r.features["tool"], r.a) < 0.05 and _common(r.features["tool"], r.b) < 0.05
    assert r.features["tool"].bounding_box().size.Z > 99
    i = joints.screw(top, bottom, at=(0, 0, 10), direction=(0, 0, -1), ctx=ctx, nut="insert")
    assert i.hardware[1] == "M3 heat-set insert"


def test_screw_line_must_cross_both_parts(ctx):
    with pytest.raises(ValueError, match="doesn't pass through both parts"):
        joints.screw(A, B, at=(0, 0, 10), direction=(0, 0, 1), ctx=ctx)


def test_dovetail_and_tongue(ctx):
    d = joints.dovetail(A, B, at=(0, 0, 0), direction=(1, 0, 0), slide=(0, 0, 1), ctx=ctx)
    assert d.b.distance_to(d.features["male"]) == pytest.approx(ctx.fit("sliding"), abs=1e-6)
    assert _common(d.a, d.b) < 1e-6
    assert d.axis == (0.0, 0.0, 1.0)
    # the tongue is clipped to the face: it doesn't stick out past the part
    bb = d.a.bounding_box()
    assert bb.min.Z == pytest.approx(-10) and bb.max.Z == pytest.approx(10)
    t = joints.tongue_groove(A, B, at=(0, 0, 0), direction=(1, 0, 0), along=(0, 1, 0), ctx=ctx, length=20)
    assert t.b.distance_to(t.features["male"]) == pytest.approx(ctx.fit("snug"), abs=1e-6)


def test_snap_hook(ctx):
    wall = Pos(0, 0, 10) * Box(40, 4, 20)
    base = Pos(0, -10, 0) * Box(20, 16, 4)
    r = joints.snap_hook(base, wall, at=(0, -2, 2), direction=(0, 0, 1), hook=(0, 1, 0), ctx=ctx)
    assert r.a.is_valid and r.b.is_valid
    assert r.b.distance_to(r.features["male"]) == pytest.approx(ctx.fit("sliding"), abs=1e-6)
    assert _common(r.a, r.b) < 1e-6


def test_join_records_joint_with_features(ctx):
    m = Model("Box")
    r = joints.peg(A, B, at=(0, 0, 0), direction=(1, 0, 0), ctx=ctx)
    a, b = m.add(r.a, "Left"), m.add(r.b, "Right")
    made = r.join(m, a, b)
    assert len(made) == 1 and m.joints[0].features["male"] is not None and m.joints[0].gap == r.gap
    m2 = Model("Pinned")
    d = joints.dowel(A, B, at=(0, 0, 0), direction=(1, 0, 0), ctx=ctx)
    a, b = m2.add(d.a, "Left"), m2.add(d.b, "Right")
    d.join(m2, a, b)
    assert [p.id for p in m2.parts] == ["P01", "P02", "P03"]
    assert {(j.a, j.b) for j in m2.joints} == {("P03", "P01"), ("P03", "P02")}

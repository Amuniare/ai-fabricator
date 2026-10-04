from pathlib import Path

import pytest
from build123d import Box, Cylinder, Pos

from fabricator import joints
from fabricator.checks import assembly
from fabricator.design import Context, Model
from fabricator.settings import Settings


@pytest.fixture(scope="module")
def ctx():
    return Context(Settings(), (180.0, 180.0, 180.0), Path("."))


def _by(checks, name, status=None):
    return [c for c in checks if c.name == name and (status is None or c.status == status)]


def test_overlapping_parts_fail(ctx):
    m = Model("Two")
    m.add(Box(20, 20, 20), "A")
    m.add(Pos(15, 0, 0) * Box(20, 20, 20), "B")
    m.join("P01", "P02", "glue", at=(10, 0, 0))
    checks = assembly.check_model(m, ctx)
    fail = _by(checks, "parts overlap", "fail")
    assert fail and "P01 and P02" in fail[0].message and fail[0].value == pytest.approx(2000, rel=1e-3)
    assert fail[0].where


def test_touching_parts_pass(ctx):
    m = Model("Two")
    m.add(Box(20, 20, 20), "A")
    m.add(Pos(20, 0, 0) * Box(20, 20, 20), "B")
    m.join("P01", "P02", "glue", at=(10, 0, 0))
    checks = assembly.check_model(m, ctx)
    assert _by(checks, "parts overlap", "pass") and not [c for c in checks if c.status != "pass"]


def test_reference_hit_and_clearance(ctx):
    m = Model("Holder")
    m.add(Box(40, 40, 10), "Holder")
    m.reference(Pos(0, 0, 10) * Cylinder(5, 20), "Drill")
    checks = assembly.check_model(m, ctx)
    hit = _by(checks, "hits reference", "fail")
    assert hit and "the drill would hit P01" in hit[0].message

    m = Model("Holder")
    m.add(Box(40, 40, 10), "Holder")
    m.reference(Pos(0, 0, 15.02) * Cylinder(5, 20), "Drill")
    checks = assembly.check_model(m, ctx)
    assert _by(checks, "reference clearance", "warn")


def test_joint_gap_measured(ctx):
    a = Pos(-50, 0, 0) * Box(100, 40, 20)
    b = Pos(50, 0, 0) * Box(100, 40, 20)
    r = joints.peg(a, b, at=(0, 0, 0), direction=(1, 0, 0), ctx=ctx, fit="snug")
    m = Model("Pegged")
    pa, pb = m.add(r.a, "A"), m.add(r.b, "B")
    r.join(m, pa, pb)
    checks = assembly.check_model(m, ctx)
    ok = _by(checks, "joint gap", "pass")
    assert ok and ok[0].value == pytest.approx(0.10, abs=0.02)

    # the same pegs recorded as a press fit: the hole is too big for that
    m.joints[0].gap = ctx.fit("press")
    m.joints[0].fit = "press"
    bad = _by(assembly.check_model(m, ctx), "joint gap", "fail")
    assert bad and "0.10 mm" in bad[0].message and bad[0].value == pytest.approx(0.10, abs=1e-3)


def test_peg_without_hole_fails(ctx):
    a = Pos(-50, 0, 0) * Box(100, 40, 20)
    b = Pos(50, 0, 0) * Box(100, 40, 20)
    r = joints.peg(a, b, at=(0, 0, 0), direction=(1, 0, 0), ctx=ctx)
    m = Model("Bad")
    pa, pb = m.add(r.a, "A"), m.add(b, "B")  # b without its hole
    r.join(m, pa, pb)
    checks = assembly.check_model(m, ctx)
    assert _by(checks, "parts overlap", "fail")
    assert "runs into" in _by(checks, "joint gap", "fail")[0].message


def test_tool_access(ctx):
    top = Pos(0, 0, 5) * Box(40, 40, 10)
    bottom = Pos(0, 0, -5) * Box(40, 40, 10)
    r = joints.screw(top, bottom, at=(0, 0, 10), direction=(0, 0, -1), ctx=ctx)
    m = Model("Screwed")
    a, b = m.add(r.a, "Top"), m.add(r.b, "Bottom")
    r.join(m, a, b)
    assert _by(assembly.check_model(m, ctx), "tool access", "pass")
    m.add(Pos(0, 0, 40) * Box(30, 30, 5), "Cover")
    m.join("P01", "P03", "rest", at=(0, 0, 37.5))
    fail = _by(assembly.check_model(m, ctx), "tool access", "fail")
    assert fail and "can't reach the screw" in fail[0].message and "P03" in fail[0].message


def test_too_big_and_unattached(ctx):
    m = Model("Loose")
    m.add(Box(300, 20, 20), "Long")
    m.add(Pos(0, 50, 0) * Box(20, 20, 20), "Small")
    checks = assembly.check_model(m, ctx)
    assert _by(checks, "fits printer", "fail")[0].part == "P01"
    warned = {c.part for c in _by(checks, "attached", "warn")}
    assert warned == {"P01", "P02"}
    assert "isn't attached to anything" in _by(checks, "attached", "warn")[0].message

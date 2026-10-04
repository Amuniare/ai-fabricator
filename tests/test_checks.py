from types import SimpleNamespace

from build123d import Box, Cylinder, Pos, Rot

from fabricator import orient
from fabricator.checks import geometry, printability
from fabricator.design import Part3D
from fabricator.meshing import to_mesh

CTX = SimpleNamespace(bed=(180, 180, 180), min_wall=0.8, min_feature=0.6, material="PLA")


def run(shape, ctx=CTX, face_down=None, printed=True):
    part = Part3D("P01", "test", shape, "#4C78A8", printed=printed, face_down=face_down)
    choice = orient.choose(part, ctx.bed, ctx)
    placed = orient.place_on_bed(shape, choice)
    mesh = to_mesh(placed)
    g = geometry.check_part(part, placed, mesh, ctx, choice)
    p = printability.check_part(part, placed, mesh, ctx, choice)
    return {c.name: c for c in g + p}


def test_simple_box_passes_everything():
    r = run(Box(30, 30, 10))
    assert all(c.status == "pass" for c in r.values()), {k: (v.status, v.message) for k, v in r.items()}
    assert r["wall thickness"].value == 10 or abs(r["wall thickness"].value - 10) < 0.2


def test_not_printed_gives_nothing():
    assert run(Box(5, 5, 5), printed=False) == {}


def test_thin_wall_fails_with_value_and_place():
    cup = Box(30, 30, 20) - Pos(0, 0, 1.5) * Box(29, 29, 20)
    r = run(cup)
    w = r["wall thickness"]
    assert w.status == "fail" and abs(w.value - 0.5) < 0.05
    assert w.where and w.fix and "0.5" in w.message


def test_marginal_wall_warns():
    cup = Box(30, 30, 20) - Pos(0, 0, 1.5) * Box(28.5, 28.5, 20)  # 0.75 mm
    assert run(cup)["wall thickness"].status == "warn"


def test_too_big_for_bed_fails_and_says_which_way():
    r = run(Box(250, 20, 5))
    c = r["fits the bed"]
    assert c.status == "fail" and "too big" in c.message


def test_two_bodies_fail():
    r = run(Box(10, 10, 10) + Pos(40, 0, 0) * Box(10, 10, 10))
    assert r["one body"].status == "fail"


def test_tiny_bit_fails():
    r = run(Box(10, 10, 10) + Pos(40, 0, 0) * Box(0.5, 0.5, 0.5))
    assert r["tiny loose bits"].status == "fail"


def test_t_flat_has_no_overhang_but_forced_upright_does():
    t = Pos(0, 0, 25) * Box(60, 20, 10) + Pos(0, 0, 10) * Box(10, 20, 20)
    assert run(t)["overhangs"].status == "pass"
    forced = run(t, face_down="-Z")["overhangs"]
    assert forced.status == "warn" and forced.value > 25 and forced.where


def test_tall_thin_pillar_warns_when_forced_upright():
    r = run(Box(8, 8, 80), face_down="-Z")
    assert r["tall and thin"].status == "warn"
    assert run(Box(8, 8, 80))["tall and thin"].status == "pass"  # laid down instead


def test_small_contact_warns():
    from build123d import Sphere

    r = run(Sphere(15))
    assert r["bed contact"].status == "warn"


def test_long_bridge_warns():
    # a 60 mm wide slab held up by two thin legs
    slab = Pos(0, 0, 20) * Box(60, 60, 4) + Pos(-27, 0, 9) * Box(6, 60, 18) + Pos(27, 0, 9) * Box(6, 60, 18)
    r = run(slab, face_down="-Z")
    assert r["long bridges"].status == "warn"


def test_sideways_hole_droop_and_warping():
    block = Box(60, 40, 40) - Rot(90, 0, 0) * Cylinder(10, 50)
    r = run(block, face_down="-Z")
    assert r["sideways holes"].status == "warn"
    ctx = SimpleNamespace(bed=(300, 300, 300), min_wall=0.8, min_feature=0.6, material="ABS")
    assert run(Box(200, 20, 5), ctx)["warping"].status == "warn"
    assert "warping" not in run(Box(20, 20, 5))


def test_checks_are_fast_on_a_busy_part():
    import time

    body = Box(80, 60, 30)
    for i in range(10):
        for j in range(8):
            body -= Pos(-36 + i * 8, -26 + j * 7, 5) * Cylinder(2, 30)
    t = time.time()
    run(body)
    assert time.time() - t < 15

import pytest

from fabricator.plates import layout, plan

BED = (180, 180, 180)


def P(i, x, y, z=10):
    return {"id": i, "size": [x, y, z]}


def test_small_parts_one_plate():
    pcs = [P(f"P{i}", 40, 40) for i in range(6)]
    assert plan(pcs, BED) == [[f"P{i}" for i in range(6)]]


def test_big_parts_three_plates():
    pcs = [P(f"P{i}", 150, 150) for i in range(3)]
    assert len(plan(pcs, BED)) == 3


def test_two_160x80_share_plate_with_rotation():
    # usable 170: 80+6+80 = 166 fits side by side, rotated one on the other axis
    pcs = [P("A", 160, 80), P("B", 160, 80)]
    assert plan(pcs, BED) == [["A", "B"]]
    pcs = [P("A", 160, 80), P("B", 80, 160)]
    assert plan(pcs, BED) == [["A", "B"]]


def test_mixed_minimal():
    # 170 usable: 3 pieces of 80x80 per 2x2 grid slot -> 4 per plate; 5 need 2
    pcs = [P(f"S{i}", 80, 80) for i in range(5)]
    assert len(plan(pcs, BED)) == 2
    pcs += [P("L", 160, 160)]
    assert len(plan(pcs, BED)) == 3


def test_deterministic_and_input_order():
    pcs = [P("b", 30, 50), P("a", 90, 40), P("c", 30, 50), P("d", 100, 100)]
    assert plan(pcs, BED) == plan(list(pcs), BED)
    flat = plan(pcs, BED)[0]
    assert flat == [i for i in ["b", "a", "c", "d"] if i in flat]


def test_oversized_raises():
    with pytest.raises(ValueError, match="P03"):
        plan([P("P01", 10, 10), P("P03", 190, 40)], BED)
    with pytest.raises(ValueError, match="P04"):
        plan([P("P04", 10, 10, 200)], BED)


def test_empty():
    assert plan([], BED) == []


def test_layout_no_overlap_and_margins():
    sp, mg = 6.0, 5.0
    pcs = [P(f"P{i}", 20 + (i * 13) % 60, 15 + (i * 29) % 70) for i in range(25)]
    size = {p["id"]: p["size"] for p in pcs}
    plates = layout(pcs, BED, sp, mg)
    assert sum(len(pl) for pl in plates) == 25
    for pl in plates:
        rects = []
        for r in pl:
            w, h = size[r["id"]][:2]
            if r["rotated"]:
                w, h = h, w
            x0, x1 = r["x"] - w / 2, r["x"] + w / 2
            y0, y1 = r["y"] - h / 2, r["y"] + h / 2
            assert x0 >= -90 + mg - 1e-6 and x1 <= 90 - mg + 1e-6
            assert y0 >= -90 + mg - 1e-6 and y1 <= 90 - mg + 1e-6
            rects.append((x0, x1, y0, y1))
        for i, a in enumerate(rects):
            for b in rects[i + 1 :]:
                gapx = max(b[0] - a[1], a[0] - b[1])
                gapy = max(b[2] - a[3], a[2] - b[3])
                assert max(gapx, gapy) >= sp - 1e-6

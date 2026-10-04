import json

import numpy as np
import pytest
from build123d import Box, Cylinder, Pos, export_stl

from fabricator import orient, render
from fabricator.design import Model
from fabricator.project import ProjectError


class Ctx:
    bed = (180, 180, 180)
    min_wall, min_feature, material = 0.8, 0.6, "PLA"


def png_ok(path):
    data = path.read_bytes()
    return data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 3000


def small_model():
    m = Model("Test")
    m.add(Box(40, 20, 10), "Base")
    m.add(Pos(0, 0, 10) * Cylinder(8, 20), "Post")
    m.reference(Pos(60, 0, 5) * Box(10, 10, 10), "Wall")
    return m


def test_build_pictures_makes_all_three(tmp_path):
    paths = render.build_pictures(small_model(), tmp_path / "pics", Ctx())
    assert [p.name for p in paths] == ["overview.png", "exploded.png", "print.png"]
    assert all(png_ok(p) for p in paths)


def test_single_part_has_no_exploded(tmp_path):
    m = Model("One")
    m.add(Box(10, 10, 10), "Cube")
    names = [p.name for p in render.build_pictures(m, tmp_path, Ctx())]
    assert names == ["overview.png", "print.png"]


def test_exploded_moves_printed_parts_apart():
    items = [render.item_from_mesh(i, __import__("trimesh").creation.box(extents=(10, 10, 10),
             transform=__import__("trimesh").transformations.translation_matrix((x, 0, 0))), "#ff0000")
             for i, x in (("A", -6), ("B", 6))]
    moved = render.exploded_items(items)
    assert moved[1].lo[0] - moved[0].hi[0] > items[1].lo[0] - items[0].hi[0] + 5


def test_pack_on_plates_stays_on_bed():
    import trimesh

    items = [render.item_from_mesh(f"P{i}", trimesh.creation.box(extents=(70, 70, 5)), "#00f") for i in range(6)]
    for it in items:
        it.tris = it.tris + np.array([0, 0, 2.5])
    moved, outlines = render.pack_on_plates(items, (180, 180, 180))
    assert len(outlines) == 2
    for it in moved:
        assert it.hi[1] <= 180 + 1e-6 and it.lo[1] >= -1e-6


class FakeProject:
    def __init__(self, path):
        self.path = path
        self.build_dir = path / "build"


def built_project(tmp_path):
    proj = FakeProject(tmp_path)
    model = small_model()
    (proj.build_dir / "parts").mkdir(parents=True)
    (proj.build_dir / "print").mkdir()
    info = []
    for p in model.parts:
        export_stl(p.shape, str(proj.build_dir / "parts" / f"{p.id}.stl"))
        if p.printed:
            c = orient.choose(p, Ctx.bed)
            export_stl(orient.place_on_bed(p.shape, c), str(proj.build_dir / "print" / f"{p.id}.stl"))
        info.append({"id": p.id, "name": p.name, "color": p.color, "printed": p.printed})
    (proj.build_dir / "model.json").write_text(json.dumps({"parts": info, "bed_mm": [180, 180, 180]}))
    return proj


def test_pictures_from_files(tmp_path):
    proj = built_project(tmp_path)
    out = render.pictures(proj, parts=["P01"], views=["front", "top", "print"])
    assert [p.name for p in out] == ["show-P01-front.png", "show-P01-top.png", "show-P01-print.png"]
    assert all(png_ok(p) for p in out)
    out = render.pictures(proj, parts=["Post"], section="x", transparent=True)
    assert png_ok(out[0]) and "section-x" in out[0].name
    out = render.pictures(proj, exploded=True, hide=["R03"], views=["plates"])
    assert png_ok(out[0])


def test_errors_are_plain(tmp_path):
    with pytest.raises(ProjectError, match="built"):
        render.pictures(FakeProject(tmp_path))
    proj = built_project(tmp_path)
    with pytest.raises(ProjectError, match="Nope"):
        render.pictures(proj, parts=["Nope"])

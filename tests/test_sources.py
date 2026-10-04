import pytest
import trimesh
from build123d import Box, export_step

from fabricator import sources
from fabricator.project import Project, ProjectError


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRICATOR_HOME", str(tmp_path / "home"))
    return Project.create("Test thing")


def _stl(tmp_path):
    p = tmp_path / "box.stl"
    trimesh.creation.box(extents=(10, 20, 30)).export(str(p))
    return p


def test_refuses_code_files(project, tmp_path):
    bad = tmp_path / "evil.py"
    bad.write_text("print('hi')")
    with pytest.raises(ProjectError, match="can't run code"):
        sources.import_file(project, bad)
    assert not list((project.path / "imports").iterdir())


def test_import_stl_and_load(project, tmp_path):
    rec = sources.import_file(project, _stl(tmp_path), name="my box", url="http://x", author="a", license="CC0")
    assert rec["name"] == "my_box" and rec["format"] == "stl"
    assert len(rec["sha256"]) == 64 and rec["bytes"] > 0
    assert sorted(rec["size_mm"]) == [10, 20, 30]
    assert (project.path / "imports" / "my_box.stl").exists()
    assert sources._read(project)["imports"][0]["sha256"] == rec["sha256"]
    bb = sources.load_import(project.path, "my_box").bounding_box()
    assert sorted(round(v) for v in (bb.size.X, bb.size.Y, bb.size.Z)) == [10, 20, 30]


def test_import_step(project, tmp_path):
    p = tmp_path / "b.step"
    export_step(Box(5, 6, 7), str(p))
    rec = sources.import_file(project, p)
    assert rec["format"] == "step" and sorted(rec["size_mm"]) == [5, 6, 7]
    bb = sources.load_import(project.path, "b").bounding_box()
    assert round(bb.size.Z) == 7


def test_garbage_shape_file_rejected(project, tmp_path):
    p = tmp_path / "junk.stl"
    p.write_text("not a shape")
    with pytest.raises(ProjectError):
        sources.import_file(project, p)


def test_load_missing(project):
    with pytest.raises(ProjectError, match="No imported file"):
        sources.load_import(project.path, "nope")


def test_source_warning(project):
    first = sources.add_source(project, "screw head diameter", 5.5, "mm", "http://m", "manufacturer")
    assert "warning" not in first
    second = sources.add_source(project, "screw head diameter", 5.0, "mm", "http://r", "retailer", "low")
    assert "warning" in second
    same = sources.add_source(project, "screw head diameter", 5.5, "mm", "http://r", "retailer")
    assert "warning" not in same
    assert len(sources._read(project)["sources"]) == 3

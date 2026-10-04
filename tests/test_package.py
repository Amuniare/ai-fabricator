"""The finished folder."""

import base64
import re
from pathlib import Path

import pytest
import trimesh

from fabricator import package
from fabricator.build import build
from fabricator.project import Project, ProjectError
from fabricator.settings import Settings

BAMBU = Path.home() / "tools/bambu/bambu-studio"
RESOURCES = Path.home() / "tools/bambu/squashfs-root/resources"

TWO = '''
from fabricator.design import *

def build(p, ctx):
    m = Model("Two Blocks")
    a = m.add(Pos(0, 0, 5) * Box(20, 20, 10), "Base plate")
    b = m.add(Pos(0, 0, 15) * Box(10, 10, 10), "Top block")
    m.join(a, b, "screw", hardware=["M3x12 screw"], note="Tighten gently.")
    return m
'''


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRICATOR_HOME", str(tmp_path / "home"))
    if BAMBU.exists():
        monkeypatch.setenv("FABRICATOR_BAMBU", str(BAMBU))
        monkeypatch.setenv("FABRICATOR_BAMBU_RESOURCES", str(RESOURCES))
    p = Project.create("Two Blocks")
    (p.path / "design.py").write_text(TWO, encoding="utf-8")
    build(p, save=False, pictures=False)
    return p


def check_folder(root: Path):
    assert root.name == "Two Blocks"
    for name in ("guide.html", "README.txt", "complete_model.step", "complete_model.glb"):
        assert (root / name).stat().st_size > 0
    assert (root / "Parts" / "P01_Base_plate.stl").exists() and (root / "Parts" / "P02_Top_block.step").exists()
    for name in ("project.yaml", "design.py", "sources.yaml"):
        assert (root / "Source" / name).exists()
    assert (root / "Source" / "imports").is_dir()
    assert len((root / "README.txt").read_text(encoding="utf-8").strip().splitlines()) == 5
    guide = (root / "guide.html").read_text(encoding="utf-8")
    for text in ("Two Blocks", "Base plate", "Top block", "M3x12 screw", "Join Base plate", "Tighten gently."):
        assert text in guide
    assert "http://" not in guide.replace("http://www.w3.org", "") and "src=\"http" not in guide
    for uri in re.findall(r'src="data:image/png;base64,([^"]+)"', guide):
        assert base64.b64decode(uri, validate=True)[:4] == b"\x89PNG"
    scene = trimesh.load(str(root / "complete_model.glb"))
    assert len(scene.geometry) == 2


def test_package_without_slicing(project):
    root = package.make(project, Settings(), slice_plates=False)
    check_folder(root)
    assert not (root / "Print files").exists()
    assert "not sliced" in (root / "guide.html").read_text(encoding="utf-8")
    # Running again replaces everything cleanly.
    (root / "stale.txt").write_text("x")
    root2 = package.make(project, Settings(), slice_plates=False)
    assert root2 == root and not (root / "stale.txt").exists()


def test_package_needs_build(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRICATOR_HOME", str(tmp_path / "home"))
    with pytest.raises(ProjectError):
        package.make(Project.create("Nothing"), Settings(), slice_plates=False)


def test_safe_names():
    assert package._safe('A/B: "C"?') == "AB C"
    assert package._safe("...") == "Project"


@pytest.mark.slow
@pytest.mark.skipif(not BAMBU.exists(), reason="Bambu Studio not available")
def test_package_with_print_files(project):
    root = package.make(project, Settings(), slice_plates=True)
    check_folder(root)
    plates = sorted((root / "Print files").glob("Plate_*.gcode.3mf"))
    assert plates and plates[0].stat().st_size > 1000
    assert re.search(r"\d+ min", (root / "guide.html").read_text(encoding="utf-8"))

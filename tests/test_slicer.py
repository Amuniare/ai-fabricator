"""Profiles, printer lookup and real slicing (slow tests need Bambu Studio)."""

import json
import os
from pathlib import Path

import pytest
import trimesh

from fabricator import slicer
from fabricator.project import Project, ProjectError
from fabricator.settings import Settings
from fabricator.slicer import bambu, profiles

BAMBU = Path.home() / "tools/bambu/bambu-studio"
RESOURCES = Path.home() / "tools/bambu/squashfs-root/resources"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRICATOR_HOME", str(tmp_path / "home"))
    if "FABRICATOR_BAMBU" not in os.environ and BAMBU.exists():
        monkeypatch.setenv("FABRICATOR_BAMBU", str(BAMBU))
        monkeypatch.setenv("FABRICATOR_BAMBU_RESOURCES", str(RESOURCES))


needs_slicer = pytest.mark.skipif(slicer.find_slicer(Settings()) is None and not BAMBU.exists(),
                                  reason="Bambu Studio not available")


def cube_stl(path: Path, size=20.0):
    m = trimesh.creation.box((size, size, size))
    m.apply_translation((0, 0, size / 2))
    m.export(str(path))
    return path


@needs_slicer
def test_find_slicer():
    info = slicer.find_slicer(Settings())
    assert info["kind"] == "bambu" and Path(info["exe"]).exists() and Path(info["resources"]).is_dir()


@needs_slicer
def test_merge_gives_a1_mini_bed_and_density():
    s = Settings()
    assert slicer.printer_bed(s) == (180.0, 180.0, 180.0)
    p = slicer.project_profiles(s)
    assert p["density"] > 0 and p["diameter"] == 1.75
    assert "inherits" not in p["process"] and p["process"]["from"] == "system"
    assert p["process"]["wall_loops"] == "2"  # came from a parent profile
    assert "A1M" in p["filament_name"]


@needs_slicer
def test_print_options_change_process():
    s = Settings()
    strong = slicer.project_profiles(s, {"strength": "strong"})["process"]
    assert strong["wall_loops"] == "3" and strong["sparse_infill_density"] == "25%"
    on = slicer.project_profiles(s, {"supports": "on", "layer": 0.28})["process"]
    assert on["enable_support"] == "1" and on["layer_height"] == "0.28"
    assert slicer.project_profiles(s)["process"]["enable_support"] == "0"
    with pytest.raises(ProjectError):
        slicer.project_profiles(s, {"strength": "huge"})


@needs_slicer
def test_materials_and_unsupported_material():
    s = Settings(material="PETG")
    assert "PETG" in slicer.project_profiles(s)["filament_name"]
    with pytest.raises(ProjectError, match="ABS"):
        slicer.project_profiles(Settings(material="ABS"))  # A1 mini has no ABS profile


@needs_slicer
def test_printer_matching():
    s = Settings()
    assert "Bambu Lab A1 mini" in slicer.list_printers(s)
    assert slicer.match_printer("a1 mini", s) == "Bambu Lab A1 mini"
    assert slicer.match_printer("Bambu Lab P1S", s) == "Bambu Lab P1S"
    with pytest.raises(ProjectError, match="P1P"):
        slicer.match_printer("p1", s)


def test_bed_falls_back_without_slicer(monkeypatch):
    monkeypatch.delenv("FABRICATOR_BAMBU", raising=False)
    monkeypatch.setattr(profiles, "find_slicer", lambda settings=None: None)
    assert slicer.printer_bed(Settings()) == (180.0, 180.0, 180.0)
    assert "Bambu Lab A1 mini" in slicer.list_printers(Settings())


def test_missing_slicer_message(monkeypatch):
    monkeypatch.setattr(profiles, "find_slicer", lambda settings=None: None)
    with pytest.raises(ProjectError, match="Install"):
        profiles.require_slicer(Settings())


def test_slice_needs_build():
    p = Project.create("Nothing")
    with pytest.raises(ProjectError, match="Build it first"):
        slicer.slice_project(p, Settings())


def test_support_fraction_parser(tmp_path):
    g = tmp_path / "x.gcode"
    g.write_text("M83\n; FEATURE: Outer wall\nG1 X1 Y1 E3\n; FEATURE: Support\nG1 X2 Y2 E1\n"
                 "G1 E-1\nG1 X3 Y3 E-0.5\n; FEATURE: Support interface\nG1 X2 Y2 E1\n")
    assert bambu.gcode_support_fraction(g) == pytest.approx(0.4)


@pytest.mark.slow
@needs_slicer
def test_cube_time_and_weight(tmp_path):
    r = slicer.slice_files(Settings(), [cube_stl(tmp_path / "c.stl")], tmp_path / "c.gcode.3mf")
    assert 10 <= r["minutes"] <= 15
    assert 3.2 <= r["grams"] <= 4.3
    assert r["support_grams"] == 0
    assert Path(r["file"]).stat().st_size > 1000


@pytest.mark.slow
@needs_slicer
def test_overhang_gets_supports(tmp_path):
    a = trimesh.creation.box((10, 10, 30)); a.apply_translation((0, 0, 15))
    b = trimesh.creation.box((40, 10, 5)); b.apply_translation((15, 0, 27.5))
    tee = trimesh.boolean.union([a, b]); tee.export(str(tmp_path / "t.stl"))
    plain = slicer.slice_files(Settings(), [tmp_path / "t.stl"], supports=False)
    sup = slicer.slice_files(Settings(), [tmp_path / "t.stl"], supports=True)
    assert sup["support_grams"] > 0.3
    assert sup["minutes"] > plain["minutes"]


@pytest.mark.slow
@needs_slicer
def test_slice_project_end_to_end():
    from fabricator.build import build

    p = Project.create("Cube", parameters={"width": 20, "depth": 20, "height": 20, "corner_radius": 0.5})
    build(p, save=False, pictures=False)
    r = slicer.slice_project(p, Settings())
    assert r["slicer"] == "Bambu Studio" and len(r["plates"]) == 1
    plate = r["plates"][0]
    assert plate["parts"] == ["P01"] and Path(plate["file"]).name == "plate_1.gcode.3mf"
    assert 10 <= plate["minutes"] <= 15 and plate["grams"] > 3
    assert r["total_grams"] == pytest.approx(plate["grams"])
    assert (p.slices_dir / "plate_1.gcode.3mf").exists()

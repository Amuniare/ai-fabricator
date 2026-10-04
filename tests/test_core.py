"""Projects, versions, settings and the design runner."""

import pytest

from fabricator.build import DesignError, run_design
from fabricator.project import Project, ProjectError
from fabricator.settings import Settings


@pytest.fixture(autouse=True)
def fab_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRICATOR_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def test_new_project_has_source_files():
    p = Project.create("Garage Remote Bracket", "holds the remote by the door")
    assert p.path.name == "garage-remote-bracket"
    for name in ("project.yaml", "design.py", "sources.yaml", ".gitignore"):
        assert (p.path / name).exists()
    assert p.data["request"] == "holds the remote by the door"


def test_find_by_name_slug_and_part():
    Project.create("Garage Remote Bracket")
    Project.create("Drill Holder")
    assert Project.find("drill").name == "Drill Holder"
    assert Project.find("Garage Remote Bracket").path.name == "garage-remote-bracket"
    with pytest.raises(ProjectError):
        Project.find("nothing like this")


def test_versions_save_and_restore():
    p = Project.create("Box")
    v1 = p.save_version("first")
    assert v1 is not None
    assert v1["number"] == 1
    assert p.save_version("no change") is None
    p.update(parameters={"width": 99})
    v2 = p.save_version("wider")
    assert v2 is not None
    assert v2["number"] == 2
    p.restore_version(1)
    assert p.data["parameters"]["width"] == 60.0
    assert [v["label"] for v in p.versions()] == ["first", "wider", "back to version 1"]


def test_fits_use_material_and_learned_overrides():
    s = Settings(material="PETG")
    assert s.fit("snug") == pytest.approx(0.15)
    s.fit_overrides[s.machine_key] = {"snug": 0.2}
    assert s.fit("snug") == pytest.approx(0.2)
    with pytest.raises(KeyError):
        s.fit("wobbly")


def test_settings_round_trip(fab_home):
    s = Settings(printer="Bambu Lab A1 mini", material="PLA", configured=True)
    s.save()
    assert Settings.load().configured is True
    assert (fab_home / "settings.yaml").exists()


def test_design_error_reports_the_line():
    p = Project.create("Broken")
    (p.path / "design.py").write_text(
        "from fabricator.design import *\n\ndef build(p, ctx):\n    return p.not_a_parameter\n",
        encoding="utf-8",
    )
    with pytest.raises(DesignError) as e:
        run_design(p, (180, 180, 180))
    assert e.value.line == 4
    assert "not_a_parameter" in str(e.value)


def test_runner_loads_parts_and_joints():
    p = Project.create("Two Blocks")
    (p.path / "design.py").write_text(
        "from fabricator.design import *\n\n"
        "def build(p, ctx):\n"
        "    m = Model('Two')\n"
        "    a = m.add(Box(10, 10, 10), 'Left')\n"
        "    b = m.add(Pos(20, 0, 0) * Box(10, 10, 10), 'Right')\n"
        "    m.join(a, b, 'glue')\n"
        "    m.hardware('M3x10 screw', 2)\n"
        "    return m\n",
        encoding="utf-8",
    )
    model = run_design(p, (180, 180, 180))
    assert [x.id for x in model.parts] == ["P01", "P02"]
    assert pytest.approx(15) == model.parts[1].shape.bounding_box().min.X
    assert model.joints[0].kind == "glue"
    assert model.extra_hardware == ["M3x10 screw", "M3x10 screw"]

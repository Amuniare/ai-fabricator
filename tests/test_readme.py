"""Notes files and each project's README page."""

import json

import pytest

from fabricator import readme
from fabricator.build import build
from fabricator.project import Project
from fabricator.settings import USER_NOTES_FILE, Settings, home


@pytest.fixture(autouse=True)
def fab_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRICATOR_HOME", str(tmp_path / "home"))
    Settings(configured=True).save()


def test_projects_folder_has_user_notes_that_are_never_overwritten():
    notes = home() / USER_NOTES_FILE
    assert notes.exists()
    notes.write_text("I have M3 screws.", encoding="utf-8")
    home()
    assert notes.read_text(encoding="utf-8") == "I have M3 screws."


def test_new_project_has_notes_saved_with_versions():
    project = Project.create("Hook", "a coat hook")
    assert (project.path / "notes.md").read_text(encoding="utf-8").startswith("# Notes: Hook")
    assert "README.md" in (project.path / ".gitignore").read_text(encoding="utf-8")


def test_readme_after_build_and_slice():
    project = Project.create("Remote bracket", example="remote-bracket")
    build(project, note="first version", pictures=False)
    page = (project.path / "README.md").read_text(encoding="utf-8")
    assert "# Remote bracket" in page
    assert "all checks passed" in page
    assert "| P01 |" in page
    assert "1. first version" in page
    assert "## Printing" not in page

    sliced = {
        "plates": [{"number": 1, "parts": ["P01"], "file": "plate_1.gcode.3mf", "minutes": 66, "grams": 26}],
        "total_minutes": 66,
        "total_grams": 26,
    }
    project.slices_dir.mkdir()
    (project.slices_dir / readme.SLICE_SUMMARY).write_text(json.dumps(sliced), encoding="utf-8")
    page = readme.write(project).read_text(encoding="utf-8")
    assert "Plate 1 (P01): 1 h 06 min, 26 g" in page
    assert "before the last build" not in page

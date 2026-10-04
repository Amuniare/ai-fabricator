"""The plan's acceptance tests, run on the example projects.

Every example must build with no failed checks and no supports. Passing means all checks
and (for the slow tests) slicing succeed, without printing anything.
"""

import os

import pytest

from fabricator.build import build
from fabricator.project import Project
from fabricator.settings import Settings

EXAMPLES = ["remote-bracket", "battery-holder", "shelf", "drill-bit-box"]


@pytest.fixture(autouse=True)
def fab_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRICATOR_HOME", str(tmp_path / "home"))
    Settings(configured=True).save()


@pytest.mark.parametrize("name", EXAMPLES)
def test_example_builds_clean(name):
    project = Project.create(name, example=name)
    result = build(project, note="first version", pictures=False)
    problems = [c.message for c in result.checks if c.status != "pass"]
    assert result.status == "pass", problems
    assert result.version and result.version["number"] == 1
    for part in result.summary["parts"]:
        if part["printed"]:
            assert not part["orientation"]["support_needed"], f"{part['id']} would need supports"


def test_too_big_splits_into_pegged_pieces():
    result = build(Project.create("Shelf", example="shelf"), pictures=False)
    printed = [p for p in result.summary["parts"] if p["printed"]]
    assert [p["id"] for p in printed] == ["P01", "P02", "P03", "P04"]
    assert all(max(p["print_size_mm"][:2]) <= 180 for p in printed)
    assert {j["kind"] for j in result.summary["joints"]} == {"peg"}
    assert any("split into 4 pieces" in m for m in result.messages)


def test_separate_parts_fit_with_a_sliding_gap():
    result = build(Project.create("Box", example="drill-bit-box"), pictures=False)
    joint = result.summary["joints"][0]
    assert joint["kind"] == "slide" and joint["gap"] == pytest.approx(0.20)
    overlap = [c for c in result.checks if "overlap" in c.name]
    assert overlap and all(c.status == "pass" for c in overlap)


def test_change_reports_only_affected_pieces():
    project = Project.create("Shelf", example="shelf")
    build(project, pictures=False)
    data = project.data
    data["parameters"]["corner_radius"] = 10.0  # only the two end pieces have front corners
    project.save(data)
    result = build(project, note="rounder corners", pictures=False)
    note = next(m for m in result.messages if m.startswith("Changed since"))
    assert "P01, P04" in note and "Unchanged: P02, P03" in note


def test_coming_back_later_edits_the_same_design():
    project = Project.create("Remote bracket", example="remote-bracket")
    build(project, note="first version", pictures=False)
    found = Project.find("remote")
    data = found.data
    data["parameters"]["plate_width"] += 10
    found.save(data)
    result = build(found, note="10 mm wider", pictures=False)
    assert result.summary["parts"][0]["size_mm"][0] == pytest.approx(74.0)
    assert [v["label"] for v in found.versions()] == ["first version", "10 mm wider"]


@pytest.mark.slow
@pytest.mark.skipif(not os.environ.get("FABRICATOR_BAMBU") and os.name != "nt",
                    reason="needs Bambu Studio (set FABRICATOR_BAMBU)")
def test_example_slices_with_real_time_and_grams():
    from fabricator import slicer

    project = Project.create("Remote bracket", example="remote-bracket")
    build(project, pictures=False)
    result = slicer.slice_project(project, Settings.load())
    plate = result["plates"][0]
    assert 20 < plate["minutes"] < 240
    assert 10 < plate["grams"] < 80
    assert not plate.get("support_grams")

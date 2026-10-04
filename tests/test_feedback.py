import json

import pytest

from fabricator import feedback
from fabricator.project import Project, ProjectError
from fabricator.settings import Settings, home


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRICATOR_HOME", str(tmp_path / "home"))
    return Project.create("Test thing"), Settings()


def _model(project, joints):
    project.build_dir.mkdir(exist_ok=True)
    (project.build_dir / "model.json").write_text(json.dumps({"joints": joints}))


def test_too_tight_moves_snug(env):
    project, s = env
    r = feedback.record(project, s, fit="snug", result="too-tight")
    assert r["new_gap"] == 0.15 and r["old_gap"] == 0.1
    assert "0.15 mm gap instead of 0.1 mm" in r["message"]
    assert Settings.load().fit_overrides[s.machine_key]["snug"] == 0.15
    assert Settings.load().fit("snug") == 0.15
    assert project.data["feedback"][0]["new_gap"] == 0.15
    assert (home() / "feedback-log.yaml").exists()


def test_too_loose_floor_zero(env):
    project, s = env
    for _ in range(3):
        feedback.record(project, s, fit="press", result="too-loose")
    assert Settings.load().fit("press") == 0.0


def test_infer_fit(env):
    project, s = env
    _model(project, [{"a": "Top", "b": "Base", "kind": "peg", "fit": "sliding"}])
    r = feedback.record(project, s, part="Top", result="too-loose")
    assert r["fit"] == "sliding" and r["new_gap"] == 0.15


def test_ambiguous_fit(env):
    project, s = env
    _model(project, [{"a": "Top", "b": "Base", "fit": "sliding"}, {"a": "Top", "b": "Lid", "fit": "snug"}])
    with pytest.raises(ProjectError, match="sliding, snug"):
        feedback.record(project, s, part="Top", result="too-tight")


def test_good_no_change(env):
    project, s = env
    feedback.record(project, s, fit="snug", result="good")
    assert Settings.load().fit_overrides == {}
    assert project.data["feedback"][0]["result"] == "good"


def test_note_only(env):
    project, s = env
    r = feedback.record(project, s, part="part 3", note="cracked when tightening the screw")
    assert r["suggestions"] and "message" in r
    assert project.data["feedback"][0]["note"].startswith("cracked")

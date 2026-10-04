"""Comparing options by slicing each one."""

from pathlib import Path

import pytest

from fabricator import compare
from fabricator.build import build
from fabricator.project import Project, ProjectError
from fabricator.settings import Settings

BAMBU = Path.home() / "tools/bambu/bambu-studio"
RESOURCES = Path.home() / "tools/bambu/squashfs-root/resources"

TEE = '''
from fabricator.design import *

def build(p, ctx):
    m = Model("Tee")
    stem = Pos(0, 0, 15) * Box(10, 10, 30)
    top = Pos(15, 0, 27.5) * Box(40, 10, 5)
    m.add(stem + top, "Tee")
    return m
'''


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRICATOR_HOME", str(tmp_path / "home"))
    if BAMBU.exists():
        monkeypatch.setenv("FABRICATOR_BAMBU", str(BAMBU))
        monkeypatch.setenv("FABRICATOR_BAMBU_RESOURCES", str(RESOURCES))


def opt(label, minutes, grams=5.0, support=0.0, needed=None):
    return {"label": label, "minutes": minutes, "grams": grams, "support_grams": support,
            "support_needed": support > 0 if needed is None else needed}


def test_recommend_prefers_no_support_within_15_percent():
    best, reason = compare.recommend([opt("Lying down", 110), opt("Standing", 100, support=2)])
    assert best["label"] == "Lying down" and "no supports" in reason


def test_recommend_fastest_when_clean_option_too_slow():
    best, reason = compare.recommend([opt("Lying down", 140), opt("Standing", 100, support=2)])
    assert best["label"] == "Standing" and reason.endswith(".")


def test_recommend_tie_goes_to_fewer_grams():
    best, _ = compare.recommend([opt("A", 60, grams=9), opt("B", 60, grams=7)])
    assert best["label"] == "B"


def test_needs_a_build_and_a_choice():
    p = Project.create("Tee")
    with pytest.raises(ProjectError, match="Build it first"):
        compare.compare(p, Settings())
    with pytest.raises(ProjectError):
        compare.compare(p, Settings(), what="nonsense")


@pytest.mark.slow
@pytest.mark.skipif(not BAMBU.exists(), reason="Bambu Studio not available")
def test_orientation_compare_slices_three_options():
    p = Project.create("Tee")
    (p.path / "design.py").write_text(TEE, encoding="utf-8")
    build(p, save=False, pictures=False)
    r = compare.compare(p, Settings(), what="orientation")
    assert len(r["options"]) == 3
    assert all(o["minutes"] > 0 and o["grams"] > 0 for o in r["options"])
    assert r["recommended"] in [o["label"] for o in r["options"]]
    assert r["reason"].endswith(".") and r["recommended"] in r["reason"]


@pytest.mark.slow
@pytest.mark.skipif(not BAMBU.exists(), reason="Bambu Studio not available")
def test_split_compare_reports_pieces():
    p = Project.create("Tee")
    (p.path / "design.py").write_text(TEE, encoding="utf-8")
    build(p, save=False, pictures=False)
    r = compare.compare(p, Settings(), what="split")
    assert r["options"] and all(o["pieces"] >= 1 and o["minutes"] > 0 for o in r["options"])
    assert r["recommended"] and r["reason"]
    assert not (p.path / "build" / "viewer.html").exists()  # the original project was untouched by the trials

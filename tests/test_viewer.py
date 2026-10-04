"""The browser 3D view: one HTML file with the model inside."""

import base64
import json
import re
import struct

import pytest

from fabricator import viewer
from fabricator.build import build
from fabricator.project import Project, ProjectError

TWO = """
from fabricator.design import *

def build(p, ctx):
    m = Model("Two Blocks")
    a = m.add(Pos(0, 0, 5) * Box(20, 20, 10), "Base <plate>")
    b = m.add(Pos(0, 0, 15) * Box(10, 10, 10), "Top block")
    m.reference(Pos(40, 0, 5) * Box(5, 5, 10), "Wall")
    m.join(a, b, "peg")
    return m
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRICATOR_HOME", str(tmp_path / "home"))
    p = Project.create("Two Blocks")
    (p.path / "design.py").write_text(TWO, encoding="utf-8")
    build(p, save=False, pictures=False)
    return p


def test_needs_a_build(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRICATOR_HOME", str(tmp_path / "home"))
    with pytest.raises(ProjectError, match="built"):
        viewer.make(Project.create("Empty"), open_browser=False)


def test_viewer_page_contents(project, monkeypatch):
    opened = []
    monkeypatch.setattr(viewer.webbrowser, "open", opened.append)
    path = viewer.make(project, open_browser=False)
    assert path == project.build_dir / "viewer.html" and not opened
    page = path.read_text(encoding="utf-8")
    m_parts = re.search(r'id="parts-data" type="application/json">(.*?)</script>', page, re.S)
    assert m_parts is not None
    parts = json.loads(m_parts.group(1))
    assert [p["id"] for p in parts] == ["P01", "P02", "R03"]
    assert "Base <plate>" in [p["name"] for p in parts]
    assert "Base <plate>" not in page.split('id="parts-data"')[0]  # never raw into the HTML
    for word in ("Top block", "Wall", "Only this", "Pull apart", "See-through", "Cut through", "Reset view"):
        assert word in page
    assert "cdn.jsdelivr.net/npm/three@" in page and "importmap" in page
    m_model = re.search(r'id="model-data" type="text/plain">(.*?)</script>', page, re.S)
    assert m_model is not None
    b64 = m_model.group(1).strip()
    glb = base64.b64decode(b64, validate=True)
    magic, version, length = struct.unpack("<4sII", glb[:12])
    assert magic == b"glTF" and version == 2 and length == len(glb)
    json_len = struct.unpack("<I", glb[12:16])[0]
    gltf = json.loads(glb[20 : 20 + json_len])
    assert {n["name"] for n in gltf["nodes"]} >= {"P01", "P02", "R03"}


def test_make_opens_browser(project, monkeypatch):
    opened = []
    monkeypatch.setattr(viewer.webbrowser, "open", opened.append)
    viewer.make(project, open_browser=True)
    assert opened and opened[0].startswith("file://") and opened[0].endswith("viewer.html")

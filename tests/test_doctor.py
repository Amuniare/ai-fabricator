from fabricator import doctor

NAMES = ["Python", "CAD engine", "Mesh tools", "Git", "Bambu Studio", "Printer profile",
         "Settings saved", "Projects folder", "OrcaSlicer", "Claude Code in VS Code", "Internet"]


def _clean(monkeypatch, tmp_path):
    monkeypatch.setenv("FABRICATOR_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(doctor, "_internet", lambda: ("optional", "skipped"))


def test_run_never_raises_without_slicer(tmp_path, monkeypatch):
    _clean(monkeypatch, tmp_path)
    monkeypatch.delenv("FABRICATOR_BAMBU", raising=False)
    monkeypatch.delenv("FABRICATOR_BAMBU_RESOURCES", raising=False)
    monkeypatch.setattr(doctor, "_slicer_info", lambda s: None)
    results = doctor.run()
    assert [r[0] for r in results] == NAMES
    assert all(r[1] in ("ok", "missing", "optional", "warn") for r in results)
    by = {r[0]: r for r in results}
    assert by["Bambu Studio"][1] == "missing" and "bambulab.com" in by["Bambu Studio"][2]
    assert by["Settings saved"][1] == "warn"
    assert by["CAD engine"][1] == "ok"


def test_failing_check_is_reported(tmp_path, monkeypatch):
    _clean(monkeypatch, tmp_path)
    def boom():
        raise RuntimeError("x")
    assert doctor._check("T", boom)[1] == "warn"


def test_bambu_ok_with_env(tmp_path, monkeypatch):
    _clean(monkeypatch, tmp_path)
    exe = tmp_path / "bambu-studio"
    exe.write_text("")
    monkeypatch.setenv("FABRICATOR_BAMBU", str(exe))
    monkeypatch.setattr(doctor, "_slicer_info", lambda s: None)
    by = {r[0]: r for r in doctor.run()}
    assert by["Bambu Studio"][1] == "ok"


def test_update_zip_message(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRICATOR_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(doctor, "TOOL_DIR", tmp_path)
    assert "ZIP" in doctor.update() and "safe" in doctor.update()

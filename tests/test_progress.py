import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import progress as p  # noqa: E402


def test_pull_lines_sum_layers_and_report_success(tmp_path, monkeypatch):
    monkeypatch.setenv("SERVICE_WEIGHTS_MARKER", str(tmp_path / "marker"))
    lines = [json.dumps({"status": "pulling a", "digest": "a", "total": 100, "completed": 40}),
             json.dumps({"status": "pulling b", "digest": "b", "total": 300, "completed": 60}), "",
             json.dumps({"status": "success"})]
    seen = []
    real = p.progress.set
    monkeypatch.setattr(p.progress, "set", lambda done, total=None: (seen.append((done, total)), real(done, total)))
    assert p.ollama_pull_progress(lines, "m") == "success"
    assert seen[-1] == (100, 400)
    assert p.progress.snapshot()["active"] is False


def test_tracking_reports_growth_of_the_folder_then_goes_idle(tmp_path, monkeypatch):
    monkeypatch.setenv("SERVICE_WEIGHTS_MARKER", str(tmp_path / "marker"))
    folder = tmp_path / "blobs"
    folder.mkdir()
    (folder / "old").write_bytes(b"x" * 10)
    with p.tracking("repo", 5, folder):
        (folder / "new").write_bytes(b"y" * 5)
        assert p.progress.snapshot()["active"] and p.progress.snapshot()["total"] == 5
    after = p.progress.snapshot()
    assert not after["active"] and after["done"] == 5

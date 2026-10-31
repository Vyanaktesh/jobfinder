import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import control_server as cs  # noqa: E402


def test_percent_complete():
    assert cs.percent_complete(None) == 0
    p = {"stage": "scraping", "platforms": {"a": {"done": 50, "total": 100}, "b": {"done": 0, "total": 100}}}
    assert cs.percent_complete(p) == 22            # 25% of the 90% scraping share
    assert cs.percent_complete({"stage": "filtering", "platforms": {}}) == 90
    assert cs.percent_complete({"stage": "done"}) == 100


def test_run_command_modes(monkeypatch):
    monkeypatch.delenv("JOBSCRAPER_RUN_CMD", raising=False)
    assert cs.run_command("quick")[-1] == "--sponsors-only"
    assert cs.run_command("recheck")[-1] == "--full"
    assert "--full" not in cs.run_command("full") and "--sponsors-only" not in cs.run_command("full")


def test_unknown_mode_rejected():
    ok, _ = cs.start_run("bogus")
    assert not ok


def test_dashboard_staleness(tmp_path, monkeypatch):
    import os
    import time
    (tmp_path / "templates").mkdir()
    (tmp_path / "static").mkdir()
    (tmp_path / "output").mkdir()
    tpl = tmp_path / "templates" / "dashboard.html"
    tpl.write_text("t")
    monkeypatch.setattr(cs, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(cs, "OUTPUT_DIR", tmp_path / "output")
    assert cs.dashboard_is_stale()                       # no page yet
    page = tmp_path / "output" / "latest.html"
    page.write_text("p")
    now = time.time()
    os.utime(tpl, (now - 100, now - 100))
    os.utime(page, (now, now))
    assert not cs.dashboard_is_stale()                   # page newer than the code
    os.utime(tpl, (now + 100, now + 100))
    assert cs.dashboard_is_stale()                       # code newer than the page

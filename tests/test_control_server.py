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

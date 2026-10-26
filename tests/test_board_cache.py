import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.main as m  # noqa: E402
from src.progress import ProgressTracker  # noqa: E402
from src.scrapers.board_cache import BoardCache, board_key  # noqa: E402


def test_board_key_variants():
    assert board_key({"token": "Stripe"}) == "stripe"
    assert board_key({"tenant": "adobe", "site": "External", "wd": "wd5"}) == "adobe/external"
    assert board_key({"board": "ramp"}) == "ramp"


def test_skips_only_after_consecutive_empty_runs(tmp_path):
    c = BoardCache(tmp_path / "c.json", min_empty_runs=2)
    co = {"token": "dead"}
    assert not c.should_skip("greenhouse", co)
    c.record("greenhouse", co, 0)
    assert not c.should_skip("greenhouse", co)       # one empty check is not enough
    c.record("greenhouse", co, 0)
    assert c.should_skip("greenhouse", co)
    c.record("greenhouse", co, 5)                    # came back to life
    assert not c.should_skip("greenhouse", co)


def test_cache_persists_and_can_be_disabled(tmp_path):
    p = tmp_path / "c.json"
    c = BoardCache(p, min_empty_runs=1)
    c.record("lever", {"site": "x"}, 0)
    c.save()
    assert BoardCache(p, min_empty_runs=1).should_skip("lever", {"site": "x"})
    assert not BoardCache(p, min_empty_runs=1, enabled=False).should_skip("lever", {"site": "x"})


def test_scrape_platform_skips_cached_boards_and_reports_progress(tmp_path):
    calls = []

    class Fake:
        def __init__(self, delay=0):
            self._client = None

        async def fetch_jobs(self, company):
            calls.append(company["token"])
            return []

        async def close(self):
            pass

    m.SCRAPERS["greenhouse"] = Fake
    cache = BoardCache(tmp_path / "c.json", min_empty_runs=2)
    prog = ProgressTracker(tmp_path / "p.json")
    cos = [{"token": "a"}, {"token": "b"}]
    for _ in range(2):  # two empty runs
        asyncio.run(m.scrape_platform("greenhouse", cos, 0, 5, cache, prog))
    calls.clear()
    asyncio.run(m.scrape_platform("greenhouse", cos, 0, 5, cache, prog))
    assert calls == []                                # both skipped on the third run
    assert prog.state["platforms"]["greenhouse"]["skipped"] == 2

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest  # noqa: E402

from src.db import JobDB, clean_application  # noqa: E402


@pytest.fixture
def db(tmp_path):
    d = JobDB(tmp_path / "t.db")
    yield d
    d.close()


def rec(**kw):
    base = {"job_key": "abc123", "status": "applied", "updated_at": "2026-10-01T10:00:00", "company": "Acme", "title": "Analyst"}
    base.update(kw)
    return base


def test_clean_rejects_bad_input():
    assert clean_application({"job_key": "", "status": "applied"}) is None
    assert clean_application({"job_key": "k", "status": "hired!"}) is None
    assert clean_application("nope") is None
    assert clean_application({"job_key": "k" * 101, "status": "applied"}) is None


def test_clean_truncates_long_text_and_bad_counts():
    r = clean_application(rec(notes="x" * 9000, h1b_count="abc"))
    assert len(r["notes"]) == 5000 and r["h1b_count"] is None


def test_upsert_and_read_back(db):
    assert db.upsert_applications([rec()]) == 1
    got = db.get_applications()
    assert got[0]["job_key"] == "abc123" and got[0]["company"] == "Acme"


def test_newest_update_wins(db):
    db.upsert_applications([rec(status="interviewing", updated_at="2026-10-05T10:00:00")])
    assert db.upsert_applications([rec(status="applied", updated_at="2026-10-01T10:00:00")]) == 0  # older ignored
    assert db.get_applications()[0]["status"] == "interviewing"
    assert db.upsert_applications([rec(status="offer", updated_at="2026-10-09T10:00:00")]) == 1
    assert db.get_applications()[0]["status"] == "offer"


def test_invalid_records_are_skipped_not_fatal(db):
    assert db.upsert_applications([{"nope": 1}, rec()]) == 1


def test_applications_survive_job_changes(db):
    # tracker rows are independent of the jobs table (jobs expire; applications must not)
    db.upsert_applications([rec()])
    db.conn.execute("DELETE FROM jobs")
    db.conn.commit()
    assert len(db.get_applications()) == 1


def test_server_endpoints(tmp_path, monkeypatch):
    import json
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    import control_server as cs

    monkeypatch.setattr(cs, "DB_FILE", tmp_path / "t.db")
    server = ThreadingHTTPServer(("127.0.0.1", 0), cs.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        body = json.dumps({"applications": [rec(), {"job_key": "bad", "status": "zzz"}]}).encode()
        req = urllib.request.Request(base + "/applications", data=body, headers={"Content-Type": "application/json"})
        assert json.load(urllib.request.urlopen(req))["saved"] == 1
        got = json.load(urllib.request.urlopen(base + "/applications"))["applications"]
        assert [a["job_key"] for a in got] == ["abc123"]
        bad = urllib.request.Request(base + "/applications", data=b'{"applications": "x"}')
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(bad)
    finally:
        server.shutdown()

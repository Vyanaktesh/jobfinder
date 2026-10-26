import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.output.csv_export import export_csv  # noqa: E402
from src.output.dashboard import render_dashboard  # noqa: E402
from src.output.digest import render_digest  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
JOB = {"id": 1, "company_name": "Acme", "title": "Analyst", "apply_url": "https://x", "source_platform": "greenhouse",
       "matched_lane": "Data", "sponsorship_flag": "GREEN", "h1b_count": 12, "location_parsed": "NY"}
STATS = {"total_passed": 1, "total_companies": 1, "total_new": 0}


def test_repeated_runs_overwrite_a_single_set_of_files(tmp_path):
    for _ in range(3):
        render_dashboard([JOB], STATS, tmp_path, ROOT / "templates")
        render_digest([JOB], STATS, tmp_path)
        export_csv([JOB], tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["latest.csv", "latest.html", "latest.md"]


def test_old_timestamped_files_are_cleaned_up(tmp_path):
    for name in ("dashboard_20260101_1200.html", "digest_20260101_1200.md", "jobs_20260101_1200.csv", "feedback.json"):
        (tmp_path / name).write_text("old")
    render_dashboard([JOB], STATS, tmp_path, ROOT / "templates")
    names = {p.name for p in tmp_path.iterdir()}
    assert names == {"latest.html", "feedback.json"}   # only stale generated files are removed

#!/usr/bin/env python3
"""Rebuild the dashboard, digest and CSV from the existing database — no scraping.

  python scripts/render_only.py

Use this if a run finished scraping but crashed while writing output, or to
refresh output/latest.html after changing templates/ or static/.
"""
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.db import JobDB  # noqa: E402
from src.main import sort_for_output  # noqa: E402
from src.output.csv_export import export_csv  # noqa: E402
from src.output.dashboard import render_dashboard  # noqa: E402
from src.output.digest import render_digest  # noqa: E402


def main():
    with open(ROOT / "config" / "settings.toml", "rb") as f:
        settings = tomllib.load(f)
    db = JobDB(ROOT / settings["output"]["db_path"])
    jobs = sort_for_output(db.get_all_active_jobs())
    db.close()

    output_dir = ROOT / settings["output"]["dashboard_dir"]
    stats = {
        "total_fetched": len(jobs),
        "total_passed": len(jobs),
        "total_new": 0,
        "total_companies": len({j["company_name"] for j in jobs}),
    }
    print(f"Dashboard: {render_dashboard(jobs, stats, output_dir, ROOT / 'templates')}")
    print(f"Digest:    {render_digest(jobs, stats, output_dir)}")
    print(f"CSV:       {export_csv(jobs, output_dir)}")
    print(f"\n{len(jobs)} jobs. Open {output_dir / 'latest.html'}")


if __name__ == "__main__":
    main()

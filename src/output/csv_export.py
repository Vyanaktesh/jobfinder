"""CSV export of evaluated jobs."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

COLUMNS = [
    "company_name", "title", "location_parsed", "matched_lane", "posted_at",
    "sponsorship_flag", "h1b_count", "match_score", "apply_url",
]


def export_csv(jobs: list[dict], output_dir: Path) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"jobs_{datetime.now():%Y%m%d_%H%M}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(jobs)
    return path

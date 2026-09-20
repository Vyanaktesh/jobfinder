"""CSV export for filtered/evaluated jobs."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path


def export_csv(jobs: list[dict], output_dir: Path) -> Path:
    """Export jobs to a CSV file in output_dir.

    Args:
        jobs: List of job dicts (from get_all_evaluated_jobs).
        output_dir: Directory to write the CSV into.

    Returns:
        Path to the written CSV file.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_path = output_dir / "jobs.csv"

    columns = [
        "company_name",
        "title",
        "matched_lane",
        "location_raw",
        "sponsorship_flag",
        "h1b_count",
        "match_score",
        "eval_global_score",
        "eval_action",
        "is_new",
        "is_rotational",
        "posted_at",
        "apply_url",
        "source_platform",
        "eval_reasoning",
        "eval_red_flags",
    ]

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for job in jobs:
            row = {col: job.get(col, "") for col in columns}
            writer.writerow(row)

    return csv_path

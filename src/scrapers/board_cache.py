"""Remember job boards that keep coming back empty so later runs can skip them.

Thousands of the boards in companies.json are dead (404) or have no openings.
Re-checking them every run wastes most of the scrape time. A board is skipped
only after it was empty on `min_empty_runs` consecutive checks, and is re-checked
once its last check is older than `skip_days`. Errors never count as "empty".
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

KEY_FIELDS = ("token", "site", "board", "id", "subdomain", "tenant")


def board_key(company: dict) -> str:
    base = next((str(company[k]) for k in KEY_FIELDS if company.get(k)), "")
    if company.get("site") and company.get("tenant"):  # Workday: tenant + site
        base = f"{company['tenant']}/{company['site']}"
    return base.lower()


class BoardCache:
    def __init__(self, path: Path, skip_days: int = 3, min_empty_runs: int = 2, enabled: bool = True):
        self.path = Path(path)
        self.skip_days = skip_days
        self.min_empty_runs = min_empty_runs
        self.enabled = enabled
        self.data: dict[str, dict[str, dict]] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.data = {}

    def should_skip(self, platform: str, company: dict) -> bool:
        if not self.enabled:
            return False
        entry = self.data.get(platform, {}).get(board_key(company))
        if not entry or entry.get("empty", 0) < self.min_empty_runs:
            return False
        try:
            checked = datetime.fromisoformat(entry["checked"])
        except (KeyError, ValueError):
            return False
        return datetime.now() - checked < timedelta(days=self.skip_days)

    def record(self, platform: str, company: dict, job_count: int):
        key = board_key(company)
        if not key:
            return
        boards = self.data.setdefault(platform, {})
        if job_count > 0:
            boards.pop(key, None)
            return
        entry = boards.get(key, {"empty": 0})
        entry["empty"] = entry.get("empty", 0) + 1
        entry["checked"] = datetime.now().isoformat(timespec="seconds")
        boards[key] = entry

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data), encoding="utf-8")
        except OSError:
            pass

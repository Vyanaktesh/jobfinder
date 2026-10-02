from __future__ import annotations

import logging
import re
import sqlite3
from functools import lru_cache
from pathlib import Path

from rapidfuzz import fuzz, process

logger = logging.getLogger(__name__)


def normalize_employer(name: str) -> str:
    """Normalize employer name for matching by removing legal suffixes and punctuation."""
    name = name.lower().strip()
    name = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|company|group|plc|lp|na|n\.a\.)\.?\b", "", name)
    name = re.sub(r"[.,;:'\"-]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


class H1BLookup:
    """Look up H1B sponsorship history for employers.

    Uses exact match first, then fuzzy matching via rapidfuzz.
    Results are cached with LRU eviction to bound memory usage (Finding #9).

    Args:
        db_path: Path to SQLite database containing h1b_employers table.
        green_threshold: Minimum worker count for GREEN flag.
        yellow_threshold: Minimum worker count for YELLOW flag.
    """

    def __init__(self, db_path: str | Path, green_threshold: int = 10, yellow_threshold: int = 1):
        self.green_threshold = green_threshold
        self.yellow_threshold = yellow_threshold
        self._employers: dict[str, int] = {}  # normalized_name -> total_count
        self._employer_list: list[tuple[str, int]] = []  # for fuzzy search

        db_path = Path(db_path)
        if not db_path.exists():
            logger.warning(f"H1B database not found at {db_path}")
            return

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT employer_name_normalized, SUM(worker_count) as total "
            "FROM h1b_employers GROUP BY employer_name_normalized"
        ).fetchall()
        conn.close()

        self._employers = {row[0]: row[1] for row in rows}
        self._employer_list = list(self._employers.items())
        logger.info(f"Loaded {len(self._employers)} H1B employers for lookup")

    def lookup(self, company_name: str) -> tuple[str, int]:
        """Look up H1B sponsorship flag for a company.

        Returns:
            Tuple of (flag, h1b_count) where flag is GREEN/YELLOW/RED.
        """
        if not self._employers:
            return ("RED", 0)

        normalized = normalize_employer(company_name)
        return self._lookup_cached(normalized)

    @lru_cache(maxsize=2000)
    def _lookup_cached(self, normalized: str) -> tuple[str, int]:
        """Cached lookup by normalized name. LRU-bounded to 2000 entries (Finding #9)."""
        # Try exact match first (O(1) dict lookup)
        if normalized in self._employers:
            count = self._employers[normalized]
            return self._flag(count), count

        # Fuzzy match — rapidfuzz's C-level extractOne scales to 100k+ employers
        best_score = 0
        best_count = 0
        match = process.extractOne(
            normalized, self._employers.keys(), scorer=fuzz.token_set_ratio, score_cutoff=85
        )
        if match:
            best_score = match[1]
            best_count = self._employers[match[0]]

        if best_score >= 85:
            return self._flag(best_count), best_count
        return ("RED", 0)

    def _flag(self, count: int) -> str:
        """Convert H1B petition count to GREEN/YELLOW/RED flag."""
        if count >= self.green_threshold:
            return "GREEN"
        elif count >= self.yellow_threshold:
            return "YELLOW"
        return "RED"

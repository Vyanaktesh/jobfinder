from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from rapidfuzz import fuzz

from src.models import RawJob

logger = logging.getLogger(__name__)

SENIORITY_LEVELS = {
    "senior": 3, "sr": 3, "sr.": 3,
    "staff": 4, "principal": 5, "lead": 4, "head": 5,
    "director": 6, "vp": 7, "group": 4,
    "junior": 1, "jr": 1, "jr.": 1,
    "associate": 1, "entry": 0,
}

MAX_ALLOWED_SENIORITY = 2  # reject senior (3) and above; allow associate (1), junior (1), entry (0)


ROTATIONAL_SIGNALS = [
    "rotational", "rotation", "leadership development program",
    "early career", "new grad", "graduate program", "development program",
    "analyst program", "associate program", "trainee", "ldp",
    "emerging talent", "early talent", "launch program", "accelerator program",
]


class TitleMatcher:
    """Fuzzy title matcher that scores job titles against configured role lanes.

    Uses rapidfuzz token_set_ratio for fuzzy matching, with abbreviation expansion,
    keyword validation, and seniority filtering.

    Args:
        config_path: Path to role_lanes.json config file.
        fuzzy_min_threshold: Minimum fuzzy score to consider a match candidate.
        fuzzy_pass_threshold: Minimum score to pass as a final match.
    """

    def __init__(self, config_path: str | Path, fuzzy_min_threshold: int = 65, fuzzy_pass_threshold: int = 70):
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)

        self.abbreviations: dict[str, str] = config.get("abbreviations", {})
        self.lanes: list[dict] = config.get("lanes", [])
        # Finding #18: Configurable thresholds instead of magic numbers
        self.fuzzy_min_threshold = fuzzy_min_threshold
        self.fuzzy_pass_threshold = fuzzy_pass_threshold

    def expand_abbreviations(self, title: str) -> str:
        words = title.lower().split()
        expanded = []
        for w in words:
            clean = w.strip(".,;:-/()")
            if clean in self.abbreviations:
                expanded.append(self.abbreviations[clean])
            else:
                expanded.append(w)
        return " ".join(expanded)

    def match(self, job: RawJob) -> tuple[str, float, bool] | None:
        """
        Returns (lane_name, score, is_rotational) or None if no match.
        """
        title_lower = job.title.lower()
        expanded = self.expand_abbreviations(job.title)
        desc_prefix = job.description_text[:500].lower() if job.description_text else ""

        best_lane = None
        best_score = 0.0
        best_rotational = False

        for lane in self.lanes:
            lane_name = lane["lane"]
            canonical_titles = lane.get("canonical_titles", [])
            required_kw = lane.get("required_keywords", [])
            boost_kw = lane.get("boost_keywords", [])
            negative_kw = lane.get("negative_keywords", [])

            title_must_any = lane.get("title_must_contain_any", [])

            if negative_kw:
                combined = title_lower + " " + desc_prefix
                if any(neg in combined for neg in negative_kw):
                    continue

            if title_must_any:
                if not any(kw in title_lower or kw in expanded for kw in title_must_any):
                    continue

            if required_kw:
                has_required = all(
                    kw in expanded or kw in title_lower or kw in desc_prefix
                    for kw in required_kw
                )
                if not has_required:
                    continue

            title_seniority = max(
                (SENIORITY_LEVELS.get(w.strip(".,;:-/()"), 0) for w in title_lower.split()),
                default=0,
            )
            if title_seniority > MAX_ALLOWED_SENIORITY:
                continue

            top_fuzzy = 0.0
            for canonical in canonical_titles:
                score = fuzz.token_set_ratio(expanded, canonical.lower())
                top_fuzzy = max(top_fuzzy, score)
                score2 = fuzz.token_set_ratio(title_lower, canonical.lower())
                top_fuzzy = max(top_fuzzy, score2)

            if top_fuzzy < self.fuzzy_min_threshold:
                continue

            boost_count = sum(
                1 for bk in boost_kw
                if bk in title_lower or bk in desc_prefix
            )
            final_score = min(top_fuzzy * (1.0 + 0.05 * boost_count), 100.0)

            if final_score > best_score:
                best_score = final_score
                best_lane = lane_name

            is_rotational = lane.get("track") == "rotational"
            if is_rotational and final_score >= 65:
                best_rotational = True

        if not best_rotational:
            best_rotational = any(sig in title_lower for sig in ROTATIONAL_SIGNALS)

        if best_lane and best_score >= self.fuzzy_pass_threshold:
            return (best_lane, round(best_score, 1), best_rotational)

        return None

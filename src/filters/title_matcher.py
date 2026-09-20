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
    "iii": 3, "iv": 3,  # "Financial Analyst III/IV" ladders are mid/senior level
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

# "FP&A", "FP and A", "FP/A", "FPnA" -> "fpa" so every spelling compares equal.
_FPA_RE = re.compile(r"\bfp\s*(?:&|and|/|n)\s*a\b")


def normalize(text: str) -> str:
    """Lowercase and strip punctuation so titles compare cleanly.

    rapidfuzz does not strip punctuation, so without this "Financial Analyst, FP&A"
    would tokenize as ["financial", "analyst,", "fp&a"] and score worse than
    "Financial Analyst FP&A". "&" becomes "and" ("Planning & Analysis" ==
    "Planning and Analysis") and FP&A spellings collapse to "fpa".
    """
    t = _FPA_RE.sub("fpa", (text or "").lower())
    t = t.replace("&", " and ")
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _word_regex(terms: list[str]) -> re.Pattern | None:
    """Regex matching any term as a whole word/phrase ('lead' must not hit 'leadership')."""
    cleaned = [normalize(t) for t in terms if normalize(t)]
    if not cleaned:
        return None
    return re.compile(r"\b(?:" + "|".join(re.escape(t) for t in cleaned) + r")\b")


class TitleMatcher:
    """Fuzzy title matcher that scores job titles against configured role lanes.

    Uses rapidfuzz token_set_ratio for fuzzy matching, with abbreviation expansion,
    keyword validation, and seniority filtering.

    Lane keys (all optional except ``lane`` and ``canonical_titles``):
        title_must_contain_any: title must contain at least one (substring match).
        title_role_any:         title must also contain at least one of these role
                                words as a whole word, e.g. ["analyst", "associate"].
                                Stops "Financial Advisor" fuzzy-matching "Financial Analyst".
        required_keywords:      all must appear in the title or first 500 chars of description.
        negative_keywords:      whole-word/phrase match against the *title only* -> reject.
        boost_keywords:         each one found in title or first 500 chars raises the score 5%.

    Args:
        config_path: Path to a role lanes JSON config file.
        fuzzy_min_threshold: Minimum fuzzy score to consider a match candidate.
        fuzzy_pass_threshold: Minimum score to pass as a final match.
    """

    def __init__(self, config_path: str | Path, fuzzy_min_threshold: int = 65, fuzzy_pass_threshold: int = 70):
        with open(config_path) as f:
            config = json.load(f)

        self.abbreviations: dict[str, str] = config.get("abbreviations", {})
        self.lanes: list[dict] = config.get("lanes", [])
        # Finding #18: Configurable thresholds instead of magic numbers
        self.fuzzy_min_threshold = fuzzy_min_threshold
        self.fuzzy_pass_threshold = fuzzy_pass_threshold

        # Pre-normalize lane config once instead of per job (~15k jobs x N lanes per run).
        self._compiled = [
            {
                "lane": lane,
                "canonicals": [normalize(c) for c in lane.get("canonical_titles", [])],
                "must_any": [normalize(k) for k in lane.get("title_must_contain_any", [])],
                "role_any": _word_regex(lane.get("title_role_any", [])),
                "negative": _word_regex(lane.get("negative_keywords", [])),
            }
            for lane in self.lanes
        ]

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
        title_norm = normalize(job.title)
        expanded = normalize(self.expand_abbreviations(job.title))
        desc_prefix = job.description_text[:500].lower() if job.description_text else ""

        title_seniority = max(
            (SENIORITY_LEVELS.get(w, 0) for w in expanded.split()),
            default=0,
        )

        best_lane = None
        best_key = (0.0, 0.0)  # (score, exact-ness) so the most specific lane wins ties
        best_rotational = False

        for c in self._compiled:
            lane = c["lane"]
            lane_name = lane["lane"]
            required_kw = lane.get("required_keywords", [])
            boost_kw = lane.get("boost_keywords", [])

            neg = c["negative"]
            if neg and (neg.search(title_norm) or neg.search(expanded)):
                continue

            if c["must_any"]:
                if not any(kw in title_norm or kw in expanded for kw in c["must_any"]):
                    continue

            role = c["role_any"]
            if role and not (role.search(title_norm) or role.search(expanded)):
                continue

            if required_kw:
                has_required = all(
                    kw in expanded or kw in title_lower or kw in desc_prefix
                    for kw in required_kw
                )
                if not has_required:
                    continue

            if title_seniority > MAX_ALLOWED_SENIORITY:
                continue

            top_fuzzy = 0.0
            top_exact = 0.0
            for canonical in c["canonicals"]:
                top_fuzzy = max(
                    top_fuzzy,
                    fuzz.token_set_ratio(expanded, canonical),
                    fuzz.token_set_ratio(title_norm, canonical),
                )
                # token_set_ratio scores any subset as 100 ("Corporate Finance Analyst" vs
                # "Finance Analyst"), so plain ratio breaks ties toward the closest lane.
                top_exact = max(top_exact, fuzz.ratio(title_norm, canonical))

            if top_fuzzy < self.fuzzy_min_threshold:
                continue

            boost_count = sum(
                1 for bk in boost_kw
                if bk in title_lower or bk in desc_prefix
            )
            final_score = min(top_fuzzy * (1.0 + 0.05 * boost_count), 100.0)

            if (final_score, top_exact) > best_key:
                best_key = (final_score, top_exact)
                best_lane = lane_name

            is_rotational = lane.get("track") == "rotational"
            if is_rotational and final_score >= 65:
                best_rotational = True

        if not best_rotational:
            best_rotational = any(sig in title_lower for sig in ROTATIONAL_SIGNALS)

        best_score = best_key[0]
        if best_lane and best_score >= self.fuzzy_pass_threshold:
            return (best_lane, round(best_score, 1), best_rotational)

        return None

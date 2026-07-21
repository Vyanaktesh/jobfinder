"""Data models for the job scraper pipeline.

Defines RawJob (unfiltered scraper output) and FilteredJob (post-filter,
ready for DB insertion) with deduplication key generation.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RawJob:
    """A job posting as scraped from an ATS API, before any filtering."""
    source_platform: str
    company_token: str
    company_name: str
    external_id: str
    title: str
    location_raw: str
    description_html: str
    description_text: str
    posted_at: datetime | None
    apply_url: str
    salary_raw: str | None = None
    workplace_type: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class FilteredJob:
    """A job posting that passed all filters, enriched with matching metadata.

    Created via FilteredJob.from_raw() which also computes the dedup_key.
    """

    source_platform: str
    company_token: str
    company_name: str
    external_id: str
    title: str
    location_raw: str
    description_html: str
    description_text: str
    posted_at: datetime | None
    apply_url: str
    salary_raw: str | None
    workplace_type: str | None
    matched_lane: str
    match_score: float
    is_new: bool
    is_rotational: bool
    is_internship: bool
    sponsorship_flag: str  # GREEN / YELLOW / RED / NA (NA = internship, no H1B needed)
    h1b_count: int | None
    location_parsed: str
    dedup_key: str

    @staticmethod
    def compute_dedup_key(company_name: str, title: str, location: str) -> str:
        """Generate a SHA-256 dedup key from normalized company+title+location.

        Returns a 16-char hex prefix — collision probability is negligible
        at the expected scale (<100K jobs).
        """
        normalized = "|".join(
            re.sub(r"\s+", " ", part.lower().strip())
            for part in (company_name, title, location)
        )
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    @classmethod
    def from_raw(
        cls,
        raw: RawJob,
        *,
        matched_lane: str,
        match_score: float,
        is_new: bool = True,
        is_rotational: bool = False,
        is_internship: bool = False,
        sponsorship_flag: str = "RED",
        h1b_count: int | None = None,
        location_parsed: str = "",
    ) -> FilteredJob:
        """Construct a FilteredJob from a RawJob plus filter/enrichment results.

        Automatically computes the dedup_key from company_name, title, and location_raw.
        """
        return cls(
            source_platform=raw.source_platform,
            company_token=raw.company_token,
            company_name=raw.company_name,
            external_id=raw.external_id,
            title=raw.title,
            location_raw=raw.location_raw,
            description_html=raw.description_html,
            description_text=raw.description_text,
            posted_at=raw.posted_at,
            apply_url=raw.apply_url,
            salary_raw=raw.salary_raw,
            workplace_type=raw.workplace_type,
            matched_lane=matched_lane,
            match_score=match_score,
            is_new=is_new,
            is_rotational=is_rotational,
            is_internship=is_internship,
            sponsorship_flag=sponsorship_flag,
            h1b_count=h1b_count,
            location_parsed=location_parsed or raw.location_raw,
            dedup_key=cls.compute_dedup_key(
                raw.company_name, raw.title, raw.location_raw
            ),
        )

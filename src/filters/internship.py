"""Detect internship / co-op postings by title.

Internships and co-ops are handled as a distinct employment type in the
pipeline: they don't need H1B sponsorship (typically covered by CPT/OPT for
international students), so they're exempted from the sponsorship keyword
filter and get a separate "NA" flag instead of the GREEN/YELLOW/RED H1B
classification used for full-time roles.
"""
from __future__ import annotations

import re

INTERNSHIP_TITLE_RE = re.compile(r"\b(intern|internship|co-?op)\b", re.IGNORECASE)


def is_internship_title(title: str) -> bool:
    """Return True if the job title indicates an internship or co-op posting."""
    return bool(INTERNSHIP_TITLE_RE.search(title or ""))

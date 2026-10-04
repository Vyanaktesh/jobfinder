"""Sponsorship blacklist filter.

Scans job descriptions for phrases that indicate the employer will NOT
sponsor work visas (e.g., "must be authorized to work", "no sponsorship").
Phrases are loaded from a plain-text blacklist file (one per line).
"""
from __future__ import annotations

from pathlib import Path


class SponsorshipFilter:
    """Filter jobs based on anti-sponsorship language in descriptions.

    Args:
        blacklist_path: Path to a text file with one blacklist phrase per line.
    """

    def __init__(self, blacklist_path: str | Path):
        path = Path(blacklist_path)
        self.phrases = [
            line.strip().lower()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def check(self, description_text: str) -> str | None:
        """Check whether a job description contains anti-sponsorship language.

        Args:
            description_text: Plain-text job description to scan.

        Returns:
            The matched blacklist phrase if found, or None if clean.
        """
        if not description_text:
            return None

        desc_lower = description_text.lower()
        for phrase in self.phrases:
            if phrase in desc_lower:
                return phrase

        return None

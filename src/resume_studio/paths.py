"""Where Resume Studio finds its data.

Your real resumes, profile and fact bank hold personal details, so they live in
config/resume_studio/ (gitignored). A fake sample ships in the repo so the feature
works out of the box; load your own with scripts/import_resume_kit.py.
"""
from __future__ import annotations

import os
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
ROOT = PKG_DIR.parent.parent
SAMPLE_DIR = PKG_DIR / "sample_data"
TEMPLATE_DIR = PKG_DIR / "templates"
USER_DIR = ROOT / "config" / "resume_studio"
OUTPUT_DIR = ROOT / "output" / "resumes"


def data_dir() -> Path:
    """RESUME_STUDIO_DATA override, else your config/resume_studio, else the fake sample."""
    override = os.getenv("RESUME_STUDIO_DATA")
    if override:
        return Path(override)
    if (USER_DIR / "profile.json").exists():
        return USER_DIR
    return SAMPLE_DIR


def using_sample() -> bool:
    return data_dir() == SAMPLE_DIR

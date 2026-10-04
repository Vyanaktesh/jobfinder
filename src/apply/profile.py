"""Load applicant profile for form auto-fill."""
from __future__ import annotations

from pathlib import Path

import yaml

_DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "applicant_profile.yml"


def load_profile(path: Path | str | None = None) -> dict:
    path = Path(path) if path else _DEFAULT_PATH
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)

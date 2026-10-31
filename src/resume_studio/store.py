"""File naming and saving for built resumes."""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from pathlib import Path


def safe_name(text: str, fallback: str = "Resume", maxlen: int = 40) -> str:
    """ASCII letters, digits and single underscores only; never empty; never a path."""
    s = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:maxlen].strip("_")
    return s or fallback


def unique_stem(out_dir: Path, company: str, role: str, today: date | None = None) -> str:
    """<Company>_<Role>_<YYYY-MM-DD>, with _2, _3 ... if a build with that name already exists."""
    day = (today or date.today()).isoformat()
    base = f"{safe_name(company, 'Company')}_{safe_name(role, 'Role')}_{day}"
    stem, n = base, 1
    while (out_dir / f"{stem}.tex").exists() or (out_dir / f"{stem}.pdf").exists():
        n += 1
        stem = f"{base}_{n}"
    return stem


def inside(path: Path, folder: Path) -> bool:
    """True if `path` resolves to a location inside `folder` (blocks ../ tricks and symlink escapes)."""
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except (ValueError, OSError):
        return False

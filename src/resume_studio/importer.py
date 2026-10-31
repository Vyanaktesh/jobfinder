"""Import a resume_kit (a folder or the .zip) into config/resume_studio/.

Only data/*.json and BULLET_BANK.md are taken, and only by file name, so a hostile zip
cannot write anywhere else.
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from src.resume_studio import paths

_JSON_NAME = re.compile(r"^[A-Za-z0-9_-]{1,60}\.json$")


def find_kit_source(root: Path | None = None) -> Path | None:
    """A resume_kit folder or resume_kit*.zip dropped in the repo root, if any."""
    root = Path(root or paths.ROOT)
    folder = root / "resume_kit"
    if (folder / "data" / "profile.json").is_file():
        return folder
    zips = sorted(root.glob("resume_kit*.zip"))
    return zips[-1] if zips else None


def _read_kit(src: Path) -> dict[str, bytes]:
    """Return {filename: bytes} for the importable files in a kit folder or zip."""
    files: dict[str, bytes] = {}
    if src.is_dir():
        data = src / "data"
        for f in sorted(data.glob("*.json")) if data.is_dir() else []:
            if _JSON_NAME.match(f.name):
                files[f.name] = f.read_bytes()
        if (src / "BULLET_BANK.md").is_file():
            files["BULLET_BANK.md"] = (src / "BULLET_BANK.md").read_bytes()
    elif zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as z:
            for info in z.infolist():
                parts = info.filename.replace("\\", "/").split("/")
                name = parts[-1]
                if info.is_dir() or info.file_size > 5_000_000:
                    continue
                if name == "BULLET_BANK.md" and len(parts) <= 2:
                    files[name] = z.read(info)
                elif len(parts) >= 2 and parts[-2] == "data" and _JSON_NAME.match(name):
                    files[name] = z.read(info)
    else:
        raise ValueError(f"{src} is neither a folder nor a .zip file.")
    return files


def import_kit(src: Path, dest: Path | None = None, force: bool = False) -> tuple[list[str], list[str]]:
    """Copy a kit's data into `dest`. Returns (copied, skipped). Raises ValueError for a bad kit."""
    src, dest = Path(src), Path(dest or paths.USER_DIR)
    files = _read_kit(src)
    if "profile.json" not in files:
        raise ValueError(f"{src} doesn't look like a resume_kit (no data/profile.json).")
    if "BULLET_BANK.md" not in files:
        raise ValueError("BULLET_BANK.md is missing. It is required: it is the list of facts a patch may use.")
    bases = [n for n in files if n.endswith(".json") and n != "profile.json"]
    if not bases:
        raise ValueError("The kit has no base resume files (data/*.json other than profile.json).")
    for name, raw in files.items():          # fail before writing anything
        if name.endswith(".json"):
            try:
                json.loads(raw.decode("utf-8-sig"))
            except ValueError as e:
                raise ValueError(f"{name} is not valid JSON: {e}") from e
    dest.mkdir(parents=True, exist_ok=True)
    copied, skipped = [], []
    for name, raw in files.items():
        target = dest / name
        if target.exists() and not force:
            skipped.append(name)
            continue
        target.write_bytes(raw)
        copied.append(name)
    return copied, skipped


def auto_import() -> list[str]:
    """If you have no data yet and a resume_kit sits in the repo folder, import it. Returns copied names."""
    if (paths.USER_DIR / "profile.json").exists():
        return []
    src = find_kit_source()
    if not src:
        return []
    try:
        copied, _ = import_kit(src)
        return copied
    except ValueError:
        return []

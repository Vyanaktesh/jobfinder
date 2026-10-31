#!/usr/bin/env python3
"""Load your own resumes into Resume Studio.

  python scripts/import_resume_kit.py path/to/resume_kit          # copies data/*.json and BULLET_BANK.md
  python scripts/import_resume_kit.py path/to/resume_kit --force  # overwrite what's already imported

Files go to config/resume_studio/, which is gitignored: they hold your contact details and
client facts, and this repo is public. Nothing is uploaded anywhere.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.resume_studio.paths import USER_DIR  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kit", type=Path, help="the resume_kit folder (contains data/ and BULLET_BANK.md)")
    ap.add_argument("--force", action="store_true", help="overwrite existing files")
    args = ap.parse_args()

    data = args.kit / "data"
    bank = args.kit / "BULLET_BANK.md"
    if not data.is_dir() or not (data / "profile.json").is_file():
        sys.exit(f"{args.kit} doesn't look like a resume_kit folder (no data/profile.json).")
    if not bank.is_file():
        sys.exit(f"{bank} not found. BULLET_BANK.md is required: it is the list of facts a patch may use.")

    sources = sorted(data.glob("*.json")) + [bank]
    for f in sources:                      # fail early on broken files, before copying anything
        if f.suffix == ".json":
            try:
                json.loads(f.read_text(encoding="utf-8"))
            except ValueError as e:
                sys.exit(f"{f.name} is not valid JSON: {e}")

    USER_DIR.mkdir(parents=True, exist_ok=True)
    copied = skipped = 0
    for f in sources:
        dest = USER_DIR / f.name
        if dest.exists() and not args.force:
            print(f"  = {f.name} already imported (use --force to overwrite)")
            skipped += 1
            continue
        shutil.copyfile(f, dest)
        print(f"  + {f.name}")
        copied += 1
    print(f"\n{copied} file(s) imported into {USER_DIR} ({skipped} skipped).")
    print("That folder is gitignored. Restart the dashboard server and open the Resume Studio tab.")


if __name__ == "__main__":
    main()

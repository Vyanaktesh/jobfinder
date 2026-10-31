#!/usr/bin/env python3
"""Load your own resumes into Resume Studio.

  python scripts/import_resume_kit.py path/to/resume_kit        # the unzipped folder
  python scripts/import_resume_kit.py path/to/resume_kit.zip    # or the zip itself
  python scripts/import_resume_kit.py <kit> --force             # overwrite what is already imported

Files go to config/resume_studio/, which is gitignored: they hold your contact details and
client facts, and this repo is public. Nothing is uploaded anywhere.

Shortcut: drop the resume_kit folder (or resume_kit.zip) into the repo folder and start
.\start_dashboard.bat; it is imported automatically the first time.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.resume_studio.importer import import_kit  # noqa: E402
from src.resume_studio.paths import USER_DIR  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kit", type=Path, help="the resume_kit folder or .zip")
    ap.add_argument("--force", action="store_true", help="overwrite existing files")
    args = ap.parse_args()
    try:
        copied, skipped = import_kit(args.kit, USER_DIR, args.force)
    except (ValueError, OSError) as e:
        sys.exit(f"Could not import: {e}")
    for n in copied:
        print(f"  + {n}")
    for n in skipped:
        print(f"  = {n} already imported (use --force to overwrite)")
    print(f"\n{len(copied)} file(s) imported into {USER_DIR} ({len(skipped)} skipped).")
    print("That folder is gitignored. Restart the dashboard server and open the Resume Studio tab.")


if __name__ == "__main__":
    main()

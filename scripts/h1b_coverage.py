#!/usr/bin/env python3
"""Show how well the scraper covers your H1B sponsor list.

  python scripts/h1b_coverage.py

Reports (1) scraped companies that are NOT in the H1B list (their jobs show as
RED) and (2) H1B sponsors that have no job board in config/companies.json yet,
i.e. sponsors whose jobs you are currently never seeing. The second list is
written to output/h1b_missing_boards.txt — feed it to scripts/discover_boards.py
to find their Greenhouse/Lever/Ashby tokens.
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rapidfuzz import fuzz, process  # noqa: E402

from src.h1b.lookup import normalize_employer  # noqa: E402


def board_names(companies: dict) -> set[str]:
    names = set()
    for platform, items in companies.items():
        if platform.startswith("_"):
            continue
        for c in items:
            if isinstance(c, dict):
                n = c.get("name") or c.get("token") or c.get("board") or c.get("site") or c.get("tenant") or c.get("id")
            else:
                n = c
            if n:
                names.add(normalize_employer(str(n)))
    return names


def main():
    companies = json.load(open(ROOT / "config" / "companies.json"))
    conn = sqlite3.connect(str(ROOT / "data" / "jobs.db"))
    sponsors = [r[0] for r in conn.execute(
        "SELECT employer_name FROM h1b_employers GROUP BY employer_name_normalized ORDER BY SUM(worker_count) DESC")]
    conn.close()
    boards = board_names(companies)

    missing = []
    for name in sponsors:
        norm = normalize_employer(name)
        # match on whole-name similarity, with a slug-style fallback (e.g. "palo alto networks" ~ "paloaltonetworks")
        if norm in boards or process.extractOne(norm.replace(" ", ""), boards, scorer=fuzz.ratio, score_cutoff=90):
            continue
        missing.append(name)

    out = ROOT / "output"
    out.mkdir(exist_ok=True)
    (out / "h1b_missing_boards.txt").write_text("\n".join(missing) + "\n")
    print(f"{len(sponsors)} H1B sponsors in DB, {len(boards)} scraped boards")
    print(f"{len(sponsors) - len(missing)} sponsors already have a board; {len(missing)} do not.")
    print(f"List written to {out / 'h1b_missing_boards.txt'} — run scripts/discover_boards.py on it to find boards.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Find Greenhouse / Lever / Ashby job boards for your H1B sponsor list.

  python scripts/find_h1b_boards.py                 # probe sponsors with no board yet
  python scripts/find_h1b_boards.py --dry-run       # report only, don't edit companies.json
  python scripts/find_h1b_boards.py --limit 200     # try the top 200 sponsors (by petitions)
  python scripts/find_h1b_boards.py --names my.txt  # use a text file instead of the DB

For each sponsor it generates likely board slugs ("Palo Alto Networks" ->
paloaltonetworks, palo-alto-networks, paloalto ...), probes the three public ATS
APIs, and adds every board that exists and has open jobs to config/companies.json
with the sponsor's proper name (so the H1B lookup matches it exactly).

Needs outbound internet access. Sponsors that use Workday/iCIMS (most large
enterprises and IT-services firms) can't be auto-discovered this way — they're
listed in output/h1b_no_board_found.txt so you can add them by hand
(see README: "Adding a company").
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.h1b.lookup import normalize_employer  # noqa: E402

COMPANIES_FILE = ROOT / "config" / "companies.json"
OUT_DIR = ROOT / "output"

# platform -> (url template, key used in companies.json entries)
PLATFORMS = {
    "greenhouse": ("https://boards-api.greenhouse.io/v1/boards/{t}/jobs", "token"),
    "lever": ("https://api.lever.co/v0/postings/{t}?mode=json", "site"),
    "ashby": ("https://api.ashbyhq.com/posting-api/job-board/{t}", "board"),
}

STOP_WORDS = {"the", "of", "and", "technologies", "technology", "systems", "holdings", "international",
              "america", "usa", "us", "services", "solutions", "labs", "software"}


def slug_candidates(name: str) -> list[str]:
    """Likely board slugs for a company name, most probable first."""
    base = re.sub(r"[^a-z0-9 ]", " ", normalize_employer(name).replace("&", " and "))
    words = base.split()
    if not words:
        return []
    core = [w for w in words if w not in STOP_WORDS] or words
    cands = ["".join(words), "-".join(words), "".join(core), "-".join(core), core[0]]
    if len(core) > 1:
        cands.append("".join(core[:2]))
    out = []
    for c in cands:
        if len(c) >= 3 and c not in out:
            out.append(c)
    return out


def job_count(platform: str, data) -> int:
    if platform == "lever":
        return len(data) if isinstance(data, list) else 0
    return len(data.get("jobs", [])) if isinstance(data, dict) else 0


async def probe(client, sem, platform, token):
    url = PLATFORMS[platform][0].format(t=token)
    async with sem:
        try:
            r = await client.get(url, timeout=15)
            if r.status_code == 200:
                n = job_count(platform, r.json())
                if n > 0:
                    return n
        except (httpx.HTTPError, ValueError):
            pass
    return 0


async def find_for(client, sem, name):
    """Return [(platform, token, n_jobs)] for every board found for this sponsor."""
    cands = slug_candidates(name)
    jobs = [(p, c) for p in PLATFORMS for c in cands]
    counts = await asyncio.gather(*(probe(client, sem, p, c) for p, c in jobs))
    found, seen = [], set()
    for (p, c), n in zip(jobs, counts):
        if n and p not in seen:  # first (most probable) slug per platform wins
            found.append((p, c, n))
            seen.add(p)
    return found


def existing_tokens(companies: dict) -> dict[str, set[str]]:
    out = {}
    for platform, (_, key) in PLATFORMS.items():
        toks = set()
        for c in companies.get(platform, []):
            toks.add((c.get(key, "") if isinstance(c, dict) else str(c)).lower())
        out[platform] = toks
    return out


def sponsor_names(args) -> list[str]:
    if args.names:
        return [l.strip().split(",")[0] for l in Path(args.names).read_text().splitlines()
                if l.strip() and not l.startswith("#")]
    conn = sqlite3.connect(str(ROOT / "data" / "jobs.db"))
    rows = conn.execute("SELECT employer_name FROM h1b_employers GROUP BY employer_name_normalized "
                        "ORDER BY SUM(worker_count) DESC").fetchall()
    conn.close()
    return [r[0] for r in rows]


async def main_async(args):
    companies = json.loads(COMPANIES_FILE.read_text())
    have = existing_tokens(companies)
    names = sponsor_names(args)
    if args.limit:
        names = names[: args.limit]
    print(f"Probing {len(names)} sponsors across {', '.join(PLATFORMS)}...")

    sem = asyncio.Semaphore(args.concurrency)
    added, none_found = [], []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for i, name in enumerate(names, 1):
            found = await find_for(client, sem, name)
            new = [(p, t, n) for p, t, n in found if t.lower() not in have[p]]
            for p, t, n in new:
                key = PLATFORMS[p][1]
                companies.setdefault(p, []).append({key: t, "name": name})
                have[p].add(t.lower())
                added.append((name, p, t, n))
                print(f"  + {name}: {p}/{t} ({n} jobs)")
            if not found:
                none_found.append(name)
            if i % 50 == 0:
                print(f"  ... {i}/{len(names)}")

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "h1b_no_board_found.txt").write_text("\n".join(none_found) + "\n")
    if added and not args.dry_run:
        COMPANIES_FILE.write_text(json.dumps(companies, indent=2))
    print(f"\n{len(added)} new boards {'(dry run, not saved)' if args.dry_run else 'added to companies.json'}; "
          f"{len(none_found)} sponsors have no Greenhouse/Lever/Ashby board "
          f"(see output/h1b_no_board_found.txt — likely Workday/iCIMS).")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, help="Only try the top N sponsors")
    ap.add_argument("--names", help="Text file of company names instead of the H1B table")
    ap.add_argument("--concurrency", type=int, default=10)
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()

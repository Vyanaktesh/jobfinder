#!/usr/bin/env python3
"""Add job boards to config/companies.json from careers-page URLs.

For H1B sponsors that auto-discovery couldn't find, open the company's careers
page, click into any job, and paste the URL here — the script works out the
platform and board token for you:

  python scripts/add_board.py "Cognizant=https://careers.cognizant.com/..." 
  python scripts/add_board.py --file my_urls.txt          # one "Name=URL" per line
  python scripts/add_board.py --verify "Name=URL"          # also check it returns jobs (needs internet)

Understood URL shapes:
  boards.greenhouse.io/<token>/...      job-boards.greenhouse.io/<token>/...
  jobs.lever.co/<site>/...              jobs.ashbyhq.com/<board>/...
  <tenant>.<wdN>.myworkdayjobs.com/[en-US/]<site>/...
  <tenant>.icims.com/...                apply.workable.com/<subdomain>/...
  jobs.smartrecruiters.com/<Company>/...
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
COMPANIES_FILE = ROOT / "config" / "companies.json"


def parse_board_url(url: str, name: str) -> tuple[str, dict] | None:
    """Return (platform, companies.json entry) for a careers URL, or None."""
    u = urlparse(url if "//" in url else "https://" + url)
    host = (u.hostname or "").lower()
    parts = [p for p in u.path.split("/") if p]

    if host.endswith("greenhouse.io") and parts:
        # boards.greenhouse.io/<token>  or  job-boards.greenhouse.io/<token>
        return "greenhouse", {"token": parts[0], "name": name}
    if host == "jobs.lever.co" and parts:
        return "lever", {"site": parts[0], "name": name}
    if host == "jobs.ashbyhq.com" and parts:
        return "ashby", {"board": parts[0], "name": name}
    if host.endswith(".myworkdayjobs.com"):
        labels = host.split(".")
        if len(labels) >= 4:
            tenant, wd = labels[0], labels[1]
            # path: [en-US/]<site>/...   (skip a locale segment like en-US, fr-CA)
            site_parts = [p for p in parts if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)]
            if site_parts:
                return "workday", {"tenant": tenant, "wd": wd, "site": site_parts[0], "name": name}
    if host.endswith(".icims.com"):
        return "icims", {"tenant": host.split(".")[0], "name": name}
    if host == "apply.workable.com" and parts:
        return "workable", {"subdomain": parts[0], "name": name}
    if host.endswith("smartrecruiters.com") and parts:
        return "smartrecruiters", {"id": parts[0], "name": name}
    return None


def key_of(platform: str, entry: dict) -> tuple:
    if platform == "workday":
        return (entry["tenant"].lower(), entry["site"].lower())
    k = next(v for kk, v in entry.items() if kk != "name")
    return (k.lower(),)


def verify(platform: str, entry: dict) -> int:
    """Number of open jobs the board returns (0 = not working). Needs internet."""
    import httpx
    try:
        if platform == "greenhouse":
            r = httpx.get(f"https://boards-api.greenhouse.io/v1/boards/{entry['token']}/jobs", timeout=20)
            return len(r.json().get("jobs", [])) if r.status_code == 200 else 0
        if platform == "lever":
            r = httpx.get(f"https://api.lever.co/v0/postings/{entry['site']}?mode=json", timeout=20)
            return len(r.json()) if r.status_code == 200 else 0
        if platform == "ashby":
            r = httpx.get(f"https://api.ashbyhq.com/posting-api/job-board/{entry['board']}", timeout=20)
            return len(r.json().get("jobs", [])) if r.status_code == 200 else 0
        if platform == "workday":
            r = httpx.post(
                f"https://{entry['tenant']}.{entry['wd']}.myworkdayjobs.com/wday/cxs/{entry['tenant']}/{entry['site']}/jobs",
                json={"appliedFacets": {}, "searchText": "", "limit": 1, "offset": 0}, timeout=20)
            return int(r.json().get("total", 0)) if r.status_code == 200 else 0
    except Exception:
        return 0
    return -1  # no verifier for this platform


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("items", nargs="*", help='"Company Name=https://careers-url"')
    ap.add_argument("--file", help="text file with one Name=URL per line")
    ap.add_argument("--verify", action="store_true", help="only add boards that return jobs (needs internet)")
    args = ap.parse_args()

    items = list(args.items)
    if args.file:
        items += [l.strip() for l in Path(args.file).read_text(encoding="utf-8").splitlines()
                  if l.strip() and not l.startswith("#")]
    if not items:
        ap.error("give at least one Name=URL")

    data = json.loads(COMPANIES_FILE.read_text(encoding="utf-8"))
    have = {p: {key_of(p, c) for c in data.get(p, []) if isinstance(c, dict)} for p in
            ("greenhouse", "lever", "ashby", "workday", "icims", "workable", "smartrecruiters")}

    added = 0
    for item in items:
        name, _, url = item.partition("=")
        name, url = name.strip(), url.strip()
        if not url:
            print(f"  ?  skipped (use Name=URL): {item}")
            continue
        parsed = parse_board_url(url, name)
        if not parsed:
            print(f"  ?  {name}: don't recognise this URL — it must be a Greenhouse/Lever/Ashby/Workday/iCIMS/Workable/SmartRecruiters page")
            continue
        platform, entry = parsed
        if key_of(platform, entry) in have.setdefault(platform, set()):
            print(f"  =  {name}: already have {platform}")
            continue
        if args.verify:
            n = verify(platform, entry)
            if n == 0:
                print(f"  x  {name}: {platform} board {entry} returned no jobs — not added")
                continue
        data.setdefault(platform, []).append(entry)
        have[platform].add(key_of(platform, entry))
        added += 1
        print(f"  +  {name}: {platform} {entry}")

    if added:
        COMPANIES_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"\nAdded {added} board(s) to companies.json")


if __name__ == "__main__":
    main()

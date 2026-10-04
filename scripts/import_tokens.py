#!/usr/bin/env python3
"""Import confirmed board tokens from open-source aggregator repos."""

import json
import sys
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).resolve().parent.parent
COMPANIES_FILE = BASE_DIR / "config" / "companies.json"

SOURCES = {
    "greenhouse": "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/greenhouse_companies.json",
    "lever": "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/lever_companies.json",
    "ashby": "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/ashby_companies.json",
}


def main():
    with open(COMPANIES_FILE, encoding="utf-8") as f:
        existing = json.load(f)

    # Build set of existing tokens per platform
    existing_tokens = {}
    for platform in ["greenhouse", "lever", "ashby"]:
        tokens = set()
        for c in existing.get(platform, []):
            if isinstance(c, dict):
                tokens.add(c.get("token", "").lower())
            else:
                tokens.add(str(c).lower())
        existing_tokens[platform] = tokens

    total_added = 0
    client = httpx.Client(follow_redirects=True, timeout=30)

    for platform, url in SOURCES.items():
        print(f"\nFetching {platform} tokens...")
        try:
            r = client.get(url)
            r.raise_for_status()
            tokens = r.json()
        except Exception as e:
            print(f"  Error: {e}")
            continue

        if not isinstance(tokens, list):
            print(f"  Unexpected format: {type(tokens)}")
            continue

        new_tokens = [t for t in tokens if isinstance(t, str) and t.lower() not in existing_tokens[platform]]
        print(f"  Remote: {len(tokens)} | Already have: {len(existing_tokens[platform])} | New: {len(new_tokens)}")

        if platform not in existing:
            existing[platform] = []

        for t in new_tokens:
            existing[platform].append(t)
            existing_tokens[platform].add(t.lower())

        total_added += len(new_tokens)

    client.close()

    with open(COMPANIES_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)

    total = sum(len(v) for k, v in existing.items() if not k.startswith("_"))
    print(f"\nAdded {total_added} new companies")
    print(f"GRAND TOTAL: {total} companies across all platforms")


if __name__ == "__main__":
    main()

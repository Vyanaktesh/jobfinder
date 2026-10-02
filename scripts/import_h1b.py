#!/usr/bin/env python3
"""
Import H1B employer data into the SQLite database.

Usage:
  1. Download the USCIS H-1B Employer Data Hub file from:
     https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub
     (Click "H-1B Employer Data Hub Files" → download the CSV)

  2. OR download DOL OFLC LCA disclosure data from:
     https://www.dol.gov/agencies/eta/foreign-labor/performance
     (Select the most recent fiscal year H-1B data Excel file)

  3. Run this script:
     python scripts/import_h1b.py --file path/to/downloaded_file.csv
     python scripts/import_h1b.py --file path/to/downloaded_file.xlsx

The script auto-detects the file format and column names.
If no file is provided, it loads the built-in curated employer list plus
config/h1b_employers.txt (your editable sponsor list).

Merge more sponsors without wiping what's there:
  python scripts/import_h1b.py --list my_companies.txt        # one name per line
  python scripts/import_h1b.py --url "https://docs.google.com/spreadsheets/d/<ID>/edit#gid=0"
    (Google Sheet must be shared "anyone with the link"; first column or a
     column named Employer/Company/Name is used; optional count column)
Both MERGE into the existing table (use --replace to start from scratch).
"""

import argparse
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def normalize_employer(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|company|group|plc|lp|na|n\.a\.)\.?\b", "", name)
    name = re.sub(r"[.,;:'\"-]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def load_curated_employers() -> list[tuple[str, str, int]]:
    """Built-in list of known H1B sponsors with approximate annual counts."""
    employers = [
        # Big Tech
        ("Google LLC", 10000), ("Meta Platforms Inc", 5000), ("Amazon.com Services LLC", 8000),
        ("Microsoft Corporation", 7000), ("Apple Inc", 3000), ("Netflix Inc", 200),
        # Enterprise Tech
        ("Salesforce Inc", 2000), ("Oracle America Inc", 3000), ("IBM Corporation", 3000),
        ("SAP America Inc", 1500), ("ServiceNow Inc", 800), ("Workday Inc", 600),
        ("Snowflake Inc", 400), ("Databricks Inc", 500), ("MongoDB Inc", 300),
        # Fintech / Finance
        ("Stripe Inc", 400), ("Block Inc", 300), ("Robinhood Markets Inc", 200),
        ("Affirm Inc", 150), ("Brex Inc", 100), ("Marqeta Inc", 80),
        ("Chime Financial Inc", 100), ("Plaid Inc", 150), ("Ramp Business Corporation", 100),
        ("Visa Inc", 1500), ("Mastercard International", 1000),
        ("JPMorgan Chase", 4000), ("Goldman Sachs", 2000), ("Morgan Stanley", 1500),
        ("Bank of America", 2000), ("Capital One", 2000), ("Citigroup", 1500),
        # Consulting / Big 4
        ("Deloitte LLP", 5000), ("Ernst & Young LLP", 3000), ("PricewaterhouseCoopers LLP", 3000),
        ("KPMG LLP", 2000), ("Accenture LLP", 5000), ("McKinsey & Company Inc", 500),
        ("Boston Consulting Group", 400), ("Bain & Company", 300),
        ("Booz Allen Hamilton", 500), ("Slalom Consulting", 200),
        ("West Monroe Partners", 100), ("Huron Consulting Group", 200),
        ("FTI Consulting", 150), ("Protiviti Inc", 200), ("RSM US LLP", 300),
        # Growth Tech
        ("Airbnb Inc", 500), ("DoorDash Inc", 300), ("Lyft Inc", 200),
        ("Instacart", 150), ("Pinterest Inc", 200), ("Reddit Inc", 100),
        ("Discord Inc", 100), ("Spotify USA Inc", 300), ("Palantir Technologies", 400),
        ("Figma Inc", 100), ("Notion Labs Inc", 80), ("Asana Inc", 100),
        ("Gusto Inc", 80), ("Toast Inc", 150), ("Klaviyo Inc", 100),
        ("Samsara Inc", 200), ("Verkada Inc", 150), ("Flexport Inc", 100),
        ("GitLab Inc", 100), ("Elastic NV", 150), ("Postman Inc", 80),
        ("Amplitude Inc", 60), ("LaunchDarkly Inc", 40), ("Contentful Inc", 60),
        ("Squarespace Inc", 80), ("Twilio Inc", 300), ("Cloudflare Inc", 200),
        # Cyber
        ("CrowdStrike Inc", 300), ("Palo Alto Networks", 500), ("Zscaler Inc", 200),
        ("Fortinet Inc", 200), ("Okta Inc", 200), ("Tanium Inc", 100),
        ("Abnormal Security", 50), ("Axonius Inc", 30), ("Vanta Inc", 40),
        ("SentinelOne Inc", 100),
        # Federal / Defense
        ("Leidos Inc", 500), ("SAIC Inc", 400), ("CACI International", 300),
        ("Peraton Inc", 200), ("MITRE Corporation", 300),
        # Healthcare / Other
        ("CVS Health Corporation", 1000), ("Walt Disney Company", 500),
        ("Walmart Inc", 800), ("PwC", 3000),
        # OpenAI / AI
        ("OpenAI Inc", 200), ("Anthropic", 100),
        ("Deel Inc", 50), ("Linear Inc", 20), ("Supabase Inc", 20),
        ("Benchling Inc", 60), ("Carvana LLC", 100), ("Opendoor Technologies", 80),
        ("Relativity", 100), ("Cockroach Labs", 40), ("Faire Inc", 50),
        ("Airtable Inc", 60),
    ]

    return [(name, normalize_employer(name), count) for name, count in employers]


DEFAULT_LIST = PROJECT_ROOT / "config" / "h1b_employers.txt"
DEFAULT_COUNT = 10  # name-only entries count as established sponsors (GREEN)


def parse_name_list(text: str) -> list[tuple[str, str, int]]:
    """Parse 'Name' or 'Name,count' lines; '#' starts a comment."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, count = line, DEFAULT_COUNT
        m = re.match(r"^(.*),\s*(\d+)$", line)
        if m:
            name, count = m.group(1).strip(), int(m.group(2))
        norm = normalize_employer(name)
        if norm:
            out.append((name, norm, count))
    return out


def google_sheet_csv_url(url: str) -> str:
    """Turn a normal Google Sheets link into its CSV export link."""
    m = re.search(r"/spreadsheets/d/([\w-]+)", url)
    if not m:
        return url
    gid = re.search(r"gid=(\d+)", url)
    return (f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv"
            + (f"&gid={gid.group(1)}" if gid else ""))


def parse_csv_text(text: str) -> list[tuple[str, str, int]]:
    import csv, io
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    header = [c.strip().lower() for c in rows[0]]
    name_i, count_i, start = 0, None, 0
    for cand in ("employer", "employer_name", "company", "company name", "name", "petitioner name"):
        if cand in header:
            name_i, start = header.index(cand), 1
            break
    for cand in ("count", "petitions", "approvals", "total", "worker_count", "h1b count"):
        if cand in header:
            count_i = header.index(cand)
            break
    out = []
    for r in rows[start:]:
        if len(r) <= name_i or not r[name_i].strip():
            continue
        name = r[name_i].strip()
        count = DEFAULT_COUNT
        if count_i is not None and len(r) > count_i:
            digits = re.sub(r"[^\d]", "", r[count_i])
            if digits:
                count = max(int(digits), 1)
        out.append((name, normalize_employer(name), count))
    return [o for o in out if o[1]]


def merge_employers(db_path: Path, employers: list[tuple[str, str, int]], replace: bool = False):
    """Insert employers, skipping names already present (keeps the higher count)."""
    conn = sqlite3.connect(str(db_path))
    if replace:
        conn.execute("DELETE FROM h1b_employers")
    existing = dict(conn.execute(
        "SELECT employer_name_normalized, MAX(worker_count) FROM h1b_employers GROUP BY 1").fetchall())
    added = updated = 0
    for name, norm, count in employers:
        if norm in existing:
            if count > (existing[norm] or 0):
                conn.execute("UPDATE h1b_employers SET worker_count=? WHERE employer_name_normalized=?", (count, norm))
                existing[norm] = count
                updated += 1
            continue
        conn.execute(
            "INSERT INTO h1b_employers (employer_name, employer_name_normalized, case_status, fiscal_year, worker_count) "
            "VALUES (?, ?, 'Certified', 2025, ?)", (name, norm, count))
        existing[norm] = count
        added += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(DISTINCT employer_name_normalized) FROM h1b_employers").fetchone()[0]
    conn.close()
    print(f"Added {added} new, raised {updated} counts. {total} employers total in {db_path}")


def import_curated(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM h1b_employers")

    employers = load_curated_employers()
    conn.executemany(
        "INSERT INTO h1b_employers (employer_name, employer_name_normalized, case_status, fiscal_year, worker_count) VALUES (?, ?, 'Certified', 2025, ?)",
        employers,
    )
    conn.commit()
    print(f"Imported {len(employers)} curated employers into {db_path}")
    conn.close()
    if DEFAULT_LIST.exists():
        merge_employers(db_path, parse_name_list(DEFAULT_LIST.read_text()))


def import_csv(filepath: Path, db_path: Path):
    import pandas as pd

    print(f"Reading {filepath}...")
    if filepath.suffix in ('.xlsx', '.xls'):
        df = pd.read_excel(filepath)
    else:
        df = pd.read_csv(filepath, low_memory=False)

    print(f"Loaded {len(df)} rows, columns: {list(df.columns)[:10]}...")

    # Auto-detect column names (USCIS vs DOL format)
    employer_col = None
    for candidate in ['Employer', 'EMPLOYER_NAME', 'employer_name', 'Petitioner Name']:
        if candidate in df.columns:
            employer_col = candidate
            break

    count_col = None
    for candidate in ['Initial Approvals', 'Continuing Approvals', 'TOTAL_WORKER_POSITIONS', 'worker_count']:
        if candidate in df.columns:
            count_col = candidate
            break

    if not employer_col:
        print(f"ERROR: Could not find employer name column. Available columns: {list(df.columns)}")
        sys.exit(1)

    print(f"Using employer column: {employer_col}")
    if count_col:
        print(f"Using count column: {count_col}")

    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM h1b_employers")

    if count_col and 'Initial Approvals' in df.columns and 'Continuing Approvals' in df.columns:
        # USCIS format: aggregate by employer
        df['total'] = df['Initial Approvals'].fillna(0).astype(int) + df['Continuing Approvals'].fillna(0).astype(int)
        grouped = df.groupby(employer_col)['total'].sum().reset_index()
        records = [
            (row[employer_col], normalize_employer(str(row[employer_col])), int(row['total']))
            for _, row in grouped.iterrows()
            if row['total'] > 0
        ]
    elif count_col:
        grouped = df.groupby(employer_col)[count_col].sum().reset_index()
        records = [
            (row[employer_col], normalize_employer(str(row[employer_col])), int(row[count_col]))
            for _, row in grouped.iterrows()
            if row[count_col] > 0
        ]
    else:
        # Just count rows per employer
        grouped = df.groupby(employer_col).size().reset_index(name='count')
        records = [
            (row[employer_col], normalize_employer(str(row[employer_col])), int(row['count']))
            for _, row in grouped.iterrows()
        ]

    conn.executemany(
        "INSERT INTO h1b_employers (employer_name, employer_name_normalized, case_status, fiscal_year, worker_count) VALUES (?, ?, 'Certified', 2025, ?)",
        records,
    )
    conn.commit()
    print(f"Imported {len(records)} unique employers into {db_path}")
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Import H1B employer data")
    parser.add_argument("--file", type=Path, help="Path to USCIS CSV or DOL Excel file")
    parser.add_argument("--list", type=Path, help="Text file, one employer per line (optionally 'Name,count')")
    parser.add_argument("--url", help="Google Sheet / CSV URL of H1B employers (merged)")
    parser.add_argument("--replace", action="store_true", help="Wipe existing employers before importing")
    parser.add_argument("--db", type=Path, default=PROJECT_ROOT / "data" / "jobs.db")
    args = parser.parse_args()

    # Ensure DB exists with schema
    from src.db import JobDB
    db = JobDB(args.db)
    db.close()

    if args.url:
        import httpx
        r = httpx.get(google_sheet_csv_url(args.url), follow_redirects=True, timeout=60)
        r.raise_for_status()
        if "text/html" in r.headers.get("content-type", ""):
            sys.exit("Got an HTML page, not CSV — share the sheet as 'anyone with the link can view'.")
        merge_employers(args.db, parse_csv_text(r.text), replace=args.replace)
    elif args.list:
        merge_employers(args.db, parse_name_list(args.list.read_text()), replace=args.replace)
    elif args.file:
        import_csv(args.file, args.db)
    else:
        print("No file provided — loading curated employer list...")
        import_curated(args.db)


if __name__ == "__main__":
    main()

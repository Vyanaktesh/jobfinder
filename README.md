# Job Scraper

Job search pipeline that scrapes public ATS APIs, filters for relevant roles, flags H1B sponsors, and outputs a sortable HTML dashboard + markdown digest. No AI/LLM calls and no API keys needed.

## What it does

1. **Scrapes** 85+ companies across 6 ATS platforms (Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Workday)
2. **Filters** through 41 role lanes using fuzzy title matching (rapidfuzz), location filtering (US-only), seniority checks, exclusion rules, and sponsorship keyword blacklisting
3. **Deduplicates** across platforms using SHA-256 keys in SQLite
4. **Enriches** with H1B sponsorship likelihood (GREEN/YELLOW/RED) via employer fuzzy matching
5. **Outputs** a dark-themed HTML dashboard with client-side sorting/filtering, plus a markdown digest

## Quick Start

```bash
# Install dependencies (requires Python 3.11+)
uv venv --python 3.11
source .venv/bin/activate
uv pip install -r requirements.txt

# Create your personal config from the templates (these are gitignored)
cp config/applicant_profile.example.yml config/applicant_profile.yml
# ...then edit each with your own details.

# Import H1B employer data (curated list + config/h1b_employers.txt, ~370 companies)
python scripts/import_h1b.py

# Run the scraper
python -m src.main

# Run with options
python -m src.main --verbose                # Debug logging
python -m src.main --platform greenhouse    # Single platform only
```

Open `output/latest.html` in a browser to view the dashboard.

## Dashboard control server (optional)

The dashboard is a static file, so its **Run Pipeline** button needs a tiny local helper to act. Start it and leave it
running:

```bash
python scripts/control_server.py    # listens on http://localhost:8765
```

- **▶ Run Pipeline** — triggers a fresh scrape (via the
  `com.devansh.jobscraper` launchd job, so it survives terminal exit).

The buttons show a connection indicator and degrade gracefully when the server
isn't running.

> **Note on privacy:** `config/applicant_profile.yml`, `config/profile.yml`,
> `config/master_resume.yml`, the SQLite DB, and everything under `output/`
> (which embeds your contact details) are gitignored. Only `.example.yml`
> templates are tracked.

## Data Sources

| Platform | API Type | Companies | Notes |
|----------|----------|-----------|-------|
| Greenhouse | REST GET | 43 | Best coverage, simplest API |
| Lever | REST GET | 2 | Mid-market tech |
| Ashby | REST GET | 10 | Growing among startups |
| SmartRecruiters | REST GET (paginated) | 2 | 10 req/sec limit |
| Workable | POST (token-paginated) | 0* | Built but no verified companies yet |
| Workday | POST (offset-paginated) | 9 | Big 4, F500, defense. Datacenters wd1-wd12 |

All APIs are free, public, and require no authentication.

## Configuration

All config is in `config/` and fully editable:

- **`companies.json`** — Seed companies by ATS platform. Each entry has a board token/slug and optional settings.
- **`role_lanes.json`** — 41 role lanes with canonical titles, required keywords, boost/negative keywords, and fuzzy match thresholds.
- **`settings.toml`** — Scraping delays, filter thresholds, H1B thresholds, output paths.
- **`sponsorship_blacklist.txt`** — Phrases in job descriptions that indicate no sponsorship (e.g., "unable to sponsor", "US citizens only").

### Adding a company

Add an entry to the appropriate platform array in `companies.json`:

```json
// Greenhouse
{"token": "airbnb"}

// Workday
{"tenant": "boozallen", "wd": "wd1", "site": "Careers"}
```

### Role lanes

Each lane in `role_lanes.json` has:
- `canonical_titles` — fuzzy-matched against job titles (token_set_ratio >= 75)
- `required_keywords` — must appear in title or first 500 chars of description
- `title_must_contain_any` (optional) — at least one must appear in the title itself
- `negative_keywords` — instant reject if found in title
- `boost_keywords` — raise match score above threshold

## Filter Pipeline

Jobs pass through 5 sequential filters:

1. **Date** — Posted within last 14 days (configurable)
2. **Location** — US-based or US-remote only
3. **Title match** — Fuzzy match against 41 role lanes, with seniority cap (rejects Staff/Principal/Director/VP+)
4. **Exclusion rules** — Rejects 7+ YOE requirements, senior-only roles, pure engineering/sales/clinical titles, internships
5. **Sponsorship blacklist** — Rejects if description contains "no sponsorship" etc.

## H1B Sponsorship Enrichment

Each matched job is enriched with an H1B sponsorship flag:

- **GREEN** — Employer filed 10+ H1B petitions (likely sponsors)
- **YELLOW** — Filed 1-9 petitions (possible sponsor)
- **RED** — No match found or 0 petitions

The curated list covers 105 major employers. For better coverage, import real USCIS/DOL data:

```bash
# USCIS H-1B Employer Data Hub CSV
python scripts/import_h1b.py --file path/to/uscis_data.csv

# DOL OFLC LCA disclosure Excel
python scripts/import_h1b.py --file path/to/h1b_disclosure.xlsx
```

Jobs from companies with no H1B record are kept (flagged RED) by default. Optionally set `only_sponsors = true`
under `[h1b]` in `config/settings.toml` to drop them at scrape time. Run `python scripts/h1b_coverage.py` to see which
sponsors still have no job board configured.

### Finding job boards for your H1B sponsors

A sponsor only produces jobs if it has a board in `config/companies.json`. Auto-discover them:

```bash
python scripts/h1b_coverage.py                  # which sponsors have no board yet
python scripts/find_h1b_boards.py --dry-run     # probe Greenhouse/Lever/Ashby (needs internet)
python scripts/find_h1b_boards.py               # same, and add the hits to companies.json
```

Sponsors with no Greenhouse/Lever/Ashby board (usually Workday/iCIMS companies) are listed in
`output/h1b_no_board_found.txt` — add those by hand (see "Adding a company").

### Adding more H1B sponsors (e.g. a Google Sheet list)

```bash
# Share the sheet as "anyone with the link can view", then:
python scripts/import_h1b.py --url "https://docs.google.com/spreadsheets/d/<ID>/edit#gid=0"
# or a plain text file, one company per line ("Name" or "Name,count"):
python scripts/import_h1b.py --list my_companies.txt
```

Imports are additive (use `--replace` to reset). Name-only entries count as GREEN sponsors.
You can also just append names to `config/h1b_employers.txt`.

## Dashboard

The HTML dashboard (`output/latest.html`) features:
- Dark theme with sponsorship color-coded borders (green/yellow/red)
- Text search across all fields
- Dropdown filters: role lane, sponsorship flag, ATS platform (sorted H1B sponsors first, but jobs from companies with no H1B record are still shown; tick "H1B-friendly only" to hide them)
- Toggle: NEW jobs only, Rotational Programs only
- Sortable columns: company, title, lane, location, score, posted date
- Direct apply links
- Tabs: All / Full-Time / Internships / Saved (★) / Applied, with live counts
- Extra filters: posted date, H1B-friendly only, reset filters, `/` to search
- Applied tracking with undo + Export Applied CSV

## Scheduling (macOS)

Run every 12 hours via cron:

```bash
# See the crontab line to add
./scripts/run_cron.sh --install

# Then add it:
crontab -e
# Paste the line (runs at 7 AM and 7 PM)
```

Logs go to `logs/scraper.log` (rotating, 5 MB x 10 files) and `logs/cron.log`.

## Project Structure

```
job-scraper/
  config/          # Editable config files
  src/
    scrapers/      # 6 ATS platform scrapers
    filters/       # Title, location, exclusion, sponsorship filters
    h1b/           # H1B employer lookup
    output/        # Dashboard + digest generators
    main.py        # Entry point
    models.py      # RawJob / FilteredJob dataclasses
    db.py          # SQLite schema + queries
  templates/       # Jinja2 HTML template
  static/          # CSS + JS for dashboard
  scripts/         # H1B import, cron runner
  output/          # Generated dashboards + digests
  logs/            # Rotating log files
  data/            # SQLite database
```

## Typical Run Output

- ~15,000 raw jobs fetched across 85 companies
- ~230 pass all filters
- Dashboard + digest generated in `output/`
- Full run takes ~10 minutes (rate-limited API calls)

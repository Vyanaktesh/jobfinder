"""iCIMS scraper.

iCIMS is widely used by federal contractors (CACI, ManTech, Peraton, ICF, etc.)
and healthcare/hospitality. Each tenant lives at `careers-{tenant}.icims.com`
(or sometimes another subdomain) and exposes a public HTML job list at
`/jobs/search?ss=1&pr={page}&in_iframe=1` (50 jobs per page) plus per-job
detail pages at `/jobs/{id}/{slug}/job?in_iframe=1`.

The HTML is parsed with BeautifulSoup because iCIMS does not expose JSON.

Company config (in companies.json under "icims"):
  {"tenant": "careers-peraton", "name": "Peraton"}
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup

from src.models import RawJob
from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

PAGE_SIZE = 50
MAX_PAGES = 30  # cap at 1500 postings per company
DETAIL_CONCURRENCY = 6
DETAIL_DELAY = 0.3

# Cheap title pre-filter (same idea as Workday). Federal contractors post a
# LOT of cleared-only jobs — we don't bother fetching descriptions for those.
TITLE_PREFILTER_TOKENS = {
    "analyst", "analytics", "data", "business", "strategy", "operations",
    "consultant", "consulting", "associate", "intelligence", "reporting",
    "cyber", "security", "risk", "compliance", "grc", "audit",
    "program", "project", "product", "implementation", "transformation",
    "process", "rotation", "rotational", "finance", "planner", "planning",
    "coordinator", "manager", "engineer",
    "financ", "pricing", "revenue", "fp&a", "budget", "forecast",
}

# Skip jobs gated behind clearances we don't have
CLEARANCE_BLACKLIST = re.compile(
    r"\b(ts/sci|top\s*secret|sci\s+poly|polygraph|active\s+clearance|"
    r"secret\s+clearance|dod\s+clearance|fs\s+poly|ci\s+poly|"
    r"current\s+clearance|interim\s+secret)\b",
    re.IGNORECASE,
)


class IcimsScraper(BaseScraper):
    platform = "icims"
    base_delay = 1.5

    async def fetch_jobs(self, company: dict) -> list[RawJob]:
        tenant = company["tenant"]
        name = company.get("name", tenant)
        base_url = f"https://{tenant}.icims.com"

        all_jobs: list[RawJob] = []
        for page in range(MAX_PAGES):
            url = f"{base_url}/jobs/search?ss=1&pr={page}&in_iframe=1"
            html = await self._fetch_text(url)
            if not html:
                break
            page_jobs = self._parse_list_html(html, base_url, tenant, name)
            if not page_jobs:
                break
            all_jobs.extend(page_jobs)
            if len(page_jobs) < PAGE_SIZE:
                break  # last page
            await self.throttle()

        logger.info(f"[icims] {name}: {len(all_jobs)} postings (listing)")

        # Enrich descriptions for plausibly-relevant titles
        to_enrich = [j for j in all_jobs if self._title_looks_relevant(j.title)]
        if to_enrich:
            logger.info(
                f"[icims] {name}: fetching descriptions for {len(to_enrich)}/{len(all_jobs)} relevant titles"
            )
            await self._enrich_descriptions(to_enrich, base_url)

            # Drop jobs that turn out to require active clearance
            cleared_out = []
            for j in all_jobs:
                if j.description_text and CLEARANCE_BLACKLIST.search(j.description_text[:3000]):
                    cleared_out.append(j)
            if cleared_out:
                logger.info(f"[icims] {name}: dropping {len(cleared_out)} clearance-required jobs")
                cleared_set = {id(j) for j in cleared_out}
                all_jobs = [j for j in all_jobs if id(j) not in cleared_set]

        return all_jobs

    async def _fetch_text(self, url: str) -> str | None:
        """GET a URL and return the text body (None on failure)."""
        client = await self.get_client()
        try:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 JobScraper/1.0"})
            if resp.status_code == 200:
                return resp.text
            if resp.status_code == 404:
                return None
            logger.warning(f"[icims] HTTP {resp.status_code} for {url}")
        except Exception as e:
            logger.warning(f"[icims] fetch error for {url}: {e}")
        return None

    def _parse_list_html(self, html: str, base_url: str, tenant: str, name: str) -> list[RawJob]:
        """Parse iCIMS search-results HTML into RawJob objects (no description yet).

        iCIMS structures each job as a `div.row.iCIMS_JobCardItem` containing
        an <a class="iCIMS_Anchor"> with the title and adjacent labeled fields
        for Location (e.g., "US-MO-Saint Louis"), ID, Description, etc.
        """
        soup = BeautifulSoup(html, "lxml")
        results: list[RawJob] = []
        seen_ids: set[str] = set()

        # Each job card is a div.row containing an iCIMS_Anchor
        for row in soup.select("div.row"):
            anchor = row.select_one("a.iCIMS_Anchor[href*='/jobs/']")
            if not anchor:
                continue
            href = anchor.get("href", "")
            m = re.search(r"/jobs/(\d+)/([a-zA-Z0-9_-]+)/job", href)
            if not m:
                continue
            job_id, slug = m.group(1), m.group(2)
            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            # Pull the title from the anchor's <h3> if present (cleaner) — otherwise the
            # anchor text contains the sr-only label "External Job Posting Title".
            h3 = anchor.find("h3")
            title = (h3.get_text(strip=True) if h3 else anchor.get_text(strip=True)) or slug.replace("-", " ").title()
            title = re.sub(r"^(External Job Posting Title|Title)\s*", "", title).strip()

            # Extract location from the row text. iCIMS uses several formats:
            #   "US-MO-Saint Louis"  (Stifel)
            #   "US-OR-Home"         (Peraton remote-in-state)
            #   "US"                 (Peraton US-anywhere)
            #   "US-Remote"
            row_text = row.get_text(" ", strip=True)
            location = ""
            # Prefer most-specific: state + city
            loc_m = re.search(r"\bUS-([A-Z]{2})-([A-Za-z][A-Za-z .'\-]+)", row_text)
            if loc_m:
                state, city = loc_m.group(1), loc_m.group(2).strip()
                if city.lower() in ("home", "remote", "anywhere"):
                    location = f"Remote, {state}, US"
                else:
                    location = f"{city}, {state}, US"
            elif re.search(r"\bUS[-\s]Remote\b", row_text):
                location = "Remote, US"
            elif re.search(r"\bUS\b", row_text[:200]):
                # Just "US" with no state — treat as US-wide
                location = "United States"

            apply_url = href if href.startswith("http") else f"{base_url}{href}"

            results.append(
                RawJob(
                    source_platform=self.platform,
                    company_token=tenant,
                    company_name=name,
                    external_id=job_id,
                    title=title,
                    location_raw=self._normalize_location(location),
                    description_html="",
                    description_text="",
                    posted_at=None,
                    apply_url=apply_url,
                    metadata={"slug": slug},
                )
            )

        return results

    @staticmethod
    def _extract_field(row, label: str) -> str:
        """Pull the value following a label like 'Location' from an iCIMS row."""
        # Find any element with text exactly matching the label, then grab the next
        # sibling's text. iCIMS uses span.field-label or sr-only spans.
        for el in row.find_all(string=re.compile(rf"^\s*{label}\s*$", re.I)):
            parent = el.parent
            if parent is None:
                continue
            # The value is typically the next sibling text node or in the parent's next sibling
            nxt = parent.find_next_sibling()
            if nxt:
                return nxt.get_text(" ", strip=True)
            # Fallback: take parent's text minus the label
            txt = parent.get_text(" ", strip=True)
            return re.sub(rf"^{label}\s*", "", txt).strip()
        return ""

    @staticmethod
    def _normalize_location(raw: str) -> str:
        """Convert iCIMS 'US-MO-Saint Louis' format to 'Saint Louis, MO, US'."""
        if not raw:
            return ""
        parts = raw.split("-")
        if len(parts) >= 3 and parts[0].strip().upper() == "US":
            return f"{parts[2].strip()}, {parts[1].strip()}, US"
        return raw

    @staticmethod
    def _title_looks_relevant(title: str) -> bool:
        t = (title or "").lower()
        return any(tok in t for tok in TITLE_PREFILTER_TOKENS)

    async def _enrich_descriptions(self, jobs: list[RawJob], base_url: str) -> None:
        """Concurrently fetch each job's detail HTML, parse out the description."""
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def enrich_one(job: RawJob):
            async with sem:
                slug = job.metadata.get("slug", "")
                url = f"{base_url}/jobs/{job.external_id}/{slug}/job?in_iframe=1"
                html = await self._fetch_text(url)
                if html:
                    self._populate_description(job, html)
                await asyncio.sleep(DETAIL_DELAY)

        await asyncio.gather(*(enrich_one(j) for j in jobs), return_exceptions=True)

    def _populate_description(self, job: RawJob, html: str) -> None:
        """Extract description body + posted date from a job detail page."""
        soup = BeautifulSoup(html, "lxml")

        # iCIMS uses specific div IDs / classes for the description
        desc_node = (
            soup.select_one("#iCIMS_JobBody")
            or soup.select_one(".iCIMS_JobContent")
            or soup.select_one(".iCIMS_Job_Description")
            or soup.find("div", {"id": re.compile(r"jobOverview|jobDescription", re.I)})
        )
        if desc_node:
            job.description_html = str(desc_node)
            job.description_text = desc_node.get_text(separator=" ", strip=True)
        else:
            # Fallback: grab main body
            body = soup.find("body")
            if body:
                job.description_text = body.get_text(separator=" ", strip=True)[:4000]

        # Try to grab a posted date if present
        date_node = soup.find(string=re.compile(r"Posted Date|Date Posted|Posted on", re.I))
        if date_node and date_node.parent:
            date_text = date_node.parent.get_text(" ", strip=True)
            m = re.search(r"(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})", date_text)
            if m:
                try:
                    s = m.group(1)
                    job.posted_at = datetime.strptime(s, "%m/%d/%Y") if "/" in s else datetime.strptime(s, "%Y-%m-%d")
                except ValueError:
                    pass

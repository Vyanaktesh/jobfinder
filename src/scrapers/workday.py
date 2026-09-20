from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from bs4 import BeautifulSoup

from src.models import RawJob
from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# Cheap title pre-filter: only enrich postings whose titles contain any of these
# tokens. Saves thousands of detail-page fetches on companies like Disney that
# have 1000+ postings, most of which won't title-match downstream anyway.
TITLE_PREFILTER_TOKENS = {
    "analyst", "analytics", "data", "business", "strategy", "operations",
    "consultant", "consulting", "associate", "intelligence", "reporting",
    "cyber", "security", "risk", "compliance", "grc", "audit",
    "program", "project", "product", "implementation", "transformation",
    "process", "rotation", "rotational", "leadership development", "lcap",
    "finance", "supply", "planner", "planning", "manager", "coordinator",
    "financ", "pricing", "revenue", "fp&a", "budget", "forecast",
}

# Concurrency for detail-page (description) fetches per company.
DETAIL_CONCURRENCY = 8
DETAIL_DELAY = 0.4


class WorkdayScraper(BaseScraper):
    platform = "workday"
    base_delay = 2.5  # conservative for Workday listing pagination

    async def fetch_jobs(self, company: dict) -> list[RawJob]:
        tenant = company["tenant"]
        site = company["site"]
        wd = company.get("wd", "wd1")
        name = company.get("name", tenant)

        base_url = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"

        all_postings = []
        offset = 0
        limit = 20
        max_pages = 50  # cap at 1000 postings per company

        while True:
            body = {
                "appliedFacets": {},
                "searchText": "",
                "limit": limit,
                "offset": offset,
            }

            data = await self.fetch_with_retry(base_url, method="POST", json_body=body)
            if not data:
                break

            postings = data.get("jobPostings", [])
            if not postings:
                break

            all_postings.extend(postings)
            total = data.get("total", 0)
            offset += limit

            if offset >= total or offset >= limit * max_pages:
                break
            await self.throttle()

        logger.info(f"[workday] {name}: {len(all_postings)} postings (listing)")
        results: list[RawJob] = []

        for j in all_postings:
            try:
                title = j.get("title", "")
                external_path = j.get("externalPath", "")
                location_raw = j.get("locationsText", "") or ""
                posted_on = j.get("postedOn", "")

                posted_at = None
                if posted_on:
                    try:
                        posted_at = datetime.strptime(posted_on, "%Y-%m-%dT%H:%M:%S%z")
                    except (ValueError, TypeError):
                        try:
                            posted_at = datetime.strptime(posted_on[:10], "%Y-%m-%d")
                        except (ValueError, TypeError):
                            pass

                apply_url = f"https://{tenant}.{wd}.myworkdayjobs.com/en-US/{site}{external_path}"

                bullet_fields = j.get("bulletFields", [])

                raw = RawJob(
                    source_platform=self.platform,
                    company_token=tenant,
                    company_name=name,
                    external_id=external_path,
                    title=title,
                    location_raw=location_raw,
                    description_html="",
                    description_text="",
                    posted_at=posted_at,
                    apply_url=apply_url,
                    metadata={"bulletFields": bullet_fields},
                )
                results.append(raw)
            except Exception as e:
                logger.error(f"[workday] Error parsing job from {name}: {e}")

        # --- Enrich descriptions for plausibly-relevant titles ---
        # Workday listing API doesn't include the description body; we must
        # call the per-job detail endpoint to get it. We pre-filter on title
        # tokens so we don't fetch 1000 detail pages per company.
        to_enrich = [r for r in results if self._title_looks_relevant(r.title)]
        if to_enrich:
            logger.info(
                f"[workday] {name}: fetching descriptions for {len(to_enrich)}/{len(results)} relevant titles"
            )
            await self._enrich_descriptions(to_enrich, tenant, site, wd)

        return results

    @staticmethod
    def _title_looks_relevant(title: str) -> bool:
        """Cheap title token pre-screen — saves detail fetches on irrelevant roles."""
        t = (title or "").lower()
        return any(tok in t for tok in TITLE_PREFILTER_TOKENS)

    async def _enrich_descriptions(
        self, jobs: list[RawJob], tenant: str, site: str, wd: str
    ) -> None:
        """Concurrently fetch description bodies for a batch of jobs."""
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def enrich_one(job: RawJob):
            async with sem:
                detail_url = (
                    f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/"
                    f"{tenant}/{site}{job.external_id}"
                )
                try:
                    data = await self.fetch_with_retry(detail_url)
                    if data:
                        desc_html = (
                            data.get("jobPostingInfo", {}).get("jobDescription", "")
                            or ""
                        )
                        if desc_html:
                            job.description_html = desc_html
                            job.description_text = BeautifulSoup(
                                desc_html, "lxml"
                            ).get_text(separator=" ", strip=True)
                except Exception as e:
                    logger.warning(
                        f"[workday] description fetch failed for {job.title}: {e}"
                    )
                await asyncio.sleep(DETAIL_DELAY)

        await asyncio.gather(*(enrich_one(j) for j in jobs), return_exceptions=True)

    async def enrich_description(self, job: RawJob, company: dict) -> RawJob:
        """Single-job enrichment (kept for backward compatibility / manual use)."""
        tenant = company["tenant"]
        site = company["site"]
        wd = company.get("wd", "wd1")

        detail_url = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{job.external_id}"
        data = await self.fetch_with_retry(detail_url)
        if data:
            desc_html = data.get("jobPostingInfo", {}).get("jobDescription", "") or ""
            job.description_html = desc_html
            job.description_text = BeautifulSoup(desc_html, "lxml").get_text(separator=" ", strip=True)
        return job

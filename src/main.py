from __future__ import annotations

import argparse
import asyncio
import json
import logging
import logging.handlers
import os
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from src.db import JobDB
from src.filters.exclusion_filter import check_exclusions
from src.filters.internship import is_internship_title
from src.filters.location_filter import is_us_location
from src.filters.sponsorship import SponsorshipFilter
from src.filters.title_matcher import TitleMatcher
from src.h1b.lookup import H1BLookup
from src.models import FilteredJob, RawJob
from src.evaluator.scorer import (
    AIScorer,
    get_all_evaluated_jobs,
    get_unevaluated_jobs,
    init_eval_schema,
    save_evaluations,
)
from src.learning.learned_rules import LearnedRulesEngine
from src.learning.feedback_import import import_feedback_file
from src.apply.bookmarklet import generate_fill_js
from src.apply.profile import load_profile
from src.output.csv_export import export_csv
from src.resume.generate import generate_resumes
from src.output.dashboard import render_dashboard
from src.output.digest import render_digest
from src.scrapers.ashby import AshbyScraper
from src.scrapers.greenhouse import GreenhouseScraper
from src.scrapers.icims import IcimsScraper
from src.scrapers.lever import LeverScraper
from src.scrapers.smartrecruiters import SmartRecruitersScraper
from src.scrapers.workable import WorkableScraper
from src.scrapers.workday import WorkdayScraper

console = Console()
BASE_DIR = Path(os.getenv("JOBSCRAPER_BASE", Path(__file__).resolve().parent.parent))
CONFIG_DIR = BASE_DIR / "config"


def setup_logging(verbose: bool = False):
    """Configure console (rich) and rotating file logging."""
    level = logging.DEBUG if verbose else logging.INFO
    headless = os.getenv("JOBSCRAPER_HEADLESS") == "1"

    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "scraper.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    )

    handlers = [file_handler]
    if not headless:
        console_handler = RichHandler(console=console, rich_tracebacks=True)
        console_handler.setLevel(level)
        handlers.append(console_handler)

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(message)s",
        datefmt="[%X]",
        handlers=handlers,
    )


def validate_environment(require_ai: bool = True):
    """Validate required environment variables before expensive operations.

    Raises EnvironmentError with a clear message listing missing keys.
    Finding #1: Prevents running scrapers for hours only to discover
    the API key is missing during AI evaluation.
    """
    issues = []
    if require_ai:
        key = os.getenv("ANTHROPIC_API_KEY", "")
        if not key:
            issues.append("ANTHROPIC_API_KEY not set (needed for AI evaluation)")
        elif len(key) < 20:
            issues.append("ANTHROPIC_API_KEY looks invalid (too short)")
    if issues:
        raise EnvironmentError(
            "Environment validation failed:\n  - " + "\n  - ".join(issues)
            + "\n\nSet in .env file or export as environment variable."
        )


def load_config():
    """Load settings.toml and companies.json from config directory."""
    with open(CONFIG_DIR / "settings.toml", "rb") as f:
        settings = tomllib.load(f)
    with open(CONFIG_DIR / "companies.json") as f:
        companies = json.load(f)
    return settings, companies


SCRAPERS = {
    "greenhouse": GreenhouseScraper,
    "lever": LeverScraper,
    "ashby": AshbyScraper,
    "smartrecruiters": SmartRecruitersScraper,
    "workable": WorkableScraper,
    "workday": WorkdayScraper,
    "icims": IcimsScraper,
}


async def scrape_platform(
    platform: str,
    companies: list[dict],
    delay: float,
    concurrency: int = 5,
) -> list[RawJob]:
    """Scrape jobs from a single ATS platform across multiple companies.

    Uses an asyncio Semaphore to limit concurrent requests per platform.
    All scraper httpx clients are tracked and cleaned up in a finally block
    to prevent resource leaks (Finding #5).

    Args:
        platform: ATS platform key (e.g., 'greenhouse', 'lever').
        companies: List of company config dicts with platform-specific keys.
        delay: Seconds to wait between requests.
        concurrency: Max concurrent scraping tasks.

    Returns:
        List of RawJob objects from all companies on this platform.
    """
    scraper_cls = SCRAPERS.get(platform)
    if not scraper_cls:
        logging.getLogger(__name__).warning(f"No scraper for platform: {platform}")
        return []

    sem = asyncio.Semaphore(concurrency)
    all_jobs: list[RawJob] = []
    lock = asyncio.Lock()
    errors = 0
    # Track all scraper instances for cleanup (Finding #5)
    scrapers: list = []
    scrapers_lock = asyncio.Lock()

    async def scrape_one(company):
        nonlocal errors
        async with sem:
            scraper = scraper_cls(delay=delay)
            async with scrapers_lock:
                scrapers.append(scraper)
            try:
                jobs = await scraper.fetch_jobs(company)
                async with lock:
                    all_jobs.extend(jobs)
            except Exception as e:
                logging.getLogger(__name__).error(f"[{platform}] Error scraping {company}: {e}")
                errors += 1
            finally:
                await scraper.close()
            await asyncio.sleep(delay)

    try:
        tasks = [scrape_one(c) for c in companies]
        done = 0
        batch_size = 50
        for i in range(0, len(tasks), batch_size):
            batch = tasks[i:i + batch_size]
            await asyncio.gather(*batch)
            done += len(batch)
            if done % 200 == 0 or done == len(tasks):
                logging.getLogger(__name__).info(
                    f"[{platform}] Progress: {done}/{len(companies)} companies scraped"
                )
    finally:
        # Ensure ALL scraper clients are closed even on cancellation (Finding #5)
        cleanup = [s.close() for s in scrapers if s._client and not s._client.is_closed]
        if cleanup:
            await asyncio.gather(*cleanup, return_exceptions=True)

    logging.getLogger(__name__).info(
        f"[{platform}] Done: {len(all_jobs)} jobs from {len(companies)} companies ({errors} errors)"
    )
    return all_jobs


def apply_filters(
    jobs: list[RawJob],
    title_matcher: TitleMatcher,
    sponsorship_filter: SponsorshipFilter,
    max_age_days: int,
    db: JobDB,
    run_id: int,
) -> list[FilteredJob]:
    """Apply the full filter pipeline to raw jobs.

    Pipeline stages:
        1. Date filter — reject postings older than max_age_days
        2. Location filter — US-only (with remote detection)
        3. Title match — fuzzy match against configured role lanes
        4. Exclusion rules — seniority, YOE, sales/clinical/trades
        5. Sponsorship keyword filter — reject anti-sponsorship language

    Args:
        jobs: Raw jobs from scrapers.
        title_matcher: Configured TitleMatcher instance.
        sponsorship_filter: Configured SponsorshipFilter instance.
        max_age_days: Maximum posting age in days.
        db: Database instance for logging rejections.
        run_id: Current run ID for filter log association.

    Returns:
        List of FilteredJob objects that passed all filters.
    """
    logger = logging.getLogger(__name__)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max_age_days)
    results = []
    stats = {"total": len(jobs), "no_date": 0, "too_old": 0, "non_us": 0,
             "no_title_match": 0, "excluded": 0, "no_sponsorship": 0, "passed": 0}

    for job in jobs:
        # 1. Date filter
        if job.posted_at:
            if job.posted_at.tzinfo:
                posted_aware = job.posted_at
            else:
                # Finding #8: Log naive datetimes instead of silently assuming UTC
                logger.debug(
                    f"Naive datetime for {job.company_name}/{job.title} "
                    f"({job.source_platform}), assuming UTC"
                )
                posted_aware = job.posted_at.replace(tzinfo=timezone.utc)
            if posted_aware < cutoff:
                stats["too_old"] += 1
                continue
        # Allow jobs without dates through (first-seen fallback)

        # 2. Location filter
        is_us, loc_parsed = is_us_location(job.location_raw)
        if not is_us:
            stats["non_us"] += 1
            db.log_filter_rejection(run_id, job.company_name, job.title, "location", f"non-US: {job.location_raw}")
            continue

        # 3. Title match
        match_result = title_matcher.match(job)
        if not match_result:
            stats["no_title_match"] += 1
            continue

        lane, score, is_rotational = match_result
        is_intern = is_internship_title(job.title)

        # 4. Exclusion rules
        exclusion = check_exclusions(job)
        if exclusion:
            stats["excluded"] += 1
            db.log_filter_rejection(run_id, job.company_name, job.title, "exclusion", exclusion, job.apply_url)
            continue

        # 5. Sponsorship keyword filter — skipped for internships. Internships
        # run on CPT/OPT, not H1B sponsorship, so "no visa sponsorship" language
        # in a JD (aimed at full-time conversion) doesn't disqualify the internship.
        if not is_intern:
            sp_phrase = sponsorship_filter.check(job.description_text)
            if sp_phrase:
                stats["no_sponsorship"] += 1
                db.log_filter_rejection(run_id, job.company_name, job.title, "sponsorship", f"matched: '{sp_phrase}'", job.apply_url)
                continue

        filtered = FilteredJob.from_raw(
            job,
            matched_lane=lane,
            match_score=score,
            is_rotational=is_rotational,
            is_internship=is_intern,
            location_parsed=loc_parsed,
        )
        results.append(filtered)
        stats["passed"] += 1

    logger.info(f"Filter stats: {json.dumps(stats, indent=2)}")
    # Finding #17: Persist filter stats to database
    db.save_filter_stats(run_id, stats)
    return results


async def run(platform_filter: str | None = None, verbose: bool = False,
              skip_ai: bool = False, generate_resumes_flag: bool = False):
    """Main pipeline: scrape -> filter -> dedupe -> evaluate -> output.

    Args:
        platform_filter: If set, only scrape this single platform.
        verbose: Enable debug logging.
        skip_ai: Skip AI evaluation step (useful for testing scrape-only).
        generate_resumes_flag: Generate tailored resume PDFs for top jobs.
    """
    setup_logging(verbose)
    logger = logging.getLogger(__name__)

    # Finding #1: Validate environment before expensive operations
    if not skip_ai:
        try:
            validate_environment(require_ai=True)
        except EnvironmentError as e:
            logger.warning(f"Environment check: {e}")
            logger.warning("Continuing without AI evaluation")
            skip_ai = True

    settings, companies = load_config()
    delay = settings["scraping"]["request_delay_seconds"]
    concurrency = settings["scraping"].get("max_concurrent_per_platform", 5)
    max_age = settings["filtering"]["max_post_age_days"]

    title_matcher = TitleMatcher(CONFIG_DIR / "role_lanes.json")
    sponsorship_filter = SponsorshipFilter(CONFIG_DIR / "sponsorship_blacklist.txt")
    h1b_lookup = H1BLookup(
        BASE_DIR / settings["output"]["db_path"],
        green_threshold=settings["h1b"]["green_threshold"],
        yellow_threshold=settings["h1b"]["yellow_threshold"],
    )

    db = JobDB(BASE_DIR / settings["output"]["db_path"])
    run_id = db.start_run()

    logger.info("Starting job scraper...")

    # Map of platform -> key name expected by each scraper
    PLATFORM_KEY = {
        "greenhouse": "token",
        "lever": "site",
        "ashby": "board",
        "smartrecruiters": "id",
        "workable": "subdomain",
        "workday": None,
        "icims": "tenant",
    }

    all_jobs: list[RawJob] = []
    errors: list[str] = []
    for platform, company_list in companies.items():
        if platform.startswith("_"):
            continue
        if platform_filter and platform != platform_filter:
            continue
        if not company_list:
            continue

        key = PLATFORM_KEY.get(platform)
        normalized = []
        for c in company_list:
            if isinstance(c, str) and key:
                normalized.append({key: c, "name": c})
            else:
                normalized.append(c)

        logger.info(f"Scraping {platform}: {len(normalized)} companies")
        try:
            jobs = await scrape_platform(platform, normalized, delay, concurrency)
            all_jobs.extend(jobs)
        except Exception as e:
            error_msg = f"[{platform}] Platform error: {e}"
            logger.error(error_msg)
            errors.append(error_msg)

    logger.info(f"Total raw jobs fetched: {len(all_jobs)}")

    # Apply filters
    filtered = apply_filters(all_jobs, title_matcher, sponsorship_filter, max_age, db, run_id)

    # H1B enrichment — internships get an "NA" flag instead of GREEN/YELLOW/RED,
    # since they don't require H1B sponsorship (CPT/OPT covers them).
    for job in filtered:
        if job.is_internship:
            job.sponsorship_flag = "NA"
            job.h1b_count = None
        else:
            flag, count = h1b_lookup.lookup(job.company_name)
            job.sponsorship_flag = flag
            job.h1b_count = count

    # Dedup and store with periodic commits (Finding #6: checkpointing)
    new_count = 0
    seen_keys = set()
    for i, job in enumerate(filtered):
        seen_keys.add(job.dedup_key)
        is_new = db.upsert_job(job)
        if is_new:
            new_count += 1
            job.is_new = True
        # Commit every 100 jobs so progress survives crashes
        if (i + 1) % 100 == 0:
            db.commit()

    db.finish_run(run_id, len(all_jobs), len(filtered), new_count, errors)

    # Mark jobs not seen in this run as inactive
    if seen_keys:
        db.mark_inactive_not_seen(seen_keys)

    # Expire old postings
    expired = db.expire_old_jobs(max_age)
    if expired:
        logger.info(f"Expired {expired} jobs older than {max_age} days")

    logger.info(f"Passed filters: {len(filtered)} | New: {new_count}")

    # Display results (skip Rich table when running headless)
    if os.getenv("JOBSCRAPER_HEADLESS") != "1":
        table = Table(title=f"Filtered Jobs ({len(filtered)} total, {new_count} new)")
        table.add_column("Company", style="cyan", max_width=18)
        table.add_column("Title", style="white", max_width=35)
        table.add_column("Lane", style="yellow", max_width=22)
        table.add_column("Location", style="green", max_width=20)
        table.add_column("Score", style="magenta", justify="right")
        table.add_column("Flags", style="red")

        for job in filtered:
            flags = []
            if job.is_new:
                flags.append("NEW")
            if job.is_rotational:
                flags.append("PROGRAM")
            table.add_row(
                job.company_name,
                job.title[:35],
                job.matched_lane,
                job.location_parsed[:20],
                f"{job.match_score:.0f}",
                " ".join(flags),
            )

        console.print(table)

        # Show filter rejection summary
        rejection_rows = db.conn.execute(
            "SELECT filter_stage, COUNT(*) as cnt FROM filter_log WHERE run_id=? GROUP BY filter_stage",
            (run_id,),
        ).fetchall()
        if rejection_rows:
            console.print("\n[bold]Filter rejection summary:[/bold]")
            for row in rejection_rows:
                console.print(f"  {row['filter_stage']}: {row['cnt']} rejected")

    # Count unique companies that had postings
    total_companies = len(set(j.company_name for j in filtered))
    run_stats = {
        "total_fetched": len(all_jobs),
        "total_passed": len(filtered),
        "total_new": new_count,
        "total_companies": total_companies,
    }

    # AI Evaluation — score unevaluated jobs (with learned-rules pre-filter)
    # Finding #11: Use db (JobDB instance) instead of raw path for eval functions
    init_eval_schema(db)

    # Import any pending feedback from dashboard before mining rules
    output_dir = BASE_DIR / settings["output"]["dashboard_dir"]
    feedback_path = output_dir / "feedback.json"
    import_feedback_file(db.conn, feedback_path, logger)

    # Initialize learning engine and mine rules from past evaluations
    learning_engine = LearnedRulesEngine(db.conn)
    rule_stats = learning_engine.mine_rules()
    if any(v > 0 for v in rule_stats.values()):
        logger.info(f"Learned rules updated: {rule_stats}")

    if not skip_ai:
        unevaluated = get_unevaluated_jobs(db)
        if unevaluated:
            # Apply learned rules to partition jobs
            auto_skipped, boosted, to_evaluate = learning_engine.filter_for_eval(unevaluated)

            if auto_skipped:
                logger.info(
                    f"Learned rules auto-skipped {len(auto_skipped)} jobs "
                    f"(saved ~${len(auto_skipped) * 0.003:.2f} in API costs)"
                )
                # Save auto-skipped jobs with a placeholder evaluation
                from src.evaluator.scorer import EvalResult
                skip_results = [
                    EvalResult(
                        job_id=j["id"],
                        global_score=0.0,
                        reasoning=f"Auto-skipped by learned rule: {j.get('_auto_skip_reason', 'unknown')}",
                        recommended_action="skip",
                    )
                    for j in auto_skipped
                ]
                save_evaluations(db, skip_results, model="learned_rules")

            # AI-evaluate boosted jobs first, then the rest
            eval_queue = boosted + to_evaluate
            if eval_queue:
                logger.info(
                    f"AI evaluating {len(eval_queue)} jobs "
                    f"({len(boosted)} boosted, {len(to_evaluate)} standard)..."
                )
                try:
                    scorer = AIScorer(model="claude-haiku-4-5-20251001")
                    eval_results = await scorer.evaluate_batch(
                        eval_queue,
                        concurrency=15,
                        requests_per_minute=200,
                        progress_callback=lambda done, total: logger.info(f"  Evaluated {done}/{total} jobs"),
                    )
                    save_evaluations(db, eval_results, model=scorer.model)
                    apply_count = sum(1 for r in eval_results if r.global_score >= 3.0)
                    consider_count = sum(1 for r in eval_results if 2.0 <= r.global_score < 3.0)
                    logger.info(
                        f"AI evaluation complete: {apply_count} apply / {consider_count} consider "
                        f"out of {len(eval_results)} evaluated"
                    )
                except Exception as e:
                    logger.error(f"AI evaluation failed (continuing without): {e}")
            else:
                logger.info("All new jobs were auto-skipped by learned rules")
        else:
            logger.info("No new jobs to AI-evaluate")

    # Regenerate autofill JS from applicant profile (so dashboard embeds latest)
    profile_path = CONFIG_DIR / "applicant_profile.yml"
    if profile_path.exists():
        try:
            profile = load_profile(profile_path)
            autofill_js = generate_fill_js(profile)
            (output_dir / "autofill.js").write_text(autofill_js)
            logger.info("Autofill JS regenerated from applicant profile")
        except Exception as e:
            logger.warning(f"Failed to generate autofill JS: {e}")

    # Generate dashboard, digest, and CSV — use evaluated jobs
    jobs_for_output = get_all_evaluated_jobs(db)
    templates_dir = BASE_DIR / "templates"

    dashboard_path = render_dashboard(jobs_for_output, run_stats, output_dir, templates_dir)
    logger.info(f"Dashboard: {dashboard_path}")

    digest_path = render_digest(jobs_for_output, run_stats, output_dir)
    logger.info(f"Digest: {digest_path}")

    # Finding #25: CSV export
    csv_path = export_csv(jobs_for_output, output_dir)
    logger.info(f"CSV export: {csv_path}")

    # Resume generation for top jobs
    if generate_resumes_flag and not skip_ai:
        master_path = CONFIG_DIR / "master_resume.yml"
        if master_path.exists():
            resume_paths = generate_resumes(
                jobs_for_output,
                master_path=master_path,
                output_dir=output_dir,
                actions=("apply",),
                max_resumes=15,
            )
            if resume_paths:
                logger.info(f"Generated {len(resume_paths)} tailored resumes")
        else:
            logger.warning(f"Master resume not found at {master_path}, skipping resume generation")

    db.close()
    return filtered


def import_feedback_cli(path_str: str):
    """Standalone CLI command to import feedback and exit."""
    setup_logging(verbose=False)
    logger = logging.getLogger(__name__)
    settings, _ = load_config()
    db = JobDB(BASE_DIR / settings["output"]["db_path"])
    init_eval_schema(db)

    feedback_path = Path(path_str)
    if not feedback_path.exists():
        logger.error(f"Feedback file not found: {feedback_path}")
        db.close()
        return

    count = import_feedback_file(db.conn, feedback_path, logger)
    if count == 0:
        logger.info("No feedback to import")
    else:
        logger.info(f"Done: {count} feedback entries imported")
    db.close()


def main():
    """CLI entry point with argparse (Finding #19: replace manual argv parsing)."""
    parser = argparse.ArgumentParser(description="Job scraper pipeline")
    parser.add_argument("--platform", help="Only scrape this platform")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("--skip-ai", action="store_true", help="Skip AI evaluation step")
    parser.add_argument("--no-resumes", action="store_true",
                        help="Skip tailored resume PDF generation")
    parser.add_argument("--import-feedback", type=str, metavar="PATH",
                        help="Import feedback JSON from dashboard and exit")
    args = parser.parse_args()

    if args.import_feedback:
        import_feedback_cli(args.import_feedback)
    else:
        asyncio.run(run(
            platform_filter=args.platform,
            verbose=args.verbose,
            skip_ai=args.skip_ai,
            generate_resumes_flag=not args.no_resumes,
        ))


if __name__ == "__main__":
    main()

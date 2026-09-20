"""Resume generation orchestrator.

Takes evaluated jobs (apply/consider) and generates a tailored PDF resume
for each one. Called from main.py after AI evaluation completes.

Includes caching: skips jobs that already have a resume PDF, unless the
master_resume.yml has changed (detected via content hash).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from src.resume.tailor import ResumeTailor, TailoredResume, load_master_resume
from src.resume.pdf_gen import generate_resume_pdf, resume_filename_prefix

logger = logging.getLogger(__name__)

CACHE_FILE = "resume_cache.json"


def _master_hash(master_path: Path) -> str:
    """SHA256 hash of the master resume file content."""
    return hashlib.sha256(master_path.read_bytes()).hexdigest()[:16]


def _load_cache(resume_dir: Path) -> dict:
    """Load the resume cache metadata."""
    cache_path = resume_dir / CACHE_FILE
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(resume_dir: Path, cache: dict):
    """Save the resume cache metadata."""
    cache_path = resume_dir / CACHE_FILE
    cache_path.write_text(json.dumps(cache, indent=2))


def _resume_exists(resume_dir: Path, company: str, title: str, prefix: str) -> bool:
    """Check if a resume PDF already exists for this company/title combo."""
    company_clean = re.sub(r'[^\w\s-]', '', company).strip().replace(' ', '_')
    title_clean = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_')[:30]
    # Match any date suffix
    pattern = f"{prefix}_{company_clean}_{title_clean}_*.pdf"
    return any(resume_dir.glob(pattern))


def generate_resumes(
    jobs: list[dict],
    master_path: Path | str,
    output_dir: Path | str,
    actions: tuple[str, ...] = ("apply",),
    model: str = "claude-haiku-4-5-20251001",
    max_resumes: int = 20,
) -> list[Path]:
    """Generate tailored resumes for top-scoring jobs.

    Uses caching to skip jobs that already have a resume PDF. If the
    master_resume.yml has changed since the last run, all cached resumes
    are invalidated and regenerated.

    Args:
        jobs: List of evaluated job dicts (from get_all_evaluated_jobs).
        master_path: Path to master_resume.yml.
        output_dir: Directory to write PDFs into.
        actions: Which eval_action values to generate resumes for.
        model: Claude model for tailoring.
        max_resumes: Cap to avoid runaway API costs.

    Returns:
        List of paths to generated PDF files (newly generated only).
    """
    master_path = Path(master_path)
    resume_dir = Path(output_dir) / "resumes"
    resume_dir.mkdir(parents=True, exist_ok=True)

    # Partition action-eligible jobs into eligible vs. dropped (so we can surface
    # silent drops — most often empty description_text from Workday postings that
    # never got their detail body fetched).
    action_eligible = [j for j in jobs if j.get("eval_action") in actions]
    eligible: list[dict] = []
    dropped_no_desc: list[dict] = []
    for j in action_eligible:
        if j.get("description_text"):
            eligible.append(j)
        else:
            dropped_no_desc.append(j)

    if dropped_no_desc:
        sample = ", ".join(
            f"{j.get('company_name', '?')}/{(j.get('title') or '')[:40]} (id={j.get('id')})"
            for j in dropped_no_desc[:10]
        )
        more = f" (+{len(dropped_no_desc) - 10} more)" if len(dropped_no_desc) > 10 else ""
        logger.warning(
            f"Skipping {len(dropped_no_desc)} action-eligible job(s) with empty description_text "
            f"— no body to tailor against. Examples: {sample}{more}"
        )

    if not eligible:
        logger.info(
            f"No jobs eligible for resume generation "
            f"(action_eligible={len(action_eligible)}, dropped_no_desc={len(dropped_no_desc)})"
        )
        return []

    master = load_master_resume(master_path)
    prefix = resume_filename_prefix(master)

    # --- Cache invalidation: check if master resume changed ---
    current_hash = _master_hash(master_path)
    cache = _load_cache(resume_dir)
    cached_hash = cache.get("master_hash", "")

    if cached_hash and cached_hash != current_hash:
        logger.info(
            f"Master resume changed (hash {cached_hash[:8]}→{current_hash[:8]}), "
            f"clearing cached resumes"
        )
        # Delete all existing resume PDFs so they get regenerated
        for old_pdf in resume_dir.glob(f"{prefix}_*.pdf"):
            old_pdf.unlink(missing_ok=True)
        cache = {"master_hash": current_hash, "generated": {}}
    else:
        cache["master_hash"] = current_hash

    # --- Filter out jobs that already have a cached resume ---
    to_generate = []
    cached_count = 0
    for job in eligible:
        company = job.get("company_name", "Unknown")
        title = job.get("title", "Unknown")
        if _resume_exists(resume_dir, company, title, prefix):
            cached_count += 1
        else:
            to_generate.append(job)

    if cached_count:
        logger.info(f"Skipping {cached_count} jobs with existing resumes")

    if not to_generate:
        logger.info("All eligible jobs already have resumes — nothing to generate")
        return []

    if len(to_generate) > max_resumes:
        logger.info(f"Capping resume generation at {max_resumes} (of {len(to_generate)} new)")
        to_generate = to_generate[:max_resumes]

    logger.info(f"Generating tailored resumes for {len(to_generate)} new jobs...")

    tailor = ResumeTailor(master_path, model=model)
    generated = []

    for i, job in enumerate(to_generate, 1):
        company = job.get("company_name", "Unknown")
        title = job.get("title", "Unknown")
        try:
            logger.info(f"  [{i}/{len(to_generate)}] Tailoring for {company} — {title}")
            tailored = tailor.tailor(job)
            pdf_path = generate_resume_pdf(tailored, master, resume_dir)
            generated.append(pdf_path)

            # Track in cache
            cache.setdefault("generated", {})[f"{company}|{title}"] = {
                "file": pdf_path.name,
                "job_id": job.get("id", 0),
            }

            logger.info(f"    -> {pdf_path.name}")
        except Exception as e:
            logger.error(f"    Failed to generate resume for {company}/{title}: {e}")

    _save_cache(resume_dir, cache)
    logger.info(f"Generated {len(generated)}/{len(to_generate)} resumes in {resume_dir}")
    return generated

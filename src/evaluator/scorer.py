"""AI-powered job evaluation using Claude API.

Scores jobs across 5 dimensions (cv_match, north_star, sponsorship_likelihood,
seniority_fit, culture_signals) and produces a weighted global score with
recommended action (apply/consider/skip).

Finding #11: DB functions accept a JobDB instance instead of raw sqlite3 paths.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent


@dataclass
class EvalResult:
    """Result of AI evaluation for a single job."""
    job_id: int
    cv_match: float = 0.0          # 1-5
    north_star: float = 0.0        # 1-5
    sponsorship_likelihood: float = 0.0  # 1-5
    seniority_fit: float = 0.0     # 1-5
    culture_signals: float = 0.0   # 1-5
    global_score: float = 0.0      # 1-5 weighted
    reasoning: str = ""
    red_flags: list[str] = field(default_factory=list)
    recommended_action: str = ""   # "apply", "consider", "skip"


def load_profile(config_dir: Path | None = None) -> dict:
    """Load candidate profile from config/profile.yml."""
    if config_dir is None:
        config_dir = BASE_DIR / "config"
    with open(config_dir / "profile.yml") as f:
        return yaml.safe_load(f)


def build_profile_summary(profile: dict) -> str:
    """Build a concise profile summary for the evaluation prompt."""
    candidate = profile.get("candidate", {})
    education = profile.get("education", [])
    experience = profile.get("experience", {})
    skills = profile.get("skills", {})
    targets = profile.get("target_roles", {})

    edu_lines = []
    for e in education:
        status = f" ({e['status']})" if e.get("status") == "current" else ""
        edu_lines.append(f"- {e['degree']}, {e['school']}, {e['year']}{status}")

    roles = experience.get("roles", [])
    exp_lines = []
    for r in roles:
        exp_lines.append(f"- {r['title']} at {r['company']} ({r['duration']}): {r['domain']}")
        for h in r.get("highlights", []):
            exp_lines.append(f"  * {h}")

    tech = skills.get("technical", [])
    domain = skills.get("domain", [])

    primary = targets.get("primary", [])
    secondary = targets.get("secondary", [])

    return f"""CANDIDATE PROFILE:
Location: {candidate.get('location', 'N/A')}
Visa: {candidate.get('visa_status', 'N/A')}
Total experience: {experience.get('total_years', 0)} years

EDUCATION:
{chr(10).join(edu_lines)}

EXPERIENCE:
{chr(10).join(exp_lines)}

TECHNICAL SKILLS: {', '.join(tech)}
DOMAIN SKILLS: {', '.join(domain)}

TARGET ROLES (primary): {', '.join(primary)}
TARGET ROLES (secondary): {', '.join(secondary)}
"""


EVAL_SYSTEM_PROMPT_TEMPLATE = """You are a job evaluation assistant. You score job postings against a candidate's profile across 5 dimensions, each on a 1-5 scale.

SCORING DIMENSIONS:
1. CV Match (1-5): How well do the candidate's skills, experience, and education align with the job requirements?
   - 5 = Perfect match, candidate meets all requirements
   - 3 = Partial match, meets some requirements
   - 1 = Poor match, candidate lacks key requirements

2. North Star Alignment (1-5): How well does this role fit the candidate's career goals and target archetypes?
   - 5 = Dream role, exactly what they're targeting
   - 3 = Adjacent, could work but not ideal
   - 1 = Misaligned with career direction

3. Sponsorship Likelihood (1-5): Based on the company's H1B history and the job description, how likely is sponsorship?
   - If H1B Flag is NA, this is an INTERNSHIP — internships run on CPT/OPT, not H1B sponsorship, so this is not a blocker. Always score 5 for NA regardless of H1B history.
   - 5 = Company has strong H1B track record (GREEN flag) and no anti-sponsorship language
   - 3 = Unknown or mixed signals
   - 1 = Explicitly states no sponsorship or "must be authorized to work"

4. Seniority Fit (1-5): Is the seniority level appropriate for ~{years} years of experience?
   - 5 = Entry/early-career or associate level, perfect fit
   - 3 = Mid-level, might be a stretch but possible
   - 1 = Clearly requires 5+ years or senior/staff level

5. Culture Signals (1-5): Company culture, growth potential, industry relevance
   - 5 = Great culture signals, growing company, relevant industry
   - 3 = Neutral signals
   - 1 = Poor signals (layoffs, toxic reviews, declining industry)

RESPONSE FORMAT (strict JSON):
{
  "cv_match": <1-5>,
  "north_star": <1-5>,
  "sponsorship_likelihood": <1-5>,
  "seniority_fit": <1-5>,
  "culture_signals": <1-5>,
  "reasoning": "<2-3 sentence summary of the evaluation>",
  "red_flags": ["<list of any red flags found>"]
}

Rules:
- Be STRICT about seniority — with ~{years} years experience, anything requiring 5+ years should score low
- Be STRICT about sponsorship — any "must be authorized" or "no sponsorship" language = score 1
- Score each dimension independently; the downstream pipeline computes the weighted total and decides the action
- Output ONLY valid JSON, no markdown fences, no explanation outside the JSON
"""


def build_system_prompt(profile: dict) -> str:
    """Fill the candidate's years of experience (from profile.yml) into the system prompt."""
    years = profile.get("experience", {}).get("total_years", 2.5)
    # str.replace, not .format(): the template contains literal JSON braces.
    return EVAL_SYSTEM_PROMPT_TEMPLATE.replace("{years}", str(years))


def build_job_prompt(job: dict) -> str:
    """Build the per-job user message. Profile + instructions live in the cached system block."""
    desc_snippet = (job.get("description_text") or "")[:2000]
    h1b_flag = job.get("sponsorship_flag", "RED")
    h1b_count = job.get("h1b_count", 0)
    is_internship = job.get("is_internship")
    h1b_line = (
        "H1B Flag: NA (internship — runs on CPT/OPT, no H1B sponsorship needed)"
        if h1b_flag == "NA" or is_internship
        else f"H1B Flag: {h1b_flag} (company filed {h1b_count or 0} H1B petitions recently)"
    )

    return f"""JOB TO EVALUATE:
Company: {job.get('company_name', 'Unknown')}
Title: {job.get('title', 'Unknown')}
Location: {job.get('location_parsed', 'Unknown')}
Lane: {job.get('matched_lane', 'Unknown')}
{h1b_line}
Posted: {job.get('posted_at', 'Unknown')}

JOB DESCRIPTION (first 2000 chars):
{desc_snippet}

Evaluate this job against the candidate profile. Return strict JSON."""


class AIScorer:
    """Batch AI scorer using Claude API."""

    def __init__(self, api_key: str | None = None, model: str = "claude-haiku-4-5-20251001"):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY not set. Add it to .env or environment.")
        self.model = model
        self.client = AsyncAnthropic(api_key=self.api_key)
        self.profile = load_profile()
        self.profile_summary = build_profile_summary(self.profile)
        self.weights = self.profile.get("evaluation", {}).get("weights", {
            "cv_match": 0.35,
            "north_star": 0.25,
            "sponsorship_likelihood": 0.20,
            "seniority_fit": 0.10,
            "culture_signals": 0.10,
        })
        # Prompt-cacheable system blocks. The instructions + profile are identical
        # across every job in a run, so marking them ephemeral saves ~80% of input
        # tokens on the 2nd through Nth call within the 5-min cache window.
        self._system_blocks = [
            {"type": "text", "text": build_system_prompt(self.profile)},
            {
                "type": "text",
                "text": self.profile_summary,
                "cache_control": {"type": "ephemeral"},
            },
        ]

    async def evaluate_job(self, job: dict, rate_limiter=None) -> EvalResult:
        """Evaluate a single job using Claude API."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if rate_limiter:
                    await rate_limiter.acquire()

                prompt = build_job_prompt(job)
                response = await self.client.messages.create(
                    model=self.model,
                    max_tokens=500,
                    system=self._system_blocks,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = response.content[0].text.strip()

                # Parse JSON response — handle potential markdown fences
                if text.startswith("```"):
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

                data = json.loads(text)

                result = EvalResult(
                    job_id=job.get("id", 0),
                    cv_match=float(data.get("cv_match", 0)),
                    north_star=float(data.get("north_star", 0)),
                    sponsorship_likelihood=float(data.get("sponsorship_likelihood", 0)),
                    seniority_fit=float(data.get("seniority_fit", 0)),
                    culture_signals=float(data.get("culture_signals", 0)),
                    reasoning=data.get("reasoning", ""),
                    red_flags=data.get("red_flags", []),
                )

                # Calculate weighted global score
                result.global_score = round(
                    result.cv_match * self.weights.get("cv_match", 0.3)
                    + result.north_star * self.weights.get("north_star", 0.2)
                    + result.sponsorship_likelihood * self.weights.get("sponsorship_likelihood", 0.2)
                    + result.seniority_fit * self.weights.get("seniority_fit", 0.15)
                    + result.culture_signals * self.weights.get("culture_signals", 0.15),
                    2,
                )

                # Deterministic action from the weighted score (single source of truth).
                # Thresholds intentionally live in code, not the prompt, to avoid drift.
                if result.global_score >= 3.0:
                    result.recommended_action = "apply"
                elif result.global_score >= 2.0:
                    result.recommended_action = "consider"
                else:
                    result.recommended_action = "skip"

                return result

            except json.JSONDecodeError as e:
                logger.error(f"JSON parse error for {job.get('company_name')}/{job.get('title')}: {e}")
                return EvalResult(job_id=job.get("id", 0), reasoning=f"Parse error: {e}")
            except Exception as e:
                err_str = str(e)
                if "429" in err_str and attempt < max_retries - 1:
                    wait = (attempt + 1) * 5
                    logger.warning(f"Rate limited, retrying in {wait}s... ({job.get('company_name')}/{job.get('title')})")
                    await asyncio.sleep(wait)
                    continue
                logger.error(f"Eval error for {job.get('company_name')}/{job.get('title')}: {e}")
                return EvalResult(job_id=job.get("id", 0), reasoning=f"Error: {e}")

        return EvalResult(job_id=job.get("id", 0), reasoning="Max retries exceeded")

    async def evaluate_batch(
        self,
        jobs: list[dict],
        concurrency: int = 10,
        progress_callback=None,
        requests_per_minute: int = 40,
    ) -> list[EvalResult]:
        """Evaluate a batch of jobs with rate-limited concurrency."""

        # Token-bucket rate limiter: allows `requests_per_minute` calls per 60s
        class RateLimiter:
            def __init__(self, rpm):
                self.interval = 60.0 / rpm  # seconds between requests
                self.lock = asyncio.Lock()
                self.last = 0.0

            async def acquire(self):
                async with self.lock:
                    now = time.monotonic()
                    wait = self.last + self.interval - now
                    if wait > 0:
                        await asyncio.sleep(wait)
                    self.last = time.monotonic()

        rate_limiter = RateLimiter(requests_per_minute)
        sem = asyncio.Semaphore(concurrency)
        results: list[EvalResult] = []
        done_count = 0
        lock = asyncio.Lock()

        async def eval_one(job):
            nonlocal done_count
            async with sem:
                result = await self.evaluate_job(job, rate_limiter)
            async with lock:
                done_count += 1
                results.append(result)
                if progress_callback and (done_count % 25 == 0 or done_count == len(jobs)):
                    progress_callback(done_count, len(jobs))
            return result

        # Launch all tasks — the semaphore + rate limiter handle pacing
        tasks = [eval_one(j) for j in jobs]
        await asyncio.gather(*tasks)

        return results


# --- Database integration ---

EVAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS job_evaluations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL,
    cv_match        REAL,
    north_star      REAL,
    sponsorship_likelihood REAL,
    seniority_fit   REAL,
    culture_signals REAL,
    global_score    REAL,
    reasoning       TEXT,
    red_flags       TEXT,
    recommended_action TEXT,
    evaluated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    model_used      TEXT,
    UNIQUE(job_id)
);

CREATE INDEX IF NOT EXISTS idx_eval_job ON job_evaluations(job_id);
CREATE INDEX IF NOT EXISTS idx_eval_score ON job_evaluations(global_score DESC);
"""


def init_eval_schema(db):
    """Initialize the evaluation table in the database.

    Args:
        db: A JobDB instance (uses its connection), or a str/Path for backward compat.
    """
    conn = _get_conn(db)
    conn.executescript(EVAL_SCHEMA)
    conn.commit()


def save_evaluations(db, results: list[EvalResult], model: str = ""):
    """Save evaluation results to database.

    Skips results with zero score and empty reasoning (failed evaluations).

    Args:
        db: A JobDB instance or str/Path to database.
        results: List of EvalResult objects to persist.
        model: Model identifier string stored for provenance.
    """
    conn = _get_conn(db)
    for r in results:
        if r.global_score == 0 and not r.reasoning:
            continue  # Skip failed evaluations
        conn.execute(
            """INSERT OR REPLACE INTO job_evaluations
               (job_id, cv_match, north_star, sponsorship_likelihood, seniority_fit,
                culture_signals, global_score, reasoning, red_flags, recommended_action, model_used)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                r.job_id, r.cv_match, r.north_star, r.sponsorship_likelihood,
                r.seniority_fit, r.culture_signals, r.global_score,
                r.reasoning, json.dumps(r.red_flags), r.recommended_action, model,
            ),
        )
    conn.commit()


def get_unevaluated_jobs(db) -> list[dict]:
    """Get active jobs that haven't been AI-evaluated yet.

    Args:
        db: A JobDB instance or str/Path to database.

    Returns:
        List of job dicts ordered by newest first.
    """
    conn = _get_conn(db)
    rows = conn.execute(
        """SELECT j.* FROM jobs j
           LEFT JOIN job_evaluations e ON j.id = e.job_id
           WHERE j.is_active = 1 AND e.id IS NULL
           ORDER BY j.first_seen_at DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_evaluated_jobs(db) -> list[dict]:
    """Get all active jobs with their evaluation scores joined.

    Args:
        db: A JobDB instance or str/Path to database.

    Returns:
        List of job dicts with eval_* columns, ordered by global_score desc.
    """
    conn = _get_conn(db)
    rows = conn.execute(
        """SELECT j.*, e.cv_match AS eval_cv_match, e.north_star AS eval_north_star,
                  e.sponsorship_likelihood AS eval_sponsorship, e.seniority_fit AS eval_seniority,
                  e.culture_signals AS eval_culture, e.global_score AS eval_global_score,
                  e.reasoning AS eval_reasoning, e.red_flags AS eval_red_flags,
                  e.recommended_action AS eval_action
           FROM jobs j
           LEFT JOIN job_evaluations e ON j.id = e.job_id
           WHERE j.is_active = 1
           ORDER BY COALESCE(e.global_score, 0) DESC, j.first_seen_at DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def _get_conn(db):
    """Extract a sqlite3.Connection from a JobDB instance, path, or string.

    Allows these functions to work with either the new JobDB interface (Finding #11)
    or raw paths for backward compatibility.
    """
    import sqlite3
    if hasattr(db, "conn"):
        # JobDB instance — reuse its connection (WAL mode, busy timeout, etc.)
        return db.conn
    # Backward compat: raw path
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn

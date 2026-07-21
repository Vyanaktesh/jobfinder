from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

from src.models import FilteredJob

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key       TEXT UNIQUE NOT NULL,
    source_platform TEXT NOT NULL,
    company_token   TEXT NOT NULL,
    company_name    TEXT NOT NULL,
    external_id     TEXT NOT NULL,
    title           TEXT NOT NULL,
    location_raw    TEXT,
    location_parsed TEXT,
    description_text TEXT,
    posted_at       TEXT,
    apply_url       TEXT NOT NULL,
    salary_raw      TEXT,
    workplace_type  TEXT,
    matched_lane    TEXT,
    match_score     REAL,
    is_rotational   INTEGER DEFAULT 0,
    is_internship   INTEGER DEFAULT 0,
    sponsorship_flag TEXT,
    h1b_count       INTEGER,
    first_seen_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at    TEXT NOT NULL DEFAULT (datetime('now')),
    is_active       INTEGER DEFAULT 1,
    metadata_json   TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_dedup ON jobs(dedup_key);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company_name);
CREATE INDEX IF NOT EXISTS idx_jobs_lane ON jobs(matched_lane);
CREATE INDEX IF NOT EXISTS idx_jobs_active ON jobs(is_active);

CREATE TABLE IF NOT EXISTS h1b_employers (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    employer_name           TEXT NOT NULL,
    employer_name_normalized TEXT NOT NULL,
    case_status             TEXT,
    fiscal_year             INTEGER,
    worker_count            INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_h1b_employer ON h1b_employers(employer_name_normalized);

CREATE TABLE IF NOT EXISTS run_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    total_fetched   INTEGER DEFAULT 0,
    total_passed    INTEGER DEFAULT 0,
    total_new       INTEGER DEFAULT 0,
    errors_json     TEXT
);

CREATE TABLE IF NOT EXISTS filter_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER,
    company_name    TEXT,
    title           TEXT,
    filter_stage    TEXT,
    reason          TEXT,
    apply_url       TEXT
);

CREATE TABLE IF NOT EXISTS filter_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL,
    stats_json      TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS job_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL,
    field_name      TEXT NOT NULL,
    old_value       TEXT,
    new_value       TEXT,
    changed_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_job_history_job ON job_history(job_id);
"""

# Max retries for database operations when locked
DB_RETRY_ATTEMPTS = 5
DB_RETRY_BASE_DELAY = 0.1  # seconds


def _retry_on_locked(func):
    """Decorator to retry database operations on 'database is locked' errors."""
    def wrapper(*args, **kwargs):
        for attempt in range(DB_RETRY_ATTEMPTS):
            try:
                return func(*args, **kwargs)
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < DB_RETRY_ATTEMPTS - 1:
                    wait = DB_RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(f"DB locked, retry {attempt + 1}/{DB_RETRY_ATTEMPTS} in {wait:.1f}s")
                    time.sleep(wait)
                else:
                    raise
    return wrapper


class JobDB:
    """SQLite database interface for job storage and run tracking.

    Args:
        db_path: Path to the SQLite database file.
        wal_mode: Enable WAL mode for better concurrent read performance.
    """

    def __init__(self, db_path: str | Path, wal_mode: bool = True):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        if wal_mode:
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout = 10000")  # 10s busy timeout
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript(SCHEMA)
        self._migrate_schema()
        self.conn.commit()

    def _migrate_schema(self):
        """Add columns to pre-existing databases that predate a schema change.

        CREATE TABLE IF NOT EXISTS in SCHEMA only applies to brand-new DBs;
        existing jobs.db files need explicit ALTER TABLE for new columns.
        """
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(jobs)").fetchall()}
        if "is_internship" not in cols:
            self.conn.execute("ALTER TABLE jobs ADD COLUMN is_internship INTEGER DEFAULT 0")
            logger.info("Migrated jobs table: added is_internship column")

    def close(self):
        self.conn.close()

    def start_run(self) -> int:
        """Start a new scraper run and return its ID."""
        cur = self.conn.execute(
            "INSERT INTO run_log (started_at) VALUES (?)",
            (datetime.now().isoformat(),),
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_run(
        self, run_id: int, total_fetched: int, total_passed: int, total_new: int, errors: list[str]
    ):
        """Mark a run as complete with summary statistics."""
        self.conn.execute(
            """UPDATE run_log SET finished_at=?, total_fetched=?, total_passed=?, total_new=?, errors_json=?
               WHERE id=?""",
            (datetime.now().isoformat(), total_fetched, total_passed, total_new, json.dumps(errors), run_id),
        )
        self.conn.commit()

    @_retry_on_locked
    def upsert_job(self, job: FilteredJob) -> bool:
        """Insert or update a job. Returns True if the job is new.

        Uses a transaction to prevent race conditions with concurrent writers.
        Tracks field changes in job_history for update detection.
        """
        with self.conn:
            existing = self.conn.execute(
                "SELECT id, sponsorship_flag, h1b_count, description_text FROM jobs WHERE dedup_key = ?",
                (job.dedup_key,),
            ).fetchone()

            if existing:
                # Track changes (Finding #23: job change detection)
                changes = []
                if existing["sponsorship_flag"] != job.sponsorship_flag:
                    changes.append(("sponsorship_flag", existing["sponsorship_flag"], job.sponsorship_flag))
                if existing["h1b_count"] != job.h1b_count:
                    changes.append(("h1b_count", str(existing["h1b_count"]), str(job.h1b_count)))

                for field_name, old_val, new_val in changes:
                    self.conn.execute(
                        "INSERT INTO job_history (job_id, field_name, old_value, new_value) VALUES (?,?,?,?)",
                        (existing["id"], field_name, old_val, new_val),
                    )

                self.conn.execute(
                    """UPDATE jobs SET last_seen_at=?, is_active=1, sponsorship_flag=?, h1b_count=?, is_internship=?
                       WHERE dedup_key=?""",
                    (datetime.now().isoformat(), job.sponsorship_flag, job.h1b_count, int(job.is_internship), job.dedup_key),
                )
                return False

            self.conn.execute(
                """INSERT INTO jobs (dedup_key, source_platform, company_token, company_name,
                   external_id, title, location_raw, location_parsed, description_text,
                   posted_at, apply_url, salary_raw, workplace_type, matched_lane,
                   match_score, is_rotational, is_internship, sponsorship_flag, h1b_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job.dedup_key, job.source_platform, job.company_token, job.company_name,
                    job.external_id, job.title, job.location_raw, job.location_parsed,
                    job.description_text, job.posted_at.isoformat() if job.posted_at else None,
                    job.apply_url, job.salary_raw, job.workplace_type, job.matched_lane,
                    job.match_score, int(job.is_rotational), int(job.is_internship),
                    job.sponsorship_flag, job.h1b_count,
                ),
            )
            return True

    @_retry_on_locked
    def mark_inactive_not_seen(self, seen_keys: set[str]):
        """Mark jobs not seen in this run as inactive.

        Loads seen_keys into a temp table so the NOT IN check sees the FULL set
        in a single UPDATE — the previous batched implementation was buggy because
        each batch's NOT IN would (correctly, but disastrously) mark the OTHER
        batches' valid keys as inactive. With >900 keys that nuked everything.
        """
        if not seen_keys:
            return
        with self.conn:
            self.conn.execute("DROP TABLE IF EXISTS _seen_keys_tmp")
            self.conn.execute(
                "CREATE TEMP TABLE _seen_keys_tmp (dedup_key TEXT PRIMARY KEY)"
            )
            self.conn.executemany(
                "INSERT OR IGNORE INTO _seen_keys_tmp (dedup_key) VALUES (?)",
                [(k,) for k in seen_keys],
            )
            self.conn.execute(
                "UPDATE jobs SET is_active = 0 "
                "WHERE is_active = 1 "
                "AND dedup_key NOT IN (SELECT dedup_key FROM _seen_keys_tmp)"
            )
            self.conn.execute("DROP TABLE _seen_keys_tmp")

    def log_filter_rejection(self, run_id: int, company: str, title: str, stage: str, reason: str, url: str = ""):
        """Log a filter rejection for debugging and analysis."""
        self.conn.execute(
            "INSERT INTO filter_log (run_id, company_name, title, filter_stage, reason, apply_url) VALUES (?,?,?,?,?,?)",
            (run_id, company, title, stage, reason, url),
        )

    def save_filter_stats(self, run_id: int, stats: dict):
        """Persist filter statistics for historical analysis (Finding #17)."""
        self.conn.execute(
            "INSERT INTO filter_stats (run_id, stats_json) VALUES (?,?)",
            (run_id, json.dumps(stats)),
        )
        self.conn.commit()

    def commit(self):
        self.conn.commit()

    @_retry_on_locked
    def expire_old_jobs(self, max_age_days: int) -> int:
        """Deactivate jobs older than max_age_days based on posted_at or first_seen_at."""
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
        with self.conn:
            cursor = self.conn.execute(
                """UPDATE jobs SET is_active = 0
                   WHERE is_active = 1
                   AND COALESCE(posted_at, first_seen_at) < ?""",
                (cutoff,),
            )
        return cursor.rowcount

    def get_all_active_jobs(self) -> list[dict]:
        """Return all active jobs ordered by newest first."""
        rows = self.conn.execute(
            "SELECT * FROM jobs WHERE is_active = 1 ORDER BY first_seen_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_platform_progress(self, run_id: int, platform: str) -> int:
        """Get checkpoint count for resume capability (Finding #6)."""
        row = self.conn.execute(
            """SELECT COUNT(*) FROM filter_log WHERE run_id = ?
               AND company_name LIKE ?""",
            (run_id, f"%{platform}%"),
        ).fetchone()
        return row[0] if row else 0

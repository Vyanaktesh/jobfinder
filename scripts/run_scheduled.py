#!/usr/bin/env python3
"""Scheduled run wrapper for launchd.

Handles lock file, feedback import from ~/Downloads, env loading,
and macOS notification — all in Python to avoid Full Disk Access
issues with bash scripts under launchd.
"""
import fcntl
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_DIR / "logs"
LOCK_FILE = PROJECT_DIR / ".scraper.lock"
OUTPUT_DIR = PROJECT_DIR / "output"
VENV_PYTHON = PROJECT_DIR / ".venv" / "bin" / "python"

LOG_DIR.mkdir(exist_ok=True)


def notify(title: str, message: str, sound: str = "Glass"):
    """Send a macOS notification."""
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{message}" with title "{title}" sound name "{sound}"'],
            timeout=5, capture_output=True,
        )
    except Exception:
        pass


def load_env():
    """Load .env file into environment."""
    env_file = PROJECT_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ[key.strip()] = val.strip()


def get_new_count() -> str:
    """Get the new job count from the latest run log."""
    try:
        db_path = PROJECT_DIR / "data" / "jobs.db"
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT total_new FROM run_log ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        return str(row[0]) if row else "0"
    except Exception:
        return "?"


def main():
    # Acquire lock (non-blocking)
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"Scraper already running, skipping")
        return 0
    lock_fd.write(str(os.getpid()))
    lock_fd.flush()

    try:
        print(f"=== Job Scraper Run: {time.strftime('%Y-%m-%d %H:%M:%S')} ===")

        load_env()
        os.environ["JOBSCRAPER_HEADLESS"] = "1"

        start = time.time()
        HARD_CAP = 5400  # 90 min of AWAKE time — full pipeline averages ~60 min

        # Wrap the scraper in `caffeinate` so the Mac can't idle/sleep mid-run.
        # Without this, overnight runs span sleep cycles: wall-clock balloons to
        # many hours and the monotonic-based HARD_CAP (which pauses during sleep)
        # never fires. caffeinate holds the power assertion only while the child
        # process lives, so it's automatically released when the run ends.
        #   -i  prevent idle sleep   -m  prevent disk sleep   -s  prevent system sleep
        caffeinate = "/usr/bin/caffeinate"
        cmd = [str(VENV_PYTHON), "-m", "src.main"]
        if Path(caffeinate).exists():
            cmd = [caffeinate, "-i", "-m", "-s"] + cmd

        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_DIR),
            env=os.environ.copy(),
        )
        try:
            returncode = proc.wait(timeout=HARD_CAP)
        except subprocess.TimeoutExpired:
            # subprocess.run's timeout can be bypassed when child inherits
            # stdio; we enforce it explicitly with Popen.wait + SIGKILL.
            print(f"[run_scheduled] Hard cap {HARD_CAP}s exceeded — SIGKILLing scraper")
            proc.kill()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            returncode = -9

        elapsed = int(time.time() - start)
        minutes = elapsed // 60

        # Mimic the old result.returncode interface
        class _R:
            pass
        result = _R()
        result.returncode = returncode

        print(f"=== Finished: {time.strftime('%Y-%m-%d %H:%M:%S')} "
              f"({minutes}m {elapsed}s, exit code {result.returncode}) ===")

        if result.returncode == 0:
            new_count = get_new_count()
            notify("Job Scraper", f"Found {new_count} new jobs ({minutes}min)")
        else:
            notify("Job Scraper", f"Run failed (exit code {result.returncode})", sound="Basso")

        return result.returncode

    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()
        LOCK_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())

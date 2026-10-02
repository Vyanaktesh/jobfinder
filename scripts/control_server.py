"""Local control server for the job-scraper dashboard.

A static dashboard opened as file:// can't launch the pipeline or delete files
on its own. This tiny stdlib HTTP server exposes a few endpoints the dashboard
buttons call (with CORS so file:// pages can reach it):

    GET  /status            -> {running, progress, apply_count}
    POST /run               -> trigger a full pipeline run (via launchd)
    GET  /                  -> redirect to the latest dashboard (convenience)

Run it:
    .venv/bin/python scripts/control_server.py        # listens on :8765

It shells out to `launchctl kickstart` to reuse the existing launchd job
(com.devansh.jobscraper) so runs survive terminal/harness exits.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))


PORT = int(os.getenv("JOBSCRAPER_CONTROL_PORT", "8765"))
LAUNCHD_LABEL = "com.devansh.jobscraper"
OUTPUT_DIR = PROJECT_DIR / "output"
SCRAPER_LOG = PROJECT_DIR / "logs" / "scraper.log"
LOCK_FILE = PROJECT_DIR / ".scraper.lock"


def _pipeline_running() -> bool:
    """True if a scraper process is currently alive."""
    try:
        out = subprocess.run(
            ["pgrep", "-f", "python -m src.main"],
            capture_output=True, text=True, timeout=5,
        )
        return out.returncode == 0 and bool(out.stdout.strip())
    except Exception:
        return LOCK_FILE.exists()


def _last_progress() -> str:
    """Pull the most recent meaningful progress line from the scraper log."""
    if not SCRAPER_LOG.exists():
        return ""
    try:
        # Read tail efficiently
        with open(SCRAPER_LOG, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 500000))  # large window: logs are DEBUG-heavy
            tail = f.read().decode("utf-8", "replace").splitlines()
        wanted = re.compile(r"(Scraping |Progress:|Done:|Total raw|Passed filters|"
                            r"Dashboard:)")
        hits = [ln for ln in tail if wanted.search(ln)]
        return hits[-1].split("| __main__ |")[-1].strip() if hits else ""
    except Exception:
        return ""


def _apply_count() -> int | None:
    """Count active H1B-friendly (GREEN/YELLOW) jobs in the DB (best-effort)."""
    try:
        import sqlite3
        conn = sqlite3.connect(str(PROJECT_DIR / "data" / "jobs.db"))
        row = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE is_active=1 AND sponsorship_flag IN ('GREEN','YELLOW')"
        ).fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception:
        return None


def _trigger_run() -> tuple[bool, str]:
    if _pipeline_running():
        return False, "Pipeline already running"
    try:
        LOCK_FILE.unlink(missing_ok=True)
        subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"],
            capture_output=True, text=True, timeout=15, check=True,
        )
        return True, "Pipeline started"
    except subprocess.CalledProcessError as e:
        return False, f"launchctl failed: {e.stderr or e}"
    except Exception as e:
        return False, str(e)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):  # noqa: N802 (CORS preflight)
        self._send(204, {})

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/status"):
            self._send(200, {
                "running": _pipeline_running(),
                "progress": _last_progress(),
                "apply_count": _apply_count(),
            })
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            data = {}

        if self.path.startswith("/run"):
            ok, msg = _trigger_run()
            self._send(200 if ok else 409, {"ok": ok, "message": msg})
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, *args):  # silence default noisy logging
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Job-scraper control server listening on http://localhost:{PORT}")
    print("  GET  /status   POST /run")
    print("Leave this running; use the dashboard buttons. Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()

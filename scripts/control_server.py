"""Local server for the job-scraper dashboard: open it, click Run, watch it work.

    python scripts/control_server.py            # then your browser opens on http://localhost:8765
    python scripts/control_server.py --no-browser

Works on Windows, macOS and Linux. It serves output/latest.html and exposes:

    GET  /status   -> {running, stage, percent, platforms, last_run, counts, ...}
    POST /run      -> start a scrape.  body: {"mode": "quick" | "full" | "recheck"}
                        quick   = only boards of companies on your H1B list (fastest)
                        full    = everything, skipping boards that were recently empty
                        recheck = everything, re-checking even the empty boards
    POST /stop     -> stop the running scrape
    GET  /         -> the latest dashboard

Everything binds to 127.0.0.1 only; nothing is reachable from other machines.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

PORT = int(os.getenv("JOBSCRAPER_CONTROL_PORT", "8765"))
OUTPUT_DIR = PROJECT_DIR / "output"
PROGRESS_FILE = PROJECT_DIR / "data" / "progress.json"
DB_FILE = PROJECT_DIR / "data" / "jobs.db"
RUN_LOG = PROJECT_DIR / "logs" / "run.log"

MODE_ARGS = {
    "quick": ["--sponsors-only"],
    "full": [],
    "recheck": ["--full"],
}

_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_run_mode: str | None = None


def run_command(mode: str) -> list[str]:
    """Command that performs a scrape (overridable in tests via JOBSCRAPER_RUN_CMD)."""
    override = os.getenv("JOBSCRAPER_RUN_CMD")
    if override:
        return override.split() + MODE_ARGS[mode]
    return [sys.executable, "-m", "src.main"] + MODE_ARGS[mode]


ACTIVE_STAGES = ("starting", "scraping", "filtering", "saving", "rendering")


def pid_alive(pid: int) -> bool:
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                 capture_output=True, text=True, timeout=10).stdout
            return str(pid) in out
        os.kill(pid, 0)  # signal 0 = existence check only
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def external_pid() -> int | None:
    """PID of a scrape started outside this server (terminal, or before a server restart)."""
    prog = _read_progress()
    if prog and prog.get("stage") in ACTIVE_STAGES and prog.get("pid") and pid_alive(int(prog["pid"])):
        return int(prog["pid"])
    return None


def is_running() -> bool:
    return (_proc is not None and _proc.poll() is None) or external_pid() is not None


def start_run(mode: str) -> tuple[bool, str]:
    global _proc, _run_mode
    if mode not in MODE_ARGS:
        return False, f"Unknown mode: {mode}"
    with _lock:
        if is_running():
            return False, "A scrape is already running (maybe started from a terminal)"
        RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
        log = open(RUN_LOG, "w", encoding="utf-8")
        env = dict(os.environ, JOBSCRAPER_HEADLESS="1", PYTHONUTF8="1", PYTHONUNBUFFERED="1")
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            _proc = subprocess.Popen(
                run_command(mode), cwd=PROJECT_DIR, env=env,
                stdout=log, stderr=subprocess.STDOUT, **kwargs,
            )
        except OSError as e:
            return False, f"Could not start: {e}"
        _run_mode = mode
        # Clear stale progress so the bar starts fresh
        try:
            PROGRESS_FILE.unlink(missing_ok=True)
        except OSError:
            pass
    label = {"quick": "Quick scan", "full": "Full scan", "recheck": "Full re-check"}[mode]
    return True, f"{label} started"


def stop_run() -> tuple[bool, str]:
    with _lock:
        if not is_running():
            return False, "Nothing is running"
        pid = _proc.pid if (_proc is not None and _proc.poll() is None) else external_pid()
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15)
            else:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
        except Exception as e:  # noqa: BLE001
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:  # noqa: BLE001
                return False, f"Could not stop: {e}"
    try:
        if _proc is not None:
            _proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        pass
    try:
        data = _read_progress() or {}
        data.update(stage="stopped", message="Stopped by you")
        PROGRESS_FILE.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass
    return True, "Stopped"


def _read_progress() -> dict | None:
    try:
        return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def percent_complete(progress: dict | None) -> int:
    if not progress:
        return 0
    stage = progress.get("stage")
    if stage in ("done",):
        return 100
    plats = progress.get("platforms") or {}
    total = sum(p.get("total", 0) for p in plats.values())
    done = sum(min(p.get("done", 0), p.get("total", 0)) for p in plats.values())
    scrape_frac = (done / total) if total else 0.0
    # scraping is ~90% of the work; the rest is filtering/saving/rendering
    base = {"starting": 0, "scraping": 0, "filtering": 90, "saving": 94, "rendering": 98}.get(stage, 0)
    if stage in ("starting", "scraping"):
        return int(scrape_frac * 90)
    return base


def last_run_and_counts() -> tuple[dict | None, dict]:
    if not DB_FILE.exists():
        return None, {}
    try:
        conn = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT started_at, finished_at, total_fetched, total_passed, total_new "
            "FROM run_log WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
        counts = {r[0] or "RED": r[1] for r in conn.execute(
            "SELECT sponsorship_flag, COUNT(*) FROM jobs WHERE is_active=1 GROUP BY sponsorship_flag")}
        conn.close()
        return (dict(row) if row else None), counts
    except sqlite3.Error:
        return None, {}


def latest_dashboard_mtime() -> float:
    f = OUTPUT_DIR / "latest.html"
    return f.stat().st_mtime if f.exists() else 0.0


def status() -> dict:
    progress = _read_progress()
    running = is_running()
    stage = (progress or {}).get("stage")
    if _proc is not None and not running and stage not in ("done", "error", "stopped"):
        # Process ended without reporting (crashed / killed) — surface that
        code = _proc.returncode
        stage = "error" if code not in (0, None) else stage
    last_run, counts = last_run_and_counts()
    return {
        "running": running,
        "mode": _run_mode,
        "stage": stage,
        "message": (progress or {}).get("message", ""),
        "percent": percent_complete(progress) if running or stage == "done" else 0,
        "platforms": (progress or {}).get("platforms", {}),
        "result": (progress or {}).get("result"),
        "started_at": (progress or {}).get("started_at"),
        "last_run": last_run,
        "counts": counts,
        "dashboard_mtime": latest_dashboard_mtime(),
        "server_time": datetime.now().isoformat(timespec="seconds"),
    }


def ensure_dashboard():
    """Build output/latest.html from the DB if it doesn't exist yet."""
    if (OUTPUT_DIR / "latest.html").exists() or not DB_FILE.exists():
        return
    try:
        subprocess.run([sys.executable, str(PROJECT_DIR / "scripts" / "render_only.py")],
                       cwd=PROJECT_DIR, capture_output=True, timeout=120)
    except Exception:  # noqa: BLE001
        pass


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):  # noqa: N802
        self._json(204, {})

    def do_GET(self):  # noqa: N802
        path = unquote(urlparse(self.path).path)
        if path == "/status":
            return self._json(200, status())
        if path in ("/", ""):
            path = "/latest.html"
        target = (OUTPUT_DIR / path.lstrip("/")).resolve()
        # Only serve files inside output/ (blocks ../ tricks) and never the PII-heavy autofill script
        if OUTPUT_DIR.resolve() not in target.parents or target.name.startswith("autofill") or not target.is_file():
            if path == "/latest.html":
                body = (b"<h1>No dashboard yet</h1><p>Click run from a terminal once: "
                        b"<code>python -m src.main</code>, or <code>python scripts/render_only.py</code> "
                        b"if you already have data.</p>")
                self.send_response(404)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            return self._json(404, {"error": "not found"})
        data = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            data = {}
        path = urlparse(self.path).path
        if path == "/run":
            ok, msg = start_run(data.get("mode", "full"))
            return self._json(200 if ok else 409, {"ok": ok, "message": msg})
        if path == "/stop":
            ok, msg = stop_run()
            return self._json(200 if ok else 409, {"ok": ok, "message": msg})
        self._json(404, {"error": "not found"})

    def log_message(self, *args):  # quiet
        pass


def main():
    ap = argparse.ArgumentParser(description="Dashboard + Run button server")
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()

    ensure_dashboard()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://localhost:{args.port}"
    print(f"Job dashboard running at {url}")
    print("Use the Run buttons in the page. Leave this window open; Ctrl+C to quit.")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        if is_running():
            print("A scrape is still running; leaving it to finish in the background.")
        server.server_close()


if __name__ == "__main__":
    main()

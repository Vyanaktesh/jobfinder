"""Live progress reporting for the dashboard's Run button.

The pipeline writes data/progress.json (atomically, throttled); the control
server serves it to the dashboard. Safe to use when nobody is listening.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

STAGES = ("starting", "scraping", "filtering", "saving", "rendering", "done", "error", "stopped")


class ProgressTracker:
    def __init__(self, path: Path | None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._last_write = 0.0
        self.state = {
            "pid": os.getpid(),
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "stage": "starting",
            "message": "",
            "platforms": {},
            "result": None,
        }
        self._write(force=True)

    def stage(self, stage: str, message: str = ""):
        with self._lock:
            self.state["stage"] = stage
            self.state["message"] = message
        self._write(force=True)

    def platform_total(self, platform: str, total: int, skipped: int = 0):
        with self._lock:
            self.state["platforms"][platform] = {"done": 0, "total": total, "skipped": skipped}
        self._write(force=True)

    def platform_done(self, platform: str, done: int):
        with self._lock:
            p = self.state["platforms"].setdefault(platform, {"done": 0, "total": done, "skipped": 0})
            p["done"] = done
        self._write()

    def finish(self, result: dict | None = None, error: str | None = None):
        with self._lock:
            self.state["result"] = result
            self.state["stage"] = "error" if error else "done"
            self.state["message"] = error or ""
        self._write(force=True)

    def _write(self, force: bool = False):
        if not self.path:
            return
        now = time.monotonic()
        if not force and now - self._last_write < 1.0:
            return
        self._last_write = now
        with self._lock:
            payload = dict(self.state, updated_at=datetime.now().isoformat(timespec="seconds"))
            text = json.dumps(payload)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass  # progress is best-effort; never break the scrape

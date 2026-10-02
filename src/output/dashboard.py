"""Render the HTML dashboard (output/latest.html) from evaluated job dicts."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

# Fields the dashboard JS needs; keeps the embedded JSON small (no descriptions).
_JSON_FIELDS = (
    "id", "company_name", "title", "location_parsed", "matched_lane", "apply_url",
    "sponsorship_flag", "h1b_count",
)


def _prepare(job: dict) -> dict:
    job = dict(job)
    job["sponsorship_flag"] = job.get("sponsorship_flag") or "RED"
    job["location_parsed"] = job.get("location_parsed") or job.get("location_raw") or ""
    job["matched_lane"] = job.get("matched_lane") or ""
    job["match_score"] = job.get("match_score") or 0
    job["is_new"] = bool(job.get("is_new") or _is_recent(job.get("first_seen_at")))
    job["is_rotational"] = bool(job.get("is_rotational"))
    job["is_internship"] = bool(job.get("is_internship"))
    return job


def _is_recent(first_seen: str | None, hours: int = 36) -> bool:
    if not first_seen:
        return False
    try:
        seen = datetime.fromisoformat(first_seen.replace("Z", ""))
    except ValueError:
        return False
    return (datetime.now() - seen).total_seconds() < hours * 3600


def render_dashboard(jobs: list[dict], stats: dict, output_dir: Path, templates_dir: Path) -> Path:
    output_dir = Path(output_dir)
    templates_dir = Path(templates_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    jobs = [_prepare(j) for j in jobs]
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("dashboard.html")

    static_dir = templates_dir.parent / "static"
    css = (static_dir / "style.css").read_text() if (static_dir / "style.css").exists() else ""
    js = (static_dir / "dashboard.js").read_text() if (static_dir / "dashboard.js").exists() else ""
    autofill_path = output_dir / "autofill.js"
    autofill_js = autofill_path.read_text() if autofill_path.exists() else ""

    # "</" inside inline <script> would end the tag early.
    jobs_json = json.dumps([{k: j.get(k) for k in _JSON_FIELDS} for j in jobs]).replace("</", "<\\/")

    html = template.render(
        jobs=jobs,
        stats=stats,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        lanes_sorted=sorted({j["matched_lane"] for j in jobs if j["matched_lane"]}),
        platforms_sorted=sorted({j["source_platform"] for j in jobs if j.get("source_platform")}),
        jobs_json=jobs_json,
        autofill_js=autofill_js,
        inline_css=css,
        inline_js=js.replace("</script", "<\\/script"),
    )

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    path = output_dir / f"dashboard_{stamp}.html"
    path.write_text(html, encoding="utf-8")
    (output_dir / "latest.html").write_text(html, encoding="utf-8")
    return path

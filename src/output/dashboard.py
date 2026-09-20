"""HTML dashboard renderer using the Jinja2 template."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader


def render_dashboard(
    jobs: list[dict],
    run_stats: dict,
    output_dir: Path,
    templates_dir: Path,
) -> Path:
    """Render the HTML dashboard from the Jinja2 template.

    Args:
        jobs: List of job dicts (from get_all_evaluated_jobs).
        run_stats: Dict with total_fetched, total_passed, total_new, total_companies.
        output_dir: Directory to write latest.html into.
        templates_dir: Directory containing dashboard.html template.

    Returns:
        Path to the written HTML file.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=False)
    template = env.get_template("dashboard.html")

    # Inline CSS if available
    static_dir = Path(templates_dir).parent / "static"
    inline_css = ""
    css_path = static_dir / "style.css"
    if css_path.exists():
        inline_css = css_path.read_text(encoding="utf-8")

    inline_js = ""
    js_path = static_dir / "dashboard.js"
    if js_path.exists():
        inline_js = js_path.read_text(encoding="utf-8")

    # Ensure all jobs have fields the template expects
    for job in jobs:
        job.setdefault("is_new", 0)
        job.setdefault("is_rotational", 0)
        job.setdefault("is_internship", 0)
        job.setdefault("sponsorship_flag", "RED")
        job.setdefault("location_parsed", job.get("location_raw", ""))
        job.setdefault("eval_global_score", None)
        job.setdefault("eval_action", "")
        job.setdefault("eval_reasoning", "")

    lanes_sorted = sorted(set(j.get("matched_lane", "") for j in jobs if j.get("matched_lane")))
    platforms_sorted = sorted(set(j.get("source_platform", "") for j in jobs if j.get("source_platform")))

    generated_at = datetime.now(timezone.utc).isoformat()

    autofill_js = ""
    autofill_path = output_dir / "autofill.js"
    if autofill_path.exists():
        autofill_js = autofill_path.read_text(encoding="utf-8")

    # Finding: jobs_json was referenced in the template's inline <script> but
    # never actually passed in, producing `const JOBS = ;` — a JS syntax error
    # that silently broke the entire script block (search/sort/filters/feedback/
    # autofill/Run Pipeline button all failed as a result). Serialize it properly.
    jobs_json = json.dumps(jobs, default=str)

    html = template.render(
        jobs=jobs,
        jobs_json=jobs_json,
        stats=run_stats,
        lanes_sorted=lanes_sorted,
        platforms_sorted=platforms_sorted,
        generated_at=generated_at,
        inline_css=inline_css,
        inline_js=inline_js,
        autofill_js=autofill_js,
    )

    out_path = output_dir / "latest.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path

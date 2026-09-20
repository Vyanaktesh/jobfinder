"""Markdown digest renderer for top matched jobs."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def render_digest(
    jobs: list[dict],
    run_stats: dict,
    output_dir: Path,
    top_n: int = 30,
) -> Path:
    """Render a markdown digest of the top jobs.

    Args:
        jobs: List of job dicts (from get_all_evaluated_jobs), already sorted by score.
        run_stats: Dict with total_fetched, total_passed, total_new, total_companies.
        output_dir: Directory to write digest.md into.
        top_n: Number of top jobs to include.

    Returns:
        Path to the written markdown file.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Job Digest — {now}",
        "",
        f"**{run_stats.get('total_passed', 0)}** jobs passed filters "
        f"({run_stats.get('total_new', 0)} new) "
        f"from {run_stats.get('total_companies', 0)} companies "
        f"out of {run_stats.get('total_fetched', 0)} fetched.",
        "",
        f"## Top {min(top_n, len(jobs))} Jobs",
        "",
    ]

    for job in jobs[:top_n]:
        score = job.get("eval_global_score") or job.get("match_score", 0)
        action = job.get("eval_action") or ""
        sponsor = job.get("sponsorship_flag", "RED")
        posted = (job.get("posted_at") or "")[:10] or "N/A"
        is_new = " 🆕" if job.get("is_new") else ""
        action_tag = f" `{action.upper()}`" if action else ""

        lines.append(
            f"### {job.get('company_name')} — {job.get('title')}{is_new}{action_tag}"
        )
        lines.append(
            f"- **Lane:** {job.get('matched_lane', 'N/A')}  "
            f"**Location:** {job.get('location_parsed') or job.get('location_raw', 'N/A')}  "
            f"**Sponsor:** {sponsor}  "
            f"**Score:** {score:.1f}  "
            f"**Posted:** {posted}"
        )
        if job.get("eval_reasoning"):
            lines.append(f"- *{job['eval_reasoning'][:200]}*")
        lines.append(f"- [Apply]({job.get('apply_url', '')})")
        lines.append("")

    digest_path = output_dir / "digest.md"
    digest_path.write_text("\n".join(lines), encoding="utf-8")
    return digest_path

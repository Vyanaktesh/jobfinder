"""Markdown digest of the best jobs from a run."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path


def render_digest(jobs: list[dict], stats: dict, output_dir: Path, top_n: int = 40) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now()

    ranked = sorted(jobs, key=lambda j: j.get("eval_global_score") or 0, reverse=True)
    lines = [
        f"# Job Digest — {now:%Y-%m-%d %H:%M}",
        "",
        f"{stats.get('total_passed', len(jobs))} jobs from {stats.get('total_companies', 0)} companies "
        f"({stats.get('total_new', 0)} new).",
        "",
        "| Score | Company | Title | Location | H1B | Link |",
        "|---|---|---|---|---|---|",
    ]
    for j in ranked[:top_n]:
        score = j.get("eval_global_score")
        lines.append(
            f"| {score:.1f} | {j['company_name']} | {j['title']} | {j.get('location_parsed') or ''} "
            f"| {j.get('sponsorship_flag') or ''} | [Apply]({j['apply_url']}) |"
            if score else
            f"| — | {j['company_name']} | {j['title']} | {j.get('location_parsed') or ''} "
            f"| {j.get('sponsorship_flag') or ''} | [Apply]({j['apply_url']}) |"
        )

    path = output_dir / f"digest_{now:%Y%m%d_%H%M}.md"
    text = "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8")
    (output_dir / "latest.md").write_text(text, encoding="utf-8")
    return path

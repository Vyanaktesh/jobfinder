"""Markdown digest of the best jobs from a run."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path


def render_digest(jobs: list[dict], stats: dict, output_dir: Path, top_n: int = 100) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now()

    lines = [
        f"# Job Digest — {now:%Y-%m-%d %H:%M}",
        "",
        f"{stats.get('total_passed', len(jobs))} jobs from {stats.get('total_companies', 0)} companies "
        f"({stats.get('total_new', 0)} new).",
        "",
        "| H1B | Company | Title | Location | Link |",
        "|---|---|---|---|---|",
    ]
    for j in jobs[:top_n]:
        flag = j.get("sponsorship_flag") or ""
        if j.get("h1b_count"):
            flag += f" ({j['h1b_count']})"
        lines.append(
            f"| {flag} | {j['company_name']} | {j['title']} | {j.get('location_parsed') or ''} "
            f"| [Apply]({j['apply_url']}) |"
        )

    path = output_dir / f"digest_{now:%Y%m%d_%H%M}.md"
    text = "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8")
    (output_dir / "latest.md").write_text(text, encoding="utf-8")
    return path

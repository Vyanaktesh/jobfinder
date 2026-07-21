"""One-click apply queue generator.

Produces a streamlined HTML page listing apply-tier jobs that are ready
to apply to — with tailored resume links, autofill script pre-loaded,
and batch-open capability. Designed to compress the per-application
workflow from ~5 minutes to ~30 seconds of review-and-submit.

Usage:
    python -m src.apply.queue              # Generate queue from DB
    python -m src.apply.queue --open       # Generate and open in browser
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = PROJECT_DIR / "data" / "jobs.db"
OUTPUT_DIR = PROJECT_DIR / "output"
RESUME_DIR = OUTPUT_DIR / "resumes"
AUTOFILL_JS_PATH = OUTPUT_DIR / "autofill.js"


def _get_apply_jobs() -> list[dict]:
    """Fetch apply-tier jobs ordered by score."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT j.id, j.company_name, j.title, j.apply_url, j.source_platform,
               j.location_parsed, j.matched_lane, j.sponsorship_flag, j.h1b_count,
               je.global_score, je.reasoning, je.recommended_action
        FROM jobs j
        JOIN job_evaluations je ON j.id = je.job_id
        WHERE je.recommended_action = 'apply' AND j.is_active = 1
        ORDER BY je.global_score DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _find_resume(company: str) -> str | None:
    """Find the most recent tailored resume PDF for a company."""
    if not RESUME_DIR.exists():
        return None
    company_clean = re.sub(r'[^\w\s-]', '', company).strip().replace(' ', '_')
    candidates = sorted(RESUME_DIR.glob(f"*{company_clean}*.pdf"), reverse=True)
    return candidates[0].name if candidates else None


def _load_autofill_js() -> str:
    """Load the autofill script."""
    if AUTOFILL_JS_PATH.exists():
        return AUTOFILL_JS_PATH.read_text()
    return ""


def generate_queue() -> Path:
    """Generate the apply queue HTML page."""
    jobs = _get_apply_jobs()
    autofill_js = _load_autofill_js()

    # Enrich with resume info
    for job in jobs:
        job["resume_file"] = _find_resume(job["company_name"])
        score = job.get("global_score")
        job["score_display"] = f"{score:.1f}" if score else "?"
        # Sponsor badge
        flag = job.get("sponsorship_flag", "RED")
        job["sponsor_class"] = {"GREEN": "sp-green", "YELLOW": "sp-yellow", "NA": "sp-na"}.get(flag, "sp-red")

    ready = [j for j in jobs if j["resume_file"]]
    no_resume = [j for j in jobs if not j["resume_file"]]

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Apply Queue — {generated_at}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f1117; color: #e1e4e8; padding: 24px; }}
h1 {{ font-size: 24px; font-weight: 700; color: #f0f3f6; }}
.subtitle {{ color: #8b949e; font-size: 14px; margin-top: 4px; margin-bottom: 20px; }}
.actions-bar {{ display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; align-items: center; }}
.btn {{ padding: 10px 20px; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; border: none; transition: all 0.15s; }}
.btn-primary {{ background: #238636; color: #fff; }}
.btn-primary:hover {{ background: #2ea043; }}
.btn-secondary {{ background: #21262d; color: #c9d1d9; border: 1px solid #30363d; }}
.btn-secondary:hover {{ background: #30363d; }}
.btn-copy {{ background: #1f6feb; color: #fff; }}
.btn-copy:hover {{ background: #388bfd; }}
.stats {{ font-size: 13px; color: #8b949e; margin-left: auto; }}

.section-label {{ font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: #8b949e; margin: 20px 0 10px; }}
.queue {{ display: flex; flex-direction: column; gap: 8px; max-width: 1000px; }}

.job-card {{
  background: #161b22; border: 1px solid #30363d; border-radius: 8px;
  padding: 14px 18px; display: flex; align-items: center; gap: 14px;
  transition: all 0.15s;
}}
.job-card:hover {{ border-color: #58a6ff; }}
.job-card.done {{ opacity: 0.4; border-color: #238636; }}
.job-card.skipped {{ opacity: 0.3; }}

.job-num {{ font-size: 18px; font-weight: 700; color: #484f58; min-width: 28px; text-align: center; }}
.job-card.done .job-num {{ color: #3fb950; }}

.job-info {{ flex: 1; min-width: 0; }}
.job-title {{ font-size: 15px; font-weight: 600; color: #f0f3f6; }}
.job-company {{ font-size: 13px; color: #8b949e; margin-top: 2px; }}
.job-meta {{ display: flex; gap: 8px; margin-top: 4px; font-size: 12px; }}
.tag {{ padding: 1px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
.tag-score {{ background: rgba(63, 185, 80, 0.15); color: #3fb950; }}
.tag-lane {{ background: #21262d; color: #c9d1d9; }}
.sp-green {{ background: rgba(63, 185, 80, 0.15); color: #3fb950; }}
.sp-yellow {{ background: rgba(210, 153, 34, 0.15); color: #d29922; }}
.sp-red {{ background: rgba(248, 81, 73, 0.15); color: #f85149; }}
.sp-na {{ background: rgba(88, 166, 255, 0.15); color: #58a6ff; }}
.tag-resume {{ background: rgba(63, 185, 80, 0.15); color: #3fb950; }}
.tag-no-resume {{ background: rgba(248, 81, 73, 0.1); color: #484f58; }}

.job-actions {{ display: flex; gap: 6px; flex-shrink: 0; }}
.btn-sm {{ padding: 5px 12px; border-radius: 6px; font-size: 12px; font-weight: 600; cursor: pointer; border: none; }}
.btn-apply {{ background: #238636; color: #fff; }}
.btn-apply:hover {{ background: #2ea043; }}
.btn-autofill {{ background: #1f6feb; color: #fff; }}
.btn-autofill:hover {{ background: #388bfd; }}
.btn-done {{ background: #21262d; color: #8b949e; border: 1px solid #30363d; }}
.btn-done:hover {{ background: #30363d; color: #e1e4e8; }}
.btn-skip {{ background: transparent; color: #484f58; border: 1px solid #21262d; }}
.btn-skip:hover {{ color: #f85149; border-color: #f85149; }}

.instructions {{
  background: #161b22; border: 1px solid #30363d; border-radius: 8px;
  padding: 16px 20px; margin-bottom: 20px; max-width: 1000px;
}}
.instructions h3 {{ font-size: 14px; margin-bottom: 8px; color: #f0f3f6; }}
.instructions ol {{ padding-left: 20px; line-height: 2; font-size: 13px; color: #8b949e; }}
.instructions kbd {{
  background: #21262d; border: 1px solid #30363d; border-radius: 4px;
  padding: 1px 5px; font-family: 'SF Mono', monospace; font-size: 11px; color: #c9d1d9;
}}

.toast {{
  position: fixed; bottom: 24px; right: 24px; background: #1f2937;
  color: #fff; padding: 12px 20px; border-radius: 8px; font-size: 14px;
  z-index: 999; opacity: 0; transform: translateY(10px);
  transition: opacity 0.2s, transform 0.2s; pointer-events: none;
}}
.toast.show {{ opacity: 1; transform: translateY(0); }}

.progress-bar {{ width: 100%; height: 4px; background: #21262d; border-radius: 2px; margin-bottom: 16px; }}
.progress-fill {{ height: 100%; background: #238636; border-radius: 2px; transition: width 0.3s; }}
</style>
</head>
<body>

<h1>Apply Queue</h1>
<p class="subtitle">Generated {generated_at} — {len(ready)} ready to apply, {len(no_resume)} need resumes</p>

<div class="progress-bar"><div class="progress-fill" id="progressFill" style="width: 0%"></div></div>

<div class="actions-bar">
  <button class="btn btn-primary" onclick="openAllReady()">Open All Ready ({len(ready)}) in Tabs</button>
  <button class="btn btn-copy" onclick="copyAutofill()">Copy Autofill to Clipboard</button>
  <button class="btn btn-secondary" onclick="resetAll()">Reset All</button>
  <span class="stats" id="statsBar">0 / {len(ready)} done</span>
</div>

<div class="instructions">
  <h3>Workflow per job:</h3>
  <ol>
    <li>Click <b>Apply</b> to open the application (or use "Open All" above)</li>
    <li>Click <b>Autofill</b> → open console (<kbd>Cmd</kbd>+<kbd>Option</kbd>+<kbd>J</kbd>) → paste (<kbd>Cmd</kbd>+<kbd>V</kbd>) → <kbd>Enter</kbd></li>
    <li>Upload the tailored resume (click the green <b>PDF</b> tag to copy filename)</li>
    <li>Review all fields — especially sponsorship answers</li>
    <li>Submit the application</li>
    <li>Click <b>Done ✓</b> to mark complete</li>
  </ol>
</div>

<div class="section-label">Ready to apply ({len(ready)})</div>
<div class="queue" id="readyQueue">
"""

    for i, job in enumerate(ready, 1):
        html += _render_job_card(job, i, has_resume=True)

    html += "</div>\n"

    if no_resume:
        html += f'\n<div class="section-label">Missing resume ({len(no_resume)})</div>\n'
        html += '<div class="queue" id="noResumeQueue">\n'
        for i, job in enumerate(no_resume, len(ready) + 1):
            html += _render_job_card(job, i, has_resume=False)
        html += "</div>\n"

    autofill_escaped = json.dumps(autofill_js)
    urls_json = json.dumps([j["apply_url"] for j in ready])

    html += f"""
<div class="toast" id="toast"></div>

<script>
const AUTOFILL_JS = {autofill_escaped};
const READY_URLS = {urls_json};
const TOTAL_READY = {len(ready)};
const STATUS_KEY = 'apply_queue_status';

function loadStatuses() {{
  try {{ return JSON.parse(localStorage.getItem(STATUS_KEY) || '{{}}'); }} catch {{ return {{}}; }}
}}

function saveStatus(jobId, status) {{
  const s = loadStatuses();
  s[jobId] = status;
  localStorage.setItem(STATUS_KEY, JSON.stringify(s));
  updateUI();
}}

function updateUI() {{
  const s = loadStatuses();
  let doneCount = 0;
  document.querySelectorAll('.job-card').forEach(card => {{
    const id = card.dataset.jobid;
    const status = s[id];
    card.classList.remove('done', 'skipped');
    if (status === 'done') {{ card.classList.add('done'); doneCount++; }}
    if (status === 'skipped') {{ card.classList.add('skipped'); }}
  }});
  document.getElementById('statsBar').textContent = doneCount + ' / ' + TOTAL_READY + ' done';
  const pct = TOTAL_READY > 0 ? (doneCount / TOTAL_READY * 100) : 0;
  document.getElementById('progressFill').style.width = pct + '%';
}}

function markDone(jobId) {{
  const s = loadStatuses();
  if (s[jobId] === 'done') {{
    delete s[jobId];
    localStorage.setItem(STATUS_KEY, JSON.stringify(s));
  }} else {{
    saveStatus(jobId, 'done');
  }}
  updateUI();
}}

function markSkip(jobId) {{
  const s = loadStatuses();
  if (s[jobId] === 'skipped') {{
    delete s[jobId];
    localStorage.setItem(STATUS_KEY, JSON.stringify(s));
  }} else {{
    saveStatus(jobId, 'skipped');
  }}
  updateUI();
}}

function openAllReady() {{
  const s = loadStatuses();
  let opened = 0;
  READY_URLS.forEach((url, i) => {{
    const card = document.querySelectorAll('#readyQueue .job-card')[i];
    const id = card?.dataset.jobid;
    if (id && s[id]) return; // skip already done/skipped
    window.open(url, '_blank');
    opened++;
  }});
  showToast('Opened ' + opened + ' tabs — paste autofill in each');
}}

function copyAutofill() {{
  if (!AUTOFILL_JS) {{
    showToast('No autofill script — run: python -m src.apply.bookmarklet');
    return;
  }}
  navigator.clipboard.writeText(AUTOFILL_JS).then(() => {{
    showToast('Autofill copied! Paste in each tab\\'s console (Cmd+Option+J)');
  }});
}}

function copyFilename(filename) {{
  navigator.clipboard.writeText(filename).then(() => {{
    showToast('Filename copied: ' + filename);
  }});
}}

function resetAll() {{
  if (!confirm('Reset all apply statuses?')) return;
  localStorage.removeItem(STATUS_KEY);
  updateUI();
}}

function showToast(msg) {{
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3500);
}}

updateUI();
</script>
</body>
</html>"""

    output_path = OUTPUT_DIR / "apply_queue.html"
    output_path.write_text(html)
    return output_path


def _render_job_card(job: dict, num: int, has_resume: bool) -> str:
    """Render a single job card HTML."""
    resume_tag = ""
    if has_resume:
        resume_tag = (
            f'<span class="tag tag-resume" style="cursor:pointer" '
            f'onclick="copyFilename(\'{job["resume_file"]}\')" '
            f'title="Click to copy filename">PDF ✓</span>'
        )
    else:
        resume_tag = '<span class="tag tag-no-resume">No resume</span>'

    return f"""  <div class="job-card" data-jobid="{job['id']}">
    <span class="job-num">{num}</span>
    <div class="job-info">
      <div class="job-title">{job['title']}</div>
      <div class="job-company">{job['company_name']} — {job.get('location_parsed') or 'N/A'}</div>
      <div class="job-meta">
        <span class="tag tag-score">{job['score_display']}</span>
        <span class="tag tag-lane">{job.get('matched_lane', '')}</span>
        <span class="tag {job['sponsor_class']}">{job.get('sponsorship_flag', 'RED')}</span>
        {resume_tag}
      </div>
    </div>
    <div class="job-actions">
      <a class="btn-sm btn-apply" href="{job['apply_url']}" target="_blank">Apply</a>
      <button class="btn-sm btn-autofill" onclick="copyAutofill()">Autofill</button>
      <button class="btn-sm btn-done" onclick="markDone({job['id']})">Done ✓</button>
      <button class="btn-sm btn-skip" onclick="markSkip({job['id']})">Skip</button>
    </div>
  </div>
"""


def main():
    parser = argparse.ArgumentParser(description="Generate the one-click apply queue")
    parser.add_argument("--open", action="store_true", help="Open in browser after generating")
    args = parser.parse_args()

    path = generate_queue()
    print(f"Apply queue written to: {path}")
    print(f"  Ready jobs with resumes are at the top")
    print(f"  Open in browser: open {path}")

    if args.open:
        subprocess.run(["open", str(path)])


if __name__ == "__main__":
    main()

"""Render a resume JSON (+ profile.json) into LaTeX. Output is identical to the original
kit renderer for normal text; escaping additionally covers { } ~ ^ so pasted text can't
unbalance the document."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from src.resume_studio import paths

_BASE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,60}$")

_ESCAPES = {
    "\\": "\\textbackslash{}", "%": "\\%", "$": "\\$", "&": "\\&", "#": "\\#", "_": "\\_",
    "{": "\\{", "}": "\\}", "~": "\\textasciitilde{}", "^": "\\textasciicircum{}",
}


def esc(s: str) -> str:
    """Escape LaTeX specials in one pass, then turn **x** into \\textbf{x}."""
    s = "".join(_ESCAPES.get(ch, ch) for ch in s)
    return re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", s)


def items(lst, indent):
    return "\n".join(f"{indent}\\resumeItem{{{esc(x)}}}" for x in lst)


def role(r):
    return (f"    \\resumeSubheading\n      {{{esc(r['title'])}}}{{{esc(r['date'])}}}\n"
            f"      {{{esc(r['org'])}}}{{{esc(r['loc'])}}}\n      \\resumeItemListStart\n"
            f"{items(r['bullets'], '        ')}\n      \\resumeItemListEnd\n")


def project(p):
    tag = f" $|$ \\emph{{{esc(p['tag'])}}}" if p.get("tag") else ""
    return (f"      \\resumeProjectHeading\n          {{\\textbf{{{esc(p['name'])}}} $|$ "
            f"\\emph{{{esc(p['stack'])}}}{tag}}}{{}}\n          \\resumeItemListStart\n"
            f"{items(p['bullets'], '            ')}\n          \\resumeItemListEnd\n")


def tagline_tex(t):
    parts = [esc(x.strip()) for x in t.split("|")]
    return " \\ $|$ \\ ".join(parts)


def apply_patch(res: dict, patch: dict) -> dict:
    """Return a patched copy. Patch keys: tagline, skills, experience_bullets{index:[...]}, projects."""
    res = copy.deepcopy(res)
    for k in ("tagline", "skills", "projects"):
        if k in patch:
            res[k] = copy.deepcopy(patch[k])
    for idx, bullets in (patch.get("experience_bullets") or {}).items():
        res["experience"][int(idx)]["bullets"] = list(bullets)
    return res


def to_tex(res: dict, prof: dict) -> str:
    pre = (paths.TEMPLATE_DIR / "preamble.tex").read_text(encoding="utf-8")
    pre = pre.replace("{{PDF_TITLE}}", res["pdf_title"].replace("{", "").replace("}", ""))
    pre = pre.replace("{{PDF_AUTHOR}}", prof["name"].replace("{", "").replace("}", ""))
    sk = " \\\\\n".join(f"     \\textbf{{{esc(s['label'])}}}{{: {esc(s['items'])}}}" for s in res["skills"])
    edu = "".join(
        f"    \\resumeSubheading\n      {{{esc(e['school'])}}}{{{esc(e['loc'])}}}\n"
        f"      {{{esc(e['degree'])}}}{{{esc(e['date'])}}}\n" for e in prof["education"])
    body = f"""\\begin{{document}}

\\begin{{center}}
    \\textbf{{\\Huge \\scshape {esc(prof['name'])}}} \\\\ \\vspace{{2pt}}
    \\small {esc(prof['location'])} $|$ {esc(prof['phone'])} $|$
    \\href{{mailto:{prof['email']}}}{{\\underline{{{prof['email']}}}}} $|$
    \\href{{https://{prof['linkedin']}}}{{\\underline{{{prof['linkedin']}}}}} $|$
    \\href{{https://{prof['github']}}}{{\\underline{{{prof['github']}}}}} \\\\ \\vspace{{3pt}}
    \\small \\textit{{{tagline_tex(res['tagline'])}}}
\\end{{center}}

\\section{{Education}}
  \\resumeSubHeadingListStart
{edu}  \\resumeSubHeadingListEnd

\\section{{Technical Skills}}
 \\begin{{itemize}}[leftmargin=0.15in, label={{}}]
    \\small{{\\item{{
{sk}}}}}
 \\end{{itemize}}

\\section{{Experience}}
  \\resumeSubHeadingListStart

{chr(10).join(role(r) for r in res['experience'])}
  \\resumeSubHeadingListEnd

\\section{{Projects}}
    \\resumeSubHeadingListStart
{''.join(project(p) for p in res['projects'])}    \\resumeSubHeadingListEnd

\\end{{document}}
"""
    return pre + body


# ---- data access -------------------------------------------------------------

def load_profile(data_dir: Path | None = None) -> dict:
    d = Path(data_dir or paths.data_dir())
    return json.loads((d / "profile.json").read_text(encoding="utf-8"))


def list_bases(data_dir: Path | None = None) -> list[dict]:
    d = Path(data_dir or paths.data_dir())
    out = []
    for f in sorted(d.glob("*.json")):
        if f.name == "profile.json" or not _BASE_ID_RE.match(f.stem):
            continue
        try:
            res = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({"id": f.stem, "pdf_title": res.get("pdf_title", f.stem), "tagline": res.get("tagline", "")})
    return out


def load_base(base_id: str, data_dir: Path | None = None) -> dict:
    """Load one base resume. The id is validated so it can never point outside the data folder."""
    if not isinstance(base_id, str) or not _BASE_ID_RE.match(base_id) or base_id == "profile":
        raise KeyError(f"Unknown base resume: {base_id!r}")
    f = Path(data_dir or paths.data_dir()) / f"{base_id}.json"
    if not f.is_file():
        raise KeyError(f"Unknown base resume: {base_id!r}")
    return json.loads(f.read_text(encoding="utf-8"))


def render_tex(base_id: str, patch: dict | None = None, data_dir: Path | None = None) -> tuple[str, dict]:
    """Return (tex, final_resume) for a base plus an optional (already validated) patch."""
    res = load_base(base_id, data_dir)
    if patch:
        res = apply_patch(res, patch)
    return to_tex(res, load_profile(data_dir)), res

"""Glue used by the control server: validate -> render -> compile -> save -> record."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from src.resume_studio import paths, render
from src.resume_studio.bullet_bank import load_bank
from src.resume_studio.compile import compile_tex
from src.resume_studio.fit import fit_report
from src.resume_studio.store import inside, unique_stem
from src.resume_studio.validate import validate_patch

_build_lock = threading.Lock()   # LaTeX builds are serialized
MAX_FIELD = 200
MAX_JD = 60_000


def _clip(v, limit):
    return ("" if v is None else str(v)).strip()[:limit]


def validate(base_id: str, patch, data_dir: Path | None = None, strict: bool = False) -> dict:
    """Validate a patch for a base. Returns {"ok", "errors", "warnings", "not_in_bank", "base"}.

    Only structural problems and LaTeX injection block. The bullet bank is advice unless `strict`.
    """
    d = Path(data_dir or paths.data_dir())
    chosen = base_id
    if isinstance(patch, dict) and isinstance(patch.get("base"), str) and patch["base"]:
        chosen = patch["base"]
    try:
        base_res = render.load_base(chosen, d)
    except KeyError as e:
        return {"ok": False, "errors": [str(e).strip("'\"")], "warnings": [], "not_in_bank": [], "base": chosen}
    errors, warnings, not_in_bank = validate_patch(patch, base_res, load_bank(d / "BULLET_BANK.md"), strict=strict)
    if not (d / "BULLET_BANK.md").is_file():
        warnings.append("BULLET_BANK.md not found, so numbers and tools could not be compared.")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "not_in_bank": not_in_bank, "base": chosen}


def build(db, req: dict, data_dir: Path | None = None, output_dir: Path | None = None) -> dict:
    """Build (and save) a tailored resume. `req`: base, patch, company, role, job_id?, jd_text?, strict?"""
    d = Path(data_dir or paths.data_dir())
    out_dir = Path(output_dir or paths.OUTPUT_DIR)
    patch = req.get("patch") or {}
    check = validate(req.get("base") or "", patch, d, strict=bool(req.get("strict")))
    if not check["ok"]:
        return {"ok": False, **check}
    base_id = check["base"]
    company, role = _clip(req.get("company"), MAX_FIELD), _clip(req.get("role"), MAX_FIELD)

    tex, final_res = render.render_tex(base_id, patch, d)
    with _build_lock:
        result = compile_tex(tex)

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = unique_stem(out_dir, company, role)
    tex_path = out_dir / f"{stem}.tex"
    tex_path.write_text(tex, encoding="utf-8")
    pdf_path = None
    if result.ok and result.pdf:
        pdf_path = out_dir / f"{stem}.pdf"
        pdf_path.write_bytes(result.pdf)

    warnings = list(check["warnings"])
    if result.message:
        warnings.append(result.message)
    fit = None
    if result.ok and result.pdf:
        bullets = [b for e in final_res["experience"] for b in e["bullets"]] + \
                  [b for p in final_res["projects"] for b in p["bullets"]]
        fit = fit_report(result.pdf, result.pages, bullets)

    job_id = req.get("job_id")
    rid = db.add_tailored_resume({
        "job_id": job_id if isinstance(job_id, int) else None,
        "company": company, "role": role, "base_id": base_id,
        "jd_text": _clip(req.get("jd_text"), MAX_JD),
        "patch_json": json.dumps(patch),
        "tex_path": str(tex_path), "pdf_path": str(pdf_path) if pdf_path else None,
        "pages": result.pages,
    })
    return {
        "ok": True, "id": rid, "base": base_id, "pages": result.pages, "pdf_built": bool(pdf_path),
        "tex_path": str(tex_path), "pdf_path": str(pdf_path) if pdf_path else None,
        "errors": [], "warnings": warnings, "not_in_bank": check["not_in_bank"], "fit": fit, "engine": result.engine,
        "missing_keywords": patch.get("missing_keywords", []), "match_notes": patch.get("match_notes", ""),
    }


def file_for(db, resume_id: int, ext: str, output_dir: Path | None = None) -> tuple[Path, str] | None:
    """Resolve a saved build file for download; only files inside output/resumes are served."""
    if ext not in ("tex", "pdf"):
        return None
    rec = db.get_tailored_resume(resume_id)
    if not rec:
        return None
    p = rec.get("pdf_path" if ext == "pdf" else "tex_path")
    if not p:
        return None
    path = Path(p)
    if not path.is_file() or not inside(path, Path(output_dir or paths.OUTPUT_DIR)):
        return None
    return path, ("application/pdf" if ext == "pdf" else "text/plain; charset=utf-8")

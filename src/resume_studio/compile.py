"""Compile LaTeX to PDF safely: argument list (no shell), no shell-escape, temp dir, 30 s timeout."""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

TIMEOUT_SECONDS = 30
NO_LATEX_MESSAGE = ("No LaTeX compiler found (pdflatex or tectonic). The .tex file was still saved: open it in Overleaf, "
                    "or install MiKTeX (Windows) / TeX Live (Mac, Linux) and build again.")


@dataclass
class CompileResult:
    ok: bool
    pdf: bytes | None = None
    pages: int | None = None
    message: str = ""
    engine: str = ""


def _windows_candidates() -> list[Path]:
    out = []
    for var in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(var)
        if not base:
            continue
        rel = Path("Programs") / "MiKTeX" if var == "LOCALAPPDATA" else Path("MiKTeX")
        out.append(Path(base) / rel / "miktex" / "bin" / "x64" / "pdflatex.exe")
    return out


def find_latex() -> tuple[str, str] | None:
    """Return (engine_name, executable) or None."""
    override = os.environ.get("RESUME_STUDIO_LATEX")
    if override and Path(override).is_file():
        return Path(override).stem.lower(), override
    for name in ("pdflatex", "tectonic"):
        exe = shutil.which(name)
        if exe:
            return name, exe
    if os.name == "nt":
        for c in _windows_candidates():
            if c.is_file():
                return "pdflatex", str(c)
    return None


def count_pages(pdf: bytes) -> int | None:
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        return len(PdfReader(io.BytesIO(pdf)).pages)
    except Exception:  # noqa: BLE001  (corrupt PDF)
        return None


def _latex_errors(log: str) -> str:
    lines = [ln for ln in log.splitlines() if ln.startswith("!")]
    return "; ".join(lines[:3]) or "see the .tex in Overleaf for details"


def compile_tex(tex: str, timeout: int = TIMEOUT_SECONDS) -> CompileResult:
    found = find_latex()
    if not found:
        return CompileResult(False, message=NO_LATEX_MESSAGE)
    engine, exe = found
    with tempfile.TemporaryDirectory(prefix="resume_studio_") as tmp:
        tmpdir = Path(tmp)
        (tmpdir / "resume.tex").write_text(tex, encoding="utf-8")
        if engine == "tectonic":
            cmd = [exe, "resume.tex"]                       # tectonic has shell-escape off by default
        else:
            cmd = [exe, "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", "resume.tex"]
        try:
            proc = subprocess.run(cmd, cwd=tmpdir, capture_output=True, timeout=timeout,
                                  stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return CompileResult(False, message=f"LaTeX took longer than {timeout} seconds and was stopped.", engine=engine)
        except OSError as e:
            return CompileResult(False, message=f"Could not run {engine}: {e}", engine=engine)
        pdf_path = tmpdir / "resume.pdf"
        if not pdf_path.is_file():
            log = ""
            log_file = tmpdir / "resume.log"
            if log_file.is_file():
                log = log_file.read_text(encoding="utf-8", errors="replace")
            elif proc.stdout:
                log = proc.stdout.decode("utf-8", "replace")
            return CompileResult(False, message=f"LaTeX could not build the PDF: {_latex_errors(log)}", engine=engine)
        pdf = pdf_path.read_bytes()
    pages = count_pages(pdf)
    return CompileResult(True, pdf=pdf, pages=pages, engine=engine,
                         message="" if pages is not None else "Built, but the page count could not be checked (pip install pypdf).")

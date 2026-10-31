"""After compiling: does the resume fit on one page, and which bullet should be shortened?

Text is read from the PDF with `pdftotext` when it is installed (poppler), otherwise with pypdf
(MiKTeX on Windows does not ship pdftotext, so the pypdf path is the common one there).
"""
from __future__ import annotations

import bisect
import io
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path

SHORT_LAST_LINE = 34          # a wrapped bullet whose last line is shorter than this wastes a line
PAGE2_LINES = 3
_BULLET_GLYPHS = "•●∙·▪◦*-"


def extract_pages(pdf: bytes) -> tuple[list[list[str]], str]:
    """Return (lines per page, source) where source is 'pdftotext' or 'pypdf'. Empty lines are dropped."""
    exe = shutil.which("pdftotext")
    if exe:
        try:
            with tempfile.TemporaryDirectory(prefix="resume_fit_") as tmp:
                f = Path(tmp) / "resume.pdf"
                f.write_bytes(pdf)
                out = subprocess.run([exe, "-layout", str(f), "-"], capture_output=True, timeout=20,
                                     stdin=subprocess.DEVNULL)
            if out.returncode == 0 and out.stdout.strip():
                pages = out.stdout.decode("utf-8", "replace").split("\f")
                if pages and not pages[-1].strip():
                    pages.pop()
                return [[ln.strip() for ln in p.splitlines() if ln.strip()] for p in pages], "pdftotext"
        except (OSError, subprocess.SubprocessError):
            pass
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(pdf))
    return [[ln.strip() for ln in (pg.extract_text() or "").splitlines() if ln.strip()] for pg in reader.pages], "pypdf"


def _norm(s: str) -> str:
    """Lower-case letters and digits only, so kerning gaps ('T echnology') and glyph differences don't matter."""
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", s).lower())


def analyze(pages: list[list[str]], bullets: list[str]) -> dict:
    """Page-2 start lines and wrapped bullets whose last line is short, found by locating each bullet in the text."""
    lines = [(pi, text) for pi, page in enumerate(pages) for text in page]
    norms = [_norm(t) for _, t in lines]
    starts, total = [], 0
    for n in norms:
        starts.append(total)
        total += len(n)
    joined = "".join(norms)

    short: list[dict] = []
    pos = 0
    for raw in bullets:
        text = raw.replace("**", "").strip()
        nb = _norm(text)
        if len(nb) < 30:
            continue
        a = joined.find(nb[:24], pos)
        if a < 0:
            continue
        b = joined.find(nb[-24:], a)
        if b < 0:
            continue
        pos = b + 24
        first = bisect.bisect_right(starts, a) - 1
        last = bisect.bisect_right(starts, b + 23) - 1
        if last <= first:
            continue                                   # one-line bullet: nothing to save
        last_text = lines[last][1].lstrip(_BULLET_GLYPHS + " ")
        if len(last_text) < SHORT_LAST_LINE:
            short.append({"bullet": text[:70] + ("..." if len(text) > 70 else ""), "last_line": last_text,
                          "chars": len(last_text), "page": lines[last][0] + 1})
    page2 = pages[1][:PAGE2_LINES] if len(pages) > 1 else []
    return {"page2_lines": page2, "short_last_lines": short}


def fit_report(pdf: bytes, pages_count: int | None, bullets: list[str]) -> dict:
    """Everything the page needs to show for the fit check. Never raises."""
    report = {"pages": pages_count, "page2_lines": [], "short_last_lines": [], "source": None, "banner": ""}
    try:
        pages, source = extract_pages(pdf)
        report.update(analyze(pages, bullets), source=source)
        if pages_count is None:
            report["pages"] = len(pages)
    except Exception as e:  # noqa: BLE001  (a PDF we can't read must not break a successful build)
        report["error"] = f"{type(e).__name__}: {e}"
    n = report["pages"]
    if n and n > 1:
        report["banner"] = f"Resume is {n} pages"
    return report

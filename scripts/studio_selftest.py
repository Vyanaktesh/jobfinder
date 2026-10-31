#!/usr/bin/env python3
"""Check that Resume Studio works on this machine, end to end, and print a report.

  python scripts/studio_selftest.py

It checks: your resume data loads, every base passes the validator, LaTeX (MiKTeX/TeX Live)
is found, and every base builds to a one-page PDF with readable text. Nothing is saved to
your build history; PDFs go to output/selftest/. The first run can take a few minutes while
MiKTeX downloads packages.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.resume_studio import paths, render, service  # noqa: E402
from src.resume_studio.compile import compile_tex, find_latex  # noqa: E402

BUILD_TIMEOUT = 240      # generous: the first MiKTeX run downloads packages
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = ""):
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def main() -> int:
    d = paths.data_dir()
    print(f"Data folder: {d}")
    check("Using YOUR data (not the sample)", not paths.using_sample(),
          "run scripts/import_resume_kit.py first" if paths.using_sample() else "")
    bases, problems = render.scan_bases(d)
    check("Base resumes found", len(bases) > 0, ", ".join(b["id"] for b in bases) or "none")
    for p in problems:
        check("Resume file readable", False, p)
    check("BULLET_BANK.md present", (d / "BULLET_BANK.md").is_file())

    print("\nValidator (each base's own content must pass):")
    for b in bases:
        res = render.load_base(b["id"], d)
        patch = {"base": b["id"], "tagline": res["tagline"], "skills": res["skills"],
                 "experience_bullets": {"0": res["experience"][0]["bullets"]}, "projects": res["projects"]}
        v = service.validate(b["id"], patch, d)
        check(f"{b['id']} passes the validator", v["ok"], "; ".join(v["errors"][:2]))

    print("\nLaTeX:")
    latex = find_latex()
    check("LaTeX compiler found", latex is not None, f"{latex[0]}: {latex[1]}" if latex else
          "install MiKTeX, open a NEW terminal, and run this again")
    if latex:
        out = ROOT / "output" / "selftest"
        out.mkdir(parents=True, exist_ok=True)
        print("\nBuilding each base (first run may take a few minutes):")
        for b in bases:
            tex, _ = render.render_tex(b["id"], None, d)
            t0 = time.time()
            r = compile_tex(tex, timeout=BUILD_TIMEOUT)
            secs = f"{time.time() - t0:.0f}s"
            if not r.ok:
                check(f"{b['id']} builds", False, r.message)
                continue
            (out / f"{b['id']}.pdf").write_bytes(r.pdf)
            check(f"{b['id']} is exactly 1 page", r.pages == 1, f"{r.pages} page(s), {secs}")
            try:
                from pypdf import PdfReader
                import io
                text = "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(r.pdf)).pages)
                name = render.load_profile(d)["name"].split()[0]
                check(f"{b['id']} text is readable", name in text and len(text) > 500, f"{len(text)} characters")
            except ImportError:
                check(f"{b['id']} text is readable", False, "pip install pypdf")

    print("\nDashboard server (optional):")
    try:
        with urllib.request.urlopen("http://localhost:8765/api/resumes", timeout=3) as resp:
            api = json.load(resp)
        check("Server lists your resumes", len(api.get("bases", [])) == len(bases),
              f"{len(api.get('bases', []))} listed, latex={api.get('latex')}")
    except Exception as e:  # noqa: BLE001
        print(f"  --    server not reachable ({type(e).__name__}); start .\\start_dashboard.bat to test the page too")

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed.")
    if latex:
        print(f"PDFs are in {ROOT / 'output' / 'selftest'}: open one to look at it.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

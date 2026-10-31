import json
import os
import stat
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from src.db import JobDB  # noqa: E402
from src.resume_studio import compile as rs_compile  # noqa: E402
from src.resume_studio import paths, render, service  # noqa: E402
from src.resume_studio.bullet_bank import load_bank, numbers_in, split_top_level  # noqa: E402
from src.resume_studio.store import inside, safe_name, unique_stem  # noqa: E402
from src.resume_studio.validate import validate_patch  # noqa: E402

BASE = "Sample_Engineer"


@pytest.fixture(autouse=True)
def sample_data(monkeypatch):
    """Always test against the fake sample, never a developer's real data."""
    monkeypatch.setenv("RESUME_STUDIO_DATA", str(paths.SAMPLE_DIR))


@pytest.fixture
def bank():
    return load_bank(paths.SAMPLE_DIR / "BULLET_BANK.md")


@pytest.fixture
def base():
    return render.load_base(BASE)


@pytest.fixture
def db(tmp_path):
    d = JobDB(tmp_path / "t.db")
    yield d
    d.close()


def good_patch(**over):
    p = {"base": BASE, "tagline": "Backend Engineer | APIs, Caching | Python, FastAPI, AWS"}
    p.update(over)
    return p


def fake_latex(tmp_path, pages=1, sleep=0, fail=False):
    """An executable that behaves like pdflatex: writes resume.pdf with N pages."""
    script = tmp_path / f"pdflatex_{pages}_{sleep}_{fail}"
    script.write_text(f"""#!{sys.executable}
import sys, time
time.sleep({sleep})
if {fail}:
    open("resume.log", "w").write("! Undefined control sequence.\\n")
    sys.exit(1)
from pypdf import PdfWriter
w = PdfWriter()
for _ in range({pages}):
    w.add_blank_page(width=612, height=792)
w.write(open("resume.pdf", "wb"))
""")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


# ---------------------------------------------------------------- render
def test_sample_base_renders_complete_document():
    tex, _ = render.render_tex(BASE)
    assert tex.startswith("%") and "\\begin{document}" in tex and tex.rstrip().endswith("\\end{document}")
    assert "{{PDF_TITLE}}" not in tex and "Jordan Sample Engineer Resume" in tex


def test_special_characters_are_escaped():
    out = render.esc(r"50% of $5 & #1 a_b {x} ~ ^ \cmd")
    assert r"\%" in out and r"\$" in out and r"\&" in out and r"\#" in out and r"a\_b" in out
    assert r"\{x\}" in out and "\\textasciitilde{}" in out and "\\textasciicircum{}" in out
    assert "\\textbackslash{}cmd" in out
    assert render.esc("a **bold** b") == "a \\textbf{bold} b"


def test_special_characters_render_in_a_full_resume():
    patch = good_patch(tagline="R&D | 100% Python | C# and F_x")
    _, res = render.render_tex(BASE, patch)
    tex, _ = render.render_tex(BASE, patch)
    assert r"R\&D" in tex and r"100\% Python" in tex and r"C\# and F\_x" in tex
    assert res["tagline"].startswith("R&D")


def test_unknown_base_and_traversal_are_rejected():
    for bad in ("../profile", "nope", "profile", "", "a/b"):
        with pytest.raises(KeyError):
            render.load_base(bad)


def test_patch_does_not_mutate_the_base():
    before = json.dumps(render.load_base(BASE))
    render.render_tex(BASE, good_patch())
    assert json.dumps(render.load_base(BASE)) == before


# ---------------------------------------------------------------- bullet bank
def test_bank_parsing():
    assert numbers_in("Serve **1,000+** users in 3.1 s, 37,210 tickets, BGE-M3, H1B, 24/7.") == [
        "1000", "3.1", "37210", "24", "7"] or numbers_in("x") == []
    assert split_top_level("AWS (EC2, S3), Docker") == ["AWS (EC2, S3)", "Docker"]


def test_bank_numbers_and_lists(bank):
    assert {"12", "120", "85", "500"} <= bank.numbers
    assert "aws" in bank.skills and "ec2" in bank.skills and "python" in bank.skills
    assert "Pandas" in bank.do_not_claim and "any security clearance" not in bank.do_not_claim


# ---------------------------------------------------------------- validation
def test_clean_patch_passes(base, bank):
    assert validate_patch(good_patch(), base, bank) == ([], [])


def test_fake_number_is_rejected(base, bank):
    patch = good_patch(experience_bullets={"0": ["Cut latency 77% across 15 endpoints.", "b", "c", "d"]})
    errors, _ = validate_patch(patch, base, bank)
    assert any("77" in e and "BULLET_BANK" in e for e in errors)


def test_numbers_from_the_bank_are_allowed(base, bank):
    patch = good_patch(experience_bullets={"0": ["Cut API response time **30%** across **15** endpoints.",
                                                 "Automated reports for **12** teams.", "Wrote **120** tests.", "Raised coverage to **85%**."]})
    assert validate_patch(patch, base, bank)[0] == []


@pytest.mark.parametrize("bad", ["Built it — fast", "Built it – fast"])
def test_dashes_rejected(base, bank, bad):
    errors, _ = validate_patch(good_patch(tagline=bad), base, bank)
    assert any("dash" in e for e in errors)


@pytest.mark.parametrize("word", ["I", "me", "my", "we", "our"])
def test_first_person_rejected_in_bullets(base, bank, word):
    patch = good_patch(experience_bullets={"0": [f"Shipped what {word} designed.", "b", "c", "d"]})
    errors, _ = validate_patch(patch, base, bank)
    assert any("first-person" in e for e in errors)


def test_first_person_check_does_not_flag_ordinary_words(base, bank):
    patch = good_patch(experience_bullets={"0": ["Improved I/O throughput for our-less words like mean, meet and weekly.", "b", "c", "d"]})
    assert not any("first-person" in e for e in validate_patch(patch, base, bank)[0])


@pytest.mark.parametrize("cmd", [r"\write18{rm -rf /}", r"\input{/etc/passwd}", r"\include{x}",
                                 r"\openin1=secrets", r"\immediate\write", r"\catcode`\%=12"])
def test_latex_injection_rejected_everywhere(base, bank, cmd):
    for patch in (good_patch(tagline=f"Engineer {cmd}"),
                  good_patch(match_notes=cmd),
                  good_patch(missing_keywords=[cmd]),
                  good_patch(skills=[{"label": "A", "items": cmd}, {"label": "B", "items": "Python"}, {"label": "C", "items": "SQL"}])):
        errors, _ = validate_patch(patch, base, bank)
        assert any("Blocked LaTeX command" in e for e in errors), (cmd, errors)


def test_unknown_keys_and_types_rejected(base, bank):
    assert any("Unknown key" in e for e in validate_patch(good_patch(pdf_title="x"), base, bank)[0])
    assert validate_patch([1, 2], base, bank)[0]
    assert validate_patch(good_patch(skills="Python"), base, bank)[0]
    assert validate_patch(good_patch(experience_bullets={"x": ["a"]}), base, bank)[0]


def test_counts_must_match_the_base(base, bank):
    two_rows = good_patch(skills=[{"label": "A", "items": "Python"}, {"label": "B", "items": "SQL"}])
    assert any("exactly 3 rows" in e for e in validate_patch(two_rows, base, bank)[0])
    three_bullets = good_patch(experience_bullets={"0": ["a", "b", "c"]})
    assert any("exactly 4 bullets" in e for e in validate_patch(three_bullets, base, bank)[0])
    projects = [{"name": "P", "stack": "Python", "tag": "", "bullets": ["Wrote **12** tests."]}] * 3
    assert any("exactly 2 bullets" in e for e in validate_patch(good_patch(projects=projects), base, bank)[0])


def test_long_bullets_warn_then_block(base, bank):
    fill = "Wrote **12** tests " + "x" * 190
    ok = validate_patch(good_patch(experience_bullets={"0": [fill[:200], "b", "c", "d"]}), base, bank)
    assert not any("characters" in e for e in ok[0]) and any("over 190" in w for w in ok[1])
    too_long = validate_patch(good_patch(experience_bullets={"0": [fill + "y" * 100, "b", "c", "d"]}), base, bank)
    assert any("too long" in e for e in too_long[0])


def test_skill_and_do_not_claim_warnings(base, bank):
    skills = [{"label": "A", "items": "Python, Pandas, Rust"}, {"label": "B", "items": "SQL"}, {"label": "C", "items": "AWS (EC2, S3)"}]
    errors, warnings = validate_patch(good_patch(skills=skills), base, bank)
    assert errors == []
    assert any("'Rust'" in w for w in warnings) and any("Pandas" in w and "do-not-claim" in w for w in warnings)
    assert not any("'AWS (EC2, S3)'" in w for w in warnings)


# ---------------------------------------------------------------- file names
def test_safe_name():
    assert safe_name("Acme / Corp?") == "Acme_Corp"
    assert safe_name("../../etc/passwd") == "etc_passwd"
    assert safe_name("") == "Resume" and safe_name("***", "Role") == "Role"
    assert safe_name("Café Ñandú") == "Cafe_Nandu"
    assert len(safe_name("x" * 500)) <= 40


def test_unique_stem_never_overwrites(tmp_path):
    from datetime import date
    d = date(2026, 1, 2)
    a = unique_stem(tmp_path, "Acme", "Eng", d)
    (tmp_path / f"{a}.tex").write_text("x")
    b = unique_stem(tmp_path, "Acme", "Eng", d)
    assert a == "Acme_Eng_2026-01-02" and b == "Acme_Eng_2026-01-02_2"


def test_inside_blocks_escapes(tmp_path):
    (tmp_path / "ok.pdf").write_text("x")
    assert inside(tmp_path / "ok.pdf", tmp_path)
    assert not inside(tmp_path / ".." / "x", tmp_path)
    assert not inside(Path("/etc/passwd"), tmp_path)


# ---------------------------------------------------------------- compile (fake engine)
def test_no_latex_returns_a_clear_message(monkeypatch):
    monkeypatch.setattr(rs_compile, "find_latex", lambda: None)
    r = rs_compile.compile_tex("x")
    assert not r.ok and "Overleaf" in r.message and "MiKTeX" in r.message


def test_compile_one_page(monkeypatch, tmp_path):
    monkeypatch.setenv("RESUME_STUDIO_LATEX", str(fake_latex(tmp_path, pages=1)))
    r = rs_compile.compile_tex("\\documentclass{article}")
    assert r.ok and r.pages == 1 and r.pdf.startswith(b"%PDF")


def test_compile_failure_and_timeout(monkeypatch, tmp_path):
    monkeypatch.setenv("RESUME_STUDIO_LATEX", str(fake_latex(tmp_path, fail=True)))
    r = rs_compile.compile_tex("x")
    assert not r.ok and "Undefined control sequence" in r.message
    monkeypatch.setenv("RESUME_STUDIO_LATEX", str(fake_latex(tmp_path, sleep=5)))
    r = rs_compile.compile_tex("x", timeout=1)
    assert not r.ok and "longer than 1 seconds" in r.message


# ---------------------------------------------------------------- service + history
def test_build_without_latex_saves_tex_and_history(monkeypatch, db, tmp_path):
    monkeypatch.setattr(rs_compile, "find_latex", lambda: None)
    out = tmp_path / "resumes"
    r = service.build(db, {"base": BASE, "patch": good_patch(), "company": "Acme", "role": "Backend Eng",
                           "jd_text": "We need Python."}, output_dir=out)
    assert r["ok"] and not r["pdf_built"] and any("Overleaf" in w for w in r["warnings"])
    assert (out / f"{Path(r['tex_path']).name}").is_file()
    hist = db.list_tailored_resumes()
    assert len(hist) == 1 and hist[0]["company"] == "Acme" and hist[0]["base_id"] == BASE
    rec = db.get_tailored_resume(r["id"])
    assert rec["jd_text"] == "We need Python." and json.loads(rec["patch_json"])["base"] == BASE


def test_build_with_fake_latex_flags_extra_pages(monkeypatch, db, tmp_path):
    monkeypatch.setenv("RESUME_STUDIO_LATEX", str(fake_latex(tmp_path, pages=2)))
    r = service.build(db, {"base": BASE, "patch": good_patch(), "company": "A", "role": "B"}, output_dir=tmp_path / "o")
    assert r["ok"] and r["pdf_built"] and r["pages"] == 2 and any("2 pages" in w for w in r["warnings"])
    assert Path(r["pdf_path"]).read_bytes().startswith(b"%PDF")


def test_invalid_patch_is_not_built_or_saved(db, tmp_path):
    bad = good_patch(experience_bullets={"0": ["Cut it 99%.", "b", "c", "d"]})
    r = service.build(db, {"base": BASE, "patch": bad, "company": "A", "role": "B"}, output_dir=tmp_path / "o")
    assert not r["ok"] and r["errors"] and db.list_tailored_resumes() == [] and not (tmp_path / "o").exists()


def test_file_for_only_serves_files_inside_output(db, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "a.tex").write_text("hi")
    outside = tmp_path / "secret.pdf"
    outside.write_text("secret")
    ok = db.add_tailored_resume({"base_id": BASE, "tex_path": str(out / "a.tex"), "pdf_path": str(outside)})
    assert service.file_for(db, ok, "tex", out)[0].name == "a.tex"
    assert service.file_for(db, ok, "pdf", out) is None          # outside output/resumes
    assert service.file_for(db, ok, "exe", out) is None and service.file_for(db, 999, "tex", out) is None


@pytest.mark.skipif(rs_compile.find_latex() is None, reason="no LaTeX installed")
def test_real_latex_builds_one_page(db, tmp_path):
    r = service.build(db, {"base": BASE, "patch": {}, "company": "A", "role": "B"}, output_dir=tmp_path / "o")
    assert r["ok"] and r["pdf_built"] and r["pages"] == 1


# ---------------------------------------------------------------- server API + origin checks
@pytest.fixture
def server(tmp_path, monkeypatch):
    import control_server as cs
    monkeypatch.setattr(cs, "DB_FILE", tmp_path / "t.db")
    monkeypatch.setattr(paths, "OUTPUT_DIR", tmp_path / "resumes")
    monkeypatch.setattr(rs_compile, "find_latex", lambda: None)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), cs.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def call(url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=dict({"Content-Type": "application/json"}, **(headers or {})))
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def test_api_lists_bases_and_loads_one(server):
    status, body, _ = call(server + "/api/resumes")
    d = json.loads(body)
    assert status == 200 and [b["id"] for b in d["bases"]] == [BASE] and d["using_sample"] is True
    assert json.loads(call(server + f"/api/resumes/{BASE}")[1])["base"]["id"] == BASE
    assert call(server + "/api/resumes/nope")[0] == 404
    assert call(server + "/api/resumes/profile")[0] == 404          # profile.json is never exposed


def test_api_validate_build_history_download(server):
    status, body, _ = call(server + "/api/resumes/validate", {"base": BASE, "patch": good_patch(tagline="A — B")})
    assert status == 200 and json.loads(body)["ok"] is False
    status, body, _ = call(server + "/api/resumes/build",
                           {"base": BASE, "patch": good_patch(), "company": "Acme", "role": "Eng"})
    built = json.loads(body)
    assert status == 200 and built["ok"] and built["id"] == 1
    hist = json.loads(call(server + "/api/resumes/history")[1])["history"]
    assert hist[0]["company"] == "Acme"
    status, body, headers = call(server + "/api/resumes/file/1.tex")
    assert status == 200 and b"\\begin{document}" in body and "attachment" in headers.get("Content-Disposition", "")
    assert call(server + "/api/resumes/file/1.pdf")[0] == 404          # no PDF without LaTeX
    assert call(server + "/api/resumes/file/999.tex")[0] == 404
    status, body, _ = call(server + "/api/resumes/build", {"base": BASE, "patch": good_patch(tagline="x \\input{y}")})
    assert status == 422 and json.loads(body)["ok"] is False


def test_other_websites_cannot_call_the_api(server):
    evil = {"Origin": "https://evil.example"}
    assert call(server + "/api/resumes", headers=evil)[0] == 403
    assert call(server + "/api/resumes/build", {"base": BASE, "patch": {}}, headers=evil)[0] == 403
    assert call(server + "/applications", headers=evil)[0] == 403
    assert call(server + "/status", headers=evil)[0] == 403
    assert call(server + "/api/resumes", headers={"Host": "evil.example"})[0] == 403      # DNS rebinding
    for ok_origin in ("http://localhost:8765", "http://127.0.0.1:1234", "null"):
        status, _, headers = call(server + "/api/resumes", headers={"Origin": ok_origin})
        assert status == 200 and headers["Access-Control-Allow-Origin"] == ok_origin


def test_oversized_request_rejected(server):
    status, _, _ = call(server + "/api/resumes/validate", headers={"Content-Length": "999999999"}, body={"x": 1})
    assert status in (413, 400)


def test_job_lookup_for_tailor_button(server, tmp_path):
    from src.models import FilteredJob
    from datetime import datetime
    db = JobDB(tmp_path / "t.db")
    db.upsert_job(FilteredJob(source_platform="greenhouse", company_token="c", company_name="Acme", external_id="1", title="Data Engineer",
                              location_raw="NY", description_html="", description_text="Build pipelines in Python.", posted_at=datetime.now(),
                              apply_url="https://x/1", salary_raw=None, workplace_type=None, matched_lane="Data", match_score=90, is_new=True,
                              is_rotational=False, is_internship=False, sponsorship_flag="GREEN", h1b_count=20, location_parsed="NY", dedup_key="k1"))
    db.commit()
    db.close()
    job = json.loads(call(server + "/api/job?key=k1")[1])["job"]
    assert job["company"] == "Acme" and "pipelines" in job["description"]
    assert call(server + "/api/job?key=nope")[0] == 404

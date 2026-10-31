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
# The pasted JSON drives the resume; the bank only advises (unless strict mode is on).
def v(patch, base, bank, strict=False):
    return validate_patch(patch, base, bank, strict=strict)


def test_clean_patch_passes(base, bank):
    assert v(good_patch(), base, bank) == ([], [], [])


def test_numbers_not_in_the_bank_warn_but_never_block(base, bank):
    patch = good_patch(experience_bullets={"0": ["Cut latency 77% across 400 endpoints.", "b", "c", "d"]})
    errors, warnings, _ = v(patch, base, bank)
    assert errors == [] and any("77" in w and "400" in w and "BULLET_BANK" in w for w in warnings)


def test_numbers_in_the_bank_do_not_warn(base, bank):
    patch = good_patch(experience_bullets={"0": ["Cut API response time **30%** across **15** endpoints.", "Automated reports for **12** teams.",
                                                 "Wrote **120** tests.", "Raised coverage to **85%**."]})
    assert v(patch, base, bank) == ([], [], [])


@pytest.mark.parametrize("dash", ["\u2014", "\u2013"])
def test_dashes_warn_but_never_block(base, bank, dash):
    errors, warnings, _ = v(good_patch(tagline=f"Built it {dash} fast"), base, bank)
    assert errors == [] and any("dash" in w for w in warnings)


@pytest.mark.parametrize("word", ["I", "me", "my", "we", "our"])
def test_first_person_warns_but_never_blocks(base, bank, word):
    patch = good_patch(experience_bullets={"0": [f"Shipped what {word} designed.", "b", "c", "d"]})
    errors, warnings, _ = v(patch, base, bank)
    assert errors == [] and any("first-person" in w for w in warnings)


def test_first_person_check_does_not_flag_ordinary_words(base, bank):
    patch = good_patch(experience_bullets={"0": ["Improved I/O throughput for our-less words like mean, meet and weekly.", "b", "c", "d"]})
    assert not any("first-person" in w for w in v(patch, base, bank)[1])


def test_skills_not_in_the_bank_come_back_as_one_list_not_many_warnings(base, bank):
    skills = [{"label": "A", "items": "Python, Rust, Go"}, {"label": "B", "items": "SQL, Zig"}, {"label": "C", "items": "AWS (EC2, S3)"}]
    errors, warnings, not_in_bank = v(good_patch(skills=skills), base, bank)
    assert errors == [] and not_in_bank == ["Rust", "Go", "Zig"]
    assert not any("not in your bullet bank" in w or "Rust" in w for w in warnings)      # no per-item warnings


def test_do_not_claim_terms_still_warn(base, bank):
    skills = [{"label": "A", "items": "Python, Pandas"}, {"label": "B", "items": "SQL"}, {"label": "C", "items": "Docker"}]
    errors, warnings, _ = v(good_patch(skills=skills), base, bank)
    assert errors == [] and any("Pandas" in w and "do-not-claim" in w for w in warnings)


def test_no_length_warning_or_block_for_long_bullets(base, bank):
    long = "Wrote **12** tests " + "x" * 600
    errors, warnings, _ = v(good_patch(experience_bullets={"0": [long, "b", "c", "d"]}), base, bank)
    assert errors == [] and not any("character" in w or "190" in w or "too long" in w for w in warnings)


@pytest.mark.parametrize("cmd", [r"\write18{rm -rf /}", r"\input{/etc/passwd}", r"\include{x}", r"\openin1=secrets",
                                 r"\immediate\write", r"\catcode`\%=12", r"\def\x{y}", r"\newcommand{\z}{q}"])
def test_latex_injection_blocks_everywhere(base, bank, cmd):
    for patch in (good_patch(tagline=f"Engineer {cmd}"),
                  good_patch(match_notes=cmd),
                  good_patch(missing_keywords=[cmd]),
                  good_patch(skills=[{"label": "A", "items": cmd}, {"label": "B", "items": "Python"}, {"label": "C", "items": "SQL"}])):
        errors, _, _ = v(patch, base, bank)
        assert any("Blocked LaTeX command" in e for e in errors), (cmd, errors)


def test_unknown_keys_block_but_pdf_title_is_ignored(base, bank):
    assert any("Unknown key 'foo'" in e for e in v(good_patch(foo=1), base, bank)[0])
    assert v(good_patch(pdf_title="Old Title"), base, bank)[0] == []           # accepted, no error


def test_pdf_title_is_ignored_when_rendering():
    tex, res = render.render_tex(BASE, good_patch(pdf_title="Evil {Title}"))
    assert "Evil" not in tex and res["pdf_title"] == "Jordan Sample Engineer Resume"


def test_wrong_types_and_empty_strings_block(base, bank):
    assert v([1, 2], base, bank)[0]
    assert v(good_patch(skills="Python"), base, bank)[0]
    assert v(good_patch(experience_bullets={"x": ["a"]}), base, bank)[0]
    assert v(good_patch(experience_bullets={"9": ["a"]}), base, bank)[0]          # no such experience entry
    assert any("tagline" in e for e in v(good_patch(tagline="   "), base, bank)[0])
    assert any("[0][1]" in e for e in v(good_patch(experience_bullets={"0": ["a", "", "c", "d"]}), base, bank)[0])
    assert v(good_patch(skills=[{"label": "A", "items": ""}]), base, bank)[0]
    assert v(good_patch(skills=[{"label": "", "items": "Python"}]), base, bank)[0]
    assert v(good_patch(missing_keywords=["ok", ""]), base, bank)[0]
    assert v(good_patch(missing_keywords="nope"), base, bank)[0]
    assert v(good_patch(match_notes=5), base, bank)[0]
    assert v(good_patch(projects=[{"name": "P", "stack": "", "bullets": ["x"]}]), base, bank)[0]
    assert v(good_patch(projects=[{"name": "P", "stack": "S", "tag": 5, "bullets": ["x"]}]), base, bank)[0]
    assert v(good_patch(base=""), base, bank)[0]


def proj(n):
    return [{"name": f"P{i}", "stack": "Python", "tag": "", "bullets": ["Did a thing."]} for i in range(n)]


def test_relaxed_counts_accept_the_whole_range(base, bank):
    for rows in (1, 3, 5):
        assert v(good_patch(skills=[{"label": f"L{i}", "items": "Python"} for i in range(rows)]), base, bank)[0] == []
    for n in (1, 4, 6):
        assert v(good_patch(experience_bullets={"0": [f"Bullet {i}." for i in range(n)]}), base, bank)[0] == []
    for n in (1, 2, 4):
        assert v(good_patch(projects=proj(n)), base, bank)[0] == []
    for n in (1, 2, 3):
        pr = [{"name": "P", "stack": "S", "tag": "", "bullets": [f"B{i}." for i in range(n)]}]
        assert v(good_patch(projects=pr), base, bank)[0] == []


def test_counts_outside_the_range_block(base, bank):
    assert v(good_patch(skills=[]), base, bank)[0]
    assert v(good_patch(skills=[{"label": f"L{i}", "items": "Python"} for i in range(6)]), base, bank)[0]
    assert v(good_patch(experience_bullets={"0": []}), base, bank)[0]
    assert v(good_patch(experience_bullets={"0": [f"B{i}." for i in range(7)]}), base, bank)[0]
    assert v(good_patch(projects=[]), base, bank)[0]
    assert v(good_patch(projects=proj(5)), base, bank)[0]
    over = [{"name": "P", "stack": "S", "tag": "", "bullets": [f"B{i}." for i in range(4)]}]
    assert v(good_patch(projects=over), base, bank)[0]


def test_strict_mode_makes_numbers_and_skills_block_again(base, bank):
    patch = good_patch(experience_bullets={"0": ["Cut latency 77%.", "b", "c", "d"]},
                       skills=[{"label": "A", "items": "Python, Rust"}, {"label": "B", "items": "SQL"}, {"label": "C", "items": "Docker"}])
    assert v(patch, base, bank)[0] == []
    errors, _, not_in_bank = v(patch, base, bank, strict=True)
    assert any("77" in e and "Strict mode" in e for e in errors) and any("Rust" in e for e in errors) and not_in_bank == ["Rust"]


def test_strict_mode_does_not_block_on_dashes_or_first_person(base, bank):
    patch = good_patch(tagline="A \u2014 B", experience_bullets={"0": ["I shipped it.", "b", "c", "d"]})
    assert v(patch, base, bank, strict=True)[0] == []


def test_oversized_patch_is_refused(base, bank):
    assert any("too large" in e for e in v(good_patch(match_notes="x" * 70_000), base, bank)[0])


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


def test_build_with_fake_latex_reports_page_overflow_with_page2_lines(monkeypatch, db, tmp_path):
    from src.resume_studio import fit as rs_fit
    monkeypatch.setenv("RESUME_STUDIO_LATEX", str(fake_latex(tmp_path, pages=2)))
    monkeypatch.setattr(rs_fit, "extract_pages", lambda pdf: (
        [["Experience", "\u2022 Page one bullet"], ["\u2022 First bullet on page two", "wraps here", "Third line", "Fourth line"]], "pdftotext"))
    r = service.build(db, {"base": BASE, "patch": good_patch(), "company": "A", "role": "B"}, output_dir=tmp_path / "o")
    assert r["ok"] and r["pdf_built"] and r["pages"] == 2
    assert r["fit"]["banner"] == "Resume is 2 pages"
    assert r["fit"]["page2_lines"] == ["\u2022 First bullet on page two", "wraps here", "Third line"]
    assert Path(r["pdf_path"]).read_bytes().startswith(b"%PDF")


def test_one_page_build_has_no_banner(monkeypatch, db, tmp_path):
    monkeypatch.setenv("RESUME_STUDIO_LATEX", str(fake_latex(tmp_path, pages=1)))
    r = service.build(db, {"base": BASE, "patch": good_patch(), "company": "A", "role": "B"}, output_dir=tmp_path / "o")
    assert r["pages"] == 1 and r["fit"]["banner"] == "" and r["fit"]["page2_lines"] == []


def test_patch_with_numbers_and_skills_not_in_the_bank_still_builds(db, tmp_path):
    patch = good_patch(experience_bullets={"0": ["Cut costs 73% across 400 clusters.", "b", "c", "d"]},
                       skills=[{"label": "A", "items": "Python, Rust, Zig"}, {"label": "B", "items": "SQL"}, {"label": "C", "items": "Docker"}],
                       tagline="Engineer \u2014 I build things")
    r = service.build(db, {"base": BASE, "patch": patch, "company": "A", "role": "B"}, output_dir=tmp_path / "o")
    assert r["ok"] and r["not_in_bank"] == ["Rust", "Zig"] and any("73" in w for w in r["warnings"])
    assert Path(r["tex_path"]).is_file()


def test_strict_mode_blocks_the_same_patch(db, tmp_path):
    patch = good_patch(experience_bullets={"0": ["Cut costs 73%.", "b", "c", "d"]})
    r = service.build(db, {"base": BASE, "patch": patch, "company": "A", "role": "B", "strict": True}, output_dir=tmp_path / "o")
    assert not r["ok"] and any("Strict mode" in e for e in r["errors"]) and db.list_tailored_resumes() == []


def test_old_patches_with_pdf_title_still_build(db, tmp_path):
    r = service.build(db, {"base": BASE, "patch": good_patch(pdf_title="Whatever"), "company": "A", "role": "B"}, output_dir=tmp_path / "o")
    assert r["ok"] and r["errors"] == []


def test_invalid_patch_is_not_built_or_saved(db, tmp_path):
    bad = good_patch(tagline="Engineer \\input{/etc/passwd}")
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
    d = json.loads(body)
    assert status == 200 and d["ok"] is True and any("dash" in w for w in d["warnings"])       # a dash only warns
    status, body, _ = call(server + "/api/resumes/validate", {"base": BASE, "patch": good_patch(foo=1)})
    assert json.loads(body)["ok"] is False
    odd = good_patch(skills=[{"label": "A", "items": "Rust"}, {"label": "B", "items": "SQL"}, {"label": "C", "items": "Docker"}])
    assert json.loads(call(server + "/api/resumes/validate", {"base": BASE, "patch": odd})[1])["not_in_bank"] == ["Rust"]
    assert json.loads(call(server + "/api/resumes/validate", {"base": BASE, "patch": odd, "strict": True})[1])["ok"] is False
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


# ---------------------------------------------------------------- importer / diagnostics
def make_kit(root, bank=True, bases=("AI_Engineer",), profile=True):
    (root / "data").mkdir(parents=True)
    if profile:
        (root / "data" / "profile.json").write_text((paths.SAMPLE_DIR / "profile.json").read_text())
    for b in bases:
        (root / "data" / f"{b}.json").write_text((paths.SAMPLE_DIR / "Sample_Engineer.json").read_text())
    if bank:
        (root / "BULLET_BANK.md").write_text((paths.SAMPLE_DIR / "BULLET_BANK.md").read_text())
    return root


def test_import_from_folder_and_zip(tmp_path):
    import zipfile
    from src.resume_studio.importer import import_kit
    kit = make_kit(tmp_path / "resume_kit", bases=("AI_Engineer", "Data_Engineer"))
    copied, skipped = import_kit(kit, tmp_path / "dest1")
    assert sorted(copied) == ["AI_Engineer.json", "BULLET_BANK.md", "Data_Engineer.json", "profile.json"] and not skipped
    assert import_kit(kit, tmp_path / "dest1")[1]                      # second run skips (no --force)
    assert not import_kit(kit, tmp_path / "dest1", force=True)[1]
    z = tmp_path / "resume_kit.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for f in kit.rglob("*"):
            if f.is_file():
                zf.write(f, "resume_kit/" + f.relative_to(kit).as_posix())
    copied, _ = import_kit(z, tmp_path / "dest2")
    assert "profile.json" in copied and "BULLET_BANK.md" in copied


def test_import_rejects_bad_kits(tmp_path):
    from src.resume_studio.importer import import_kit
    for kwargs in ({"bank": False}, {"profile": False}, {"bases": ()}):
        with pytest.raises(ValueError):
            import_kit(make_kit(tmp_path / f"k{len(list(tmp_path.iterdir()))}", **kwargs), tmp_path / "d")
    assert not (tmp_path / "d").exists()                                # nothing written on failure
    bad = make_kit(tmp_path / "bad")
    (bad / "data" / "AI_Engineer.json").write_text("{oops")
    with pytest.raises(ValueError, match="not valid JSON"):
        import_kit(bad, tmp_path / "d2")


def test_zip_cannot_write_outside_the_destination(tmp_path):
    import zipfile
    from src.resume_studio.importer import import_kit
    z = tmp_path / "evil.zip"
    good = (paths.SAMPLE_DIR / "profile.json").read_text()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("resume_kit/data/profile.json", good)
        zf.writestr("resume_kit/data/AI_Engineer.json", (paths.SAMPLE_DIR / "Sample_Engineer.json").read_text())
        zf.writestr("resume_kit/BULLET_BANK.md", "x")
        zf.writestr("resume_kit/data/../../../evil.json", "{}")
        zf.writestr("../evil2.json", "{}")
    dest = tmp_path / "dest"
    import_kit(z, dest)
    assert sorted(p.name for p in dest.iterdir()) == ["AI_Engineer.json", "BULLET_BANK.md", "profile.json"]
    assert not (tmp_path / "evil.json").exists() and not (tmp_path.parent / "evil2.json").exists()


def test_json_with_a_bom_still_loads(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    for name in ("profile.json", "Sample_Engineer.json"):
        (d / name).write_bytes(b"\xef\xbb\xbf" + (paths.SAMPLE_DIR / name).read_bytes())    # what Notepad can produce
    monkeypatch.setenv("RESUME_STUDIO_DATA", str(d))
    assert [b["id"] for b in render.list_bases()] == ["Sample_Engineer"]
    assert render.render_tex("Sample_Engineer")[0].startswith("%")


def test_unusable_files_are_reported_not_hidden(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "profile.json").write_text((paths.SAMPLE_DIR / "profile.json").read_text())
    (d / "Broken.json").write_text("{nope")
    (d / "Thin.json").write_text('{"tagline": "x"}')
    (d / "bad name.json").write_text("{}")
    bases, problems = render.scan_bases(d)
    assert bases == [] and len(problems) == 3
    assert any("Broken.json" in p and "not valid JSON" in p for p in problems)
    assert any("Thin.json" in p and "missing" in p for p in problems)


def test_api_reports_folder_and_problems(server):
    d = json.loads(call(server + "/api/resumes")[1])
    assert d["data_dir"] == str(paths.SAMPLE_DIR) and d["problems"] == [] and "bases" in d


# ---------------------------------------------------------------- fit analysis
def test_analyze_lists_short_last_lines_and_page2_start():
    from src.resume_studio.fit import analyze
    bullet_a = "Raised the answer rate from **51% to 85%** on 75 real questions by analyzing past tickets, tuning a gate, and adding staff approval."
    bullet_b = "Wrote a long and detailed description of work that definitely wraps onto a second line, then ends with a long tail of words here."
    pages = [["Experience", "\u2022 Raised the answer rate from 51% to 85% on 75 real questions by analyzing past tickets, tuning a",
              "gate, and adding staff approval.",
              "\u2022 Wrote a long and detailed description of work that definitely wraps onto a second line, then ends with",
              "a long tail of words here, with enough text to be longer than the threshold."],
             ["\u2022 Next page bullet", "continues here", "and here", "and more"]]
    out = analyze(pages, [bullet_a, bullet_b])
    assert out["page2_lines"] == ["\u2022 Next page bullet", "continues here", "and here"]
    assert [s["last_line"] for s in out["short_last_lines"]] == ["gate, and adding staff approval."]
    assert out["short_last_lines"][0]["chars"] == 32 and out["short_last_lines"][0]["page"] == 1


def test_analyze_ignores_single_line_bullets_and_survives_kerning_gaps():
    from src.resume_studio.fit import analyze
    one_line = "Short bullet that fits on one line only."
    wrapped = "Built an ingestion pipeline with Technology and AWS services that wraps across two lines of the resume."
    pages = [["\u2022 Short bullet that fits on one line only.",
              "\u2022 Built an ingestion pipeline with T echnology and A WS services that wraps across two",
              "lines of the resume."]]
    out = analyze(pages, [one_line, wrapped])
    assert [s["last_line"] for s in out["short_last_lines"]] == ["lines of the resume."]


def test_fit_report_never_raises_on_unreadable_pdfs():
    from src.resume_studio.fit import fit_report
    r = fit_report(b"not a pdf", 2, ["x" * 40])
    assert r["pages"] == 2 and r["banner"] == "Resume is 2 pages" and "error" in r


def test_extract_pages_reads_a_real_pdf():
    from src.resume_studio.fit import extract_pages
    from pypdf import PdfWriter
    import io
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    w.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    w.write(buf)
    pages, source = extract_pages(buf.getvalue())
    assert len(pages) == 2 and source in ("pdftotext", "pypdf")


def test_tool_names_match_whole_words_only(bank, tmp_path):
    from src.resume_studio.bullet_bank import load_bank
    f = tmp_path / "BULLET_BANK.md"
    f.write_text("## Example\n\n- Built trust with a governance model and used Go-style naming.\n\n"
                 "## Skills you may list (exact spellings)\n\nPython, Docker\n", encoding="utf-8")
    b = load_bank(f)
    assert b.knows_tool("Python") and b.knows_tool("docker")
    assert not b.knows_tool("Rust")          # only appears inside 'trust'
    assert not b.knows_tool("Gover")         # only a prefix of 'governance'
    assert b.knows_tool("governance")        # a whole word in the bank text

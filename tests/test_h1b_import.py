import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from import_h1b import google_sheet_csv_url, merge_employers, parse_csv_text, parse_name_list  # noqa: E402


def test_parse_name_list_counts_and_comments():
    rows = parse_name_list("# hi\nAcme Inc\nBeta LLC, 42\n\n")
    assert [(r[1], r[2]) for r in rows] == [("acme", 10), ("beta", 42)]


def test_google_sheet_url():
    u = google_sheet_csv_url("https://docs.google.com/spreadsheets/d/abc-123/edit#gid=77")
    assert u == "https://docs.google.com/spreadsheets/d/abc-123/export?format=csv&gid=77"


def test_parse_csv_with_header():
    rows = parse_csv_text("Company,Petitions\nAcme Inc,5\nBeta,\n")
    assert [(r[1], r[2]) for r in rows] == [("acme", 5), ("beta", 10)]


def test_merge_is_additive(tmp_path):
    from src.db import JobDB
    db = tmp_path / "t.db"
    JobDB(db).close()
    merge_employers(db, parse_name_list("Acme Inc,3"))
    merge_employers(db, parse_name_list("Acme Inc,9\nNewco"))
    n = sqlite3.connect(db).execute("SELECT COUNT(DISTINCT employer_name_normalized) FROM h1b_employers").fetchone()[0]
    assert n == 2

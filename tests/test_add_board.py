import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from add_board import parse_board_url  # noqa: E402


def test_workday_with_locale():
    p, e = parse_board_url("https://cognizant.wd1.myworkdayjobs.com/en-US/CognizantCareers/job/X_123", "Cognizant")
    assert p == "workday" and e == {"tenant": "cognizant", "wd": "wd1", "site": "CognizantCareers", "name": "Cognizant"}


def test_workday_without_locale():
    p, e = parse_board_url("acme.wd5.myworkdayjobs.com/External", "Acme")
    assert (e["tenant"], e["wd"], e["site"]) == ("acme", "wd5", "External")


def test_other_platforms():
    assert parse_board_url("https://boards.greenhouse.io/stripe/jobs/1", "S")[1]["token"] == "stripe"
    assert parse_board_url("https://job-boards.greenhouse.io/stripe", "S")[0] == "greenhouse"
    assert parse_board_url("https://jobs.lever.co/palantir/abc", "P")[1]["site"] == "palantir"
    assert parse_board_url("https://jobs.ashbyhq.com/ramp", "R")[1]["board"] == "ramp"
    assert parse_board_url("https://careers-aon.icims.com/jobs", "A")[1]["tenant"] == "careers-aon"
    assert parse_board_url("https://apply.workable.com/acme/", "A")[1]["subdomain"] == "acme"
    assert parse_board_url("https://jobs.smartrecruiters.com/Visa/123", "V")[1]["id"] == "Visa"


def test_unknown_url():
    assert parse_board_url("https://example.com/careers", "X") is None

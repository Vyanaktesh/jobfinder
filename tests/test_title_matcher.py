"""Tests for the fuzzy title matcher against the finance role lanes (config/role_lanes.json)."""
import pytest

from src.filters.title_matcher import TitleMatcher, normalize


@pytest.fixture
def matcher(config_dir):
    """TitleMatcher loaded from real config."""
    return TitleMatcher(config_dir / "role_lanes.json")


class TestFinanceLaneMatching:
    """Every role on the target list should land in its own lane."""

    @pytest.mark.parametrize("title,lane", [
        # Financial Analyst / Finance Analyst share one lane
        ("Financial Analyst", "Financial Analyst"),
        ("Finance Analyst", "Financial Analyst"),
        ("Financial Analyst II", "Financial Analyst"),
        ("Junior Financial Analyst", "Financial Analyst"),
        ("Financial Analyst (Corporate Finance)", "Financial Analyst"),
        # FP&A in every spelling
        ("FP&A Analyst", "FP&A Analyst"),
        ("FP&A Analyst (Remote)", "FP&A Analyst"),
        ("FPA Analyst", "FP&A Analyst"),
        ("FP and A Associate", "FP&A Analyst"),
        ("Analyst, Financial Planning & Analysis", "FP&A Analyst"),
        ("Financial Planning and Analysis Analyst", "FP&A Analyst"),
        # Specific lanes must beat the generic "Financial Analyst" lane on ties
        ("Corporate Finance Analyst", "Corporate Finance Analyst"),
        ("Business Finance Analyst", "Business Finance Analyst"),
        ("Business Finance Analyst II", "Business Finance Analyst"),
        ("Strategic Finance Analyst", "Strategic Finance Analyst"),
        ("Strategy & Finance Analyst", "Strategic Finance Analyst"),
        ("Financial Planning Analyst", "Financial Planning Analyst"),
        ("Pricing Analyst", "Pricing Analyst"),
        ("Pricing Strategy Analyst", "Pricing Analyst"),
        ("Global Pricing Analyst - Commercial Finance", "Pricing Analyst"),
        ("Revenue Analyst", "Revenue Analyst"),
        ("Revenue Management Analyst", "Revenue Analyst"),
    ])
    def test_target_titles_match_expected_lane(self, title, lane, matcher, make_raw_job):
        result = matcher.match(make_raw_job(title=title))
        assert result is not None, f"{title!r} should match a lane"
        assert result[0] == lane
        assert result[1] >= 70


class TestFinanceLaneRejections:
    @pytest.mark.parametrize("title", [
        # seniority
        "Senior Financial Analyst",
        "Sr. Pricing Analyst",
        "Lead Financial Analyst",
        "Financial Analyst III",
        "Finance Manager",
        "Director, FP&A",
        "Assistant Vice President, Financial Analyst",
        # look-alike titles from other careers (fuzzy-match "Financial Analyst" above 70 without guards)
        "Financial Advisor",
        "Financial Planning Advisor",
        "Financial Crimes Analyst",
        "Financial Systems Analyst",
        "Finance Business Analyst",
        "Accounting Analyst",
        "Tax Analyst",
        "Credit Analyst",
        "Revenue Cycle Analyst",
        "Revenue Accounting Analyst",
        "Revenue Operations Analyst",
        # not finance at all
        "Data Analyst",
        "Software Engineer",
        "Executive Chef",
        "Investment Banking Analyst",
        # internships are not on the target list
        "Financial Analyst Intern",
    ])
    def test_rejected(self, title, matcher, make_raw_job):
        assert matcher.match(make_raw_job(title=title)) is None, f"{title!r} should not match"


class TestDescriptionDoesNotCauseRejection:
    def test_seniority_words_in_jd_opening_do_not_reject(self, matcher, make_raw_job):
        """Negative keywords apply to the title only.

        Almost every finance JD opens with 'reports to the Senior Manager ... lead ...
        Director'; matching those against the first 500 chars would drop nearly every job.
        """
        jd = (
            "Reporting to the Senior Manager of FP&A, you will lead monthly forecasting "
            "and partner with the Director of Finance and the VP of Operations."
        )
        result = matcher.match(make_raw_job(title="Financial Analyst", description=jd))
        assert result is not None and result[0] == "Financial Analyst"

    def test_negative_keywords_are_whole_words(self, matcher, make_raw_job):
        # "lead" must not fire on "leadership", "intern" must not fire on "internal"
        result = matcher.match(make_raw_job(title="Financial Analyst - Leadership Program, Internal Reporting"))
        assert result is not None


class TestNormalize:
    @pytest.mark.parametrize("raw,expected", [
        ("FP&A Analyst", "fpa analyst"),
        ("FP / A Analyst", "fpa analyst"),
        ("Analyst, Financial Planning & Analysis", "analyst financial planning and analysis"),
        ("  Financial   Analyst (Remote) ", "financial analyst remote"),
    ])
    def test_normalize(self, raw, expected):
        assert normalize(raw) == expected

    def test_abbreviation_expansion(self, matcher):
        assert matcher.expand_abbreviations("Sr. Financial Analyst") == "senior financial analyst"
        assert matcher.expand_abbreviations("Jr Finance Analyst") == "junior finance analyst"
        assert matcher.expand_abbreviations("Assoc. FP&A Analyst") == "associate fp&a analyst"

    def test_rotational_flag_from_title(self, matcher, make_raw_job):
        result = matcher.match(make_raw_job(title="Financial Analyst - Rotational Program"))
        assert result is not None and result[2] is True


class TestConfigIntegrity:
    def test_lane_config_is_well_formed(self, matcher):
        assert len(matcher.lanes) == 8
        names = [l["lane"] for l in matcher.lanes]
        assert len(names) == len(set(names)), "lane names must be unique (dashboard filters on them)"
        assert names[-1] == "Financial Analyst", "generic lane must stay last so specific lanes win ties"
        for lane in matcher.lanes:
            assert lane["canonical_titles"] and lane["title_must_contain_any"] and lane["title_role_any"]

    def test_software_lanes_file_still_loads(self, config_dir, make_raw_job):
        """The original software/AI lanes are kept in role_lanes.software.json."""
        m = TitleMatcher(config_dir / "role_lanes.software.json")
        result = m.match(make_raw_job(title="Backend Engineer"))
        assert result is not None and result[0] == "Backend Engineer"

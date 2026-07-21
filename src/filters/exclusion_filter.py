"""Exclusion filter for job postings based on title patterns and YOE requirements.

Rejects jobs that are clearly mismatched: senior/staff/director-level titles,
pure sales/support/recruiting/clinical/trades roles, contract-only positions,
and jobs requiring more years of experience than the candidate has (~2.5 years,
threshold set to 4).

Note: internship exclusion is handled per-lane via role_lanes.json
negative_keywords, not globally here.
"""
from __future__ import annotations

import re

from src.models import RawJob

SENIOR_TITLE_PREFIXES = re.compile(
    r"\b(staff|principal|lead|director|vp|avp|vice president|head of|chief|"
    r"founding|distinguished)\b",
    re.IGNORECASE,
)

# Senior/Sr. titles almost always require 5+ years — reject outright
SENIOR_TITLE = re.compile(
    r"\b(senior|sr\.?)\b",
    re.IGNORECASE,
)

# Max YOE we can realistically target (~2.5 yrs experience)
MAX_YOE_THRESHOLD = 4

MANAGER_TITLE = re.compile(r"\bmanager\b", re.IGNORECASE)
ALLOWED_MANAGER_CONTEXTS = re.compile(
    r"\b(project manager|program manager|product manager|scrum master)\b",
    re.IGNORECASE,
)

YOE_PATTERN = re.compile(
    r"(\d+)\+?\s*(?:years?|yrs?)\s*(?:of\s+)?(?:experience|professional|relevant|related|work|industry|hands.on|direct)",
    re.IGNORECASE,
)

MINIMUM_YOE_PATTERN = re.compile(
    r"(?:minimum|at least|min|requires?|requiring)\s*(?:of\s*)?(\d+)\s*(?:\+\s*)?(?:years?|yrs?)",
    re.IGNORECASE,
)

RANGE_YOE_PATTERN = re.compile(
    r"(\d+)\s*[-–—to]+\s*(\d+)\+?\s*(?:years?|yrs?)\s*(?:of\s+)?(?:experience|professional|relevant)?",
    re.IGNORECASE,
)

PURE_SALES_TITLES = re.compile(
    r"\b(account executive|account manager|sales representative|"
    r"sales development representative|sdr|bdr|business development representative|"
    r"inside sales|sales manager|sales associate|sales engineer)\b",
    re.IGNORECASE,
)

PURE_SUPPORT_TITLES = re.compile(
    r"\b(customer service representative|support representative|"
    r"call center|customer support specialist|help desk)\b",
    re.IGNORECASE,
)

PURE_RECRUITING = re.compile(
    r"\b(recruiter|talent acquisition|recruiting coordinator|"
    r"hr generalist|human resources generalist|sourcer)\b",
    re.IGNORECASE,
)

CLINICAL = re.compile(
    r"\b(nurse|nursing|clinical|pharmacist|pharmacy|lab scientist|"
    r"physician|therapist|radiologist|pathologist)\b",
    re.IGNORECASE,
)

TRADES = re.compile(
    r"\b(welder|welding|mechanic|technician|electrician|plumber|"
    r"hvac|carpenter|machinist)\b",
    re.IGNORECASE,
)

CONTRACT_ONLY = re.compile(
    r"\b(contract|contractor|temporary|temp position)\b",
    re.IGNORECASE,
)

CONTRACT_TO_HIRE = re.compile(
    r"\b(contract.to.hire|contract to perm|temp.to.perm|c2h)\b",
    re.IGNORECASE,
)


def check_exclusions(job: RawJob) -> str | None:
    """Check whether a job should be excluded based on title and description patterns.

    Runs a chain of regex checks against the title and description:
    1. Role-type exclusions (sales, support, recruiting, clinical, trades)
    2. Contract-only exclusions
    3. Seniority-level exclusions (senior, staff, director, VP, etc.)
    4. Manager-title exclusions (unless PM/program/product/scrum)
    5. Years-of-experience extraction and threshold check

    Args:
        job: A RawJob instance to evaluate.

    Returns:
        A string describing the exclusion reason, or None if the job passes.
    """
    title = job.title
    desc = job.description_text or ""

    if PURE_SALES_TITLES.search(title):
        return f"pure_sales_title: {title}"

    if PURE_SUPPORT_TITLES.search(title):
        return f"pure_support_title: {title}"

    if PURE_RECRUITING.search(title):
        return f"pure_recruiting_title: {title}"

    if CLINICAL.search(title):
        return f"clinical_title: {title}"

    if TRADES.search(title):
        return f"trades_title: {title}"

    if CONTRACT_ONLY.search(title) and not CONTRACT_TO_HIRE.search(title) and not CONTRACT_TO_HIRE.search(desc[:500]):
        if "contract" in title.lower() and "analyst" not in title.lower():
            return f"contract_only: {title}"

    # Reject staff/principal/lead/director/vp/etc. titles
    if SENIOR_TITLE_PREFIXES.search(title):
        return f"senior_level_title: {title}"

    # Reject Senior/Sr. titles outright (nearly always need 5+ yrs)
    if SENIOR_TITLE.search(title):
        return f"senior_title: {title}"

    # Reject Manager titles (unless project/program/product manager)
    if MANAGER_TITLE.search(title) and not ALLOWED_MANAGER_CONTEXTS.search(title):
        return f"manager_title: {title}"

    # Reject any job requiring more YOE than we have
    yoe_max = _extract_max_yoe(desc)
    if yoe_max and yoe_max >= MAX_YOE_THRESHOLD:
        return f"requires_{yoe_max}_yoe: exceeds {MAX_YOE_THRESHOLD - 1}yr threshold"

    return None


def _extract_max_yoe(text: str) -> int | None:
    """Extract the maximum years-of-experience requirement from job description text.

    Parses three pattern types: range ("3-5 years"), minimum ("at least 4 years"),
    and general ("5+ years of experience"). Returns the highest value found,
    or None if no YOE requirements are detected.
    """
    if not text:
        return None

    # Normalize HTML entities and whitespace that break YOE regexes
    import html
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)      # strip remaining HTML tags
    text = re.sub(r"\s+", " ", text)           # collapse whitespace

    years_found = []

    for m in RANGE_YOE_PATTERN.finditer(text):
        years_found.append(int(m.group(2)))

    for m in MINIMUM_YOE_PATTERN.finditer(text):
        years_found.append(int(m.group(1)))

    for m in YOE_PATTERN.finditer(text):
        years_found.append(int(m.group(1)))

    return max(years_found) if years_found else None

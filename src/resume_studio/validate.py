"""Validate a tailoring patch before it is rendered.

The pasted JSON drives the resume; the bullet bank is advice, not a gate.

BLOCKING (the build stops):
  invalid JSON / non-object, unknown keys, wrong types, empty strings in printed fields,
  counts outside the allowed ranges, a bad experience index, LaTeX commands that could read
  or write files or define macros, and an absurdly large payload.
WARNINGS (never block): numbers not in BULLET_BANK.md, em/en dashes, first-person words in
  bullets, non-ASCII characters, unbalanced ** markers, do-not-claim terms.
NOT IN BANK (never blocks): skills/tools missing from the bank, returned as one list so the page
  can show a single collapsed "N items not in bank" line.
STRICT mode (off by default) turns the number and skill checks into blocking errors again.
Whether the page fits on one page is checked after compiling, not here.
"""
from __future__ import annotations

import json
import re
from typing import NamedTuple

from src.resume_studio.bullet_bank import BulletBank, numbers_in, split_top_level

ALLOWED_KEYS = ("base", "tagline", "skills", "experience_bullets", "projects", "missing_keywords", "match_notes")
IGNORED_KEYS = ("pdf_title",)           # accepted and ignored so older patches don't fail
SKILL_ROWS = (1, 5)
BULLETS_PER_ENTRY = (1, 6)
PROJECTS = (1, 4)
BULLETS_PER_PROJECT = (1, 3)
MAX_PATCH_BYTES = 60_000

DASH_RE = re.compile("[–—]")
FIRST_PERSON_RE = re.compile(r"(?<![\w/'-])(?:I|me|my|we|our)(?![\w/'-])", re.IGNORECASE)
# TeX primitives/macros that can read or write files, define commands or load packages.
INJECTION_RE = re.compile(
    r"\\(write|input|include|openin|openout|immediate|catcode|read|csname|usepackage|"
    r"def|edef|gdef|xdef|newcommand|renewcommand|providecommand|let|loop|scantokens|detokenize|directlua|"
    r"special|documentclass)(?![A-Za-z])", re.IGNORECASE)


class ValidationResult(NamedTuple):
    errors: list[str]
    warnings: list[str]
    not_in_bank: list[str]


def _visible(s: str) -> str:
    return s.replace("**", "")


def _nonempty(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _strings(patch: dict) -> list[tuple[str, str, bool]]:
    """Every (location, text, is_bullet) in the printed parts of a well-formed patch."""
    out: list[tuple[str, str, bool]] = []
    if isinstance(patch.get("tagline"), str):
        out.append(("tagline", patch["tagline"], False))
    for i, row in enumerate(patch.get("skills") or []):
        for k in ("label", "items"):
            out.append((f"skills[{i}].{k}", row[k], False))
    for idx, bullets in (patch.get("experience_bullets") or {}).items():
        for j, b in enumerate(bullets):
            out.append((f"experience_bullets[{idx}][{j}]", b, True))
    for i, p in enumerate(patch.get("projects") or []):
        for k in ("name", "stack", "tag"):
            if isinstance(p.get(k), str):
                out.append((f"projects[{i}].{k}", p[k], False))
        for j, b in enumerate(p["bullets"]):
            out.append((f"projects[{i}].bullets[{j}]", b, True))
    return out


def _range_error(name: str, n: int, lo_hi: tuple[int, int]) -> str | None:
    lo, hi = lo_hi
    if lo <= n <= hi:
        return None
    return f"{name} must have {lo} to {hi} entries (got {n})."


def validate_patch(patch, base_res: dict | None, bank: BulletBank, strict: bool = False) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    not_in_bank: list[str] = []

    if not isinstance(patch, dict):
        return ValidationResult(["The patch must be a JSON object ({ ... })."], [], [])
    try:
        if len(json.dumps(patch)) > MAX_PATCH_BYTES:
            return ValidationResult([f"The patch is too large (over {MAX_PATCH_BYTES // 1000} KB)."], [], [])
    except (TypeError, ValueError):
        return ValidationResult(["The patch contains values that are not valid JSON."], [], [])

    for k in patch:
        if k not in ALLOWED_KEYS and k not in IGNORED_KEYS:
            errors.append(f"Unknown key '{k}'. Allowed: {', '.join(ALLOWED_KEYS)}.")

    # ---- injection check on every string anywhere in the patch (including unprinted fields)
    def walk(o):
        if isinstance(o, str):
            yield o
        elif isinstance(o, dict):
            for k, v in o.items():
                yield str(k)
                yield from walk(v)
        elif isinstance(o, list):
            for v in o:
                yield from walk(v)

    injection: list[str] = []
    for s in walk(patch):
        m = INJECTION_RE.search(s)
        if m:
            injection.append(f"Blocked LaTeX command '\\{m.group(1)}' (not allowed in a resume patch).")
            break

    # ---- structure: types, empty strings, counts
    if "base" in patch and not _nonempty(patch["base"]):
        errors.append("base must be a non-empty string.")

    if "tagline" in patch and not _nonempty(patch["tagline"]):
        errors.append("tagline must be a non-empty string.")

    if "skills" in patch:
        sk = patch["skills"]
        if not isinstance(sk, list):
            errors.append("skills must be a list of {label, items} rows.")
        else:
            e = _range_error("skills", len(sk), SKILL_ROWS)
            if e:
                errors.append(e)
            for i, row in enumerate(sk):
                if not (isinstance(row, dict) and _nonempty(row.get("label")) and _nonempty(row.get("items"))):
                    errors.append(f"skills[{i}] needs a non-empty 'label' and 'items' string.")

    if "experience_bullets" in patch:
        eb = patch["experience_bullets"]
        if not isinstance(eb, dict) or not eb:
            errors.append("experience_bullets must be an object like {\"0\": [bullets]}.")
        else:
            for idx, bullets in eb.items():
                if not (isinstance(idx, str) and idx.isdigit()):
                    errors.append(f"experience_bullets key {idx!r} must be an index like \"0\".")
                    continue
                if base_res is not None and int(idx) >= len(base_res["experience"]):
                    errors.append(f"experience_bullets[{idx}]: this base resume has no experience entry {idx}.")
                    continue
                if not isinstance(bullets, list):
                    errors.append(f"experience_bullets[{idx}] must be a list of bullets.")
                    continue
                e = _range_error(f"experience_bullets[{idx}]", len(bullets), BULLETS_PER_ENTRY)
                if e:
                    errors.append(e)
                for j, b in enumerate(bullets):
                    if not _nonempty(b):
                        errors.append(f"experience_bullets[{idx}][{j}] must be a non-empty string.")

    if "projects" in patch:
        pr = patch["projects"]
        if not isinstance(pr, list):
            errors.append("projects must be a list of {name, stack, tag, bullets}.")
        else:
            e = _range_error("projects", len(pr), PROJECTS)
            if e:
                errors.append(e)
            for i, p in enumerate(pr):
                if not isinstance(p, dict):
                    errors.append(f"projects[{i}] must be an object.")
                    continue
                for k in ("name", "stack"):
                    if not _nonempty(p.get(k)):
                        errors.append(f"projects[{i}].{k} must be a non-empty string.")
                if "tag" in p and not isinstance(p["tag"], str):
                    errors.append(f"projects[{i}].tag must be a string (use \"\" for none).")
                b = p.get("bullets")
                if not isinstance(b, list):
                    errors.append(f"projects[{i}].bullets must be a list of bullets.")
                else:
                    e = _range_error(f"projects[{i}].bullets", len(b), BULLETS_PER_PROJECT)
                    if e:
                        errors.append(e)
                    for j, x in enumerate(b):
                        if not _nonempty(x):
                            errors.append(f"projects[{i}].bullets[{j}] must be a non-empty string.")

    if "missing_keywords" in patch:
        mk = patch["missing_keywords"]
        if not (isinstance(mk, list) and all(_nonempty(x) for x in mk)):
            errors.append("missing_keywords must be a list of non-empty strings.")
    if "match_notes" in patch and not isinstance(patch["match_notes"], str):
        errors.append("match_notes must be a string.")

    if errors:
        # content checks assume a well-formed patch
        return ValidationResult(injection + errors, warnings, not_in_bank)
    errors.extend(injection)

    # ---- content checks (warnings; numbers and skills become errors in strict mode)
    number_problems: list[str] = []
    for loc, text, is_bullet in _strings(patch):
        if DASH_RE.search(text):
            warnings.append(f"{loc}: contains an em or en dash.")
        elif any(ord(c) > 127 for c in text):
            warnings.append(f"{loc}: contains non-ASCII characters; plain ASCII punctuation is safest.")
        if not is_bullet:
            continue
        if FIRST_PERSON_RE.search(text):
            warnings.append(f"{loc}: uses a first-person word (I, me, my, we, our).")
        if text.count("**") % 2:
            warnings.append(f"{loc}: unbalanced ** bold markers.")
        bad = list(dict.fromkeys(x for x in numbers_in(text) if x not in bank.numbers))
        if bad:
            number_problems.append(f"{loc}: number(s) {', '.join(bad)} not found in BULLET_BANK.md.")

    for i, row in enumerate(patch.get("skills") or []):
        for item in split_top_level(row["items"]):
            base_name = re.sub(r"\s*\(.*\)\s*$", "", item).strip()
            if not (bank.knows_tool(item) or bank.knows_tool(base_name)) and item not in not_in_bank:
                not_in_bank.append(item)

    # do-not-claim terms anywhere printed (always a warning)
    printed = " \n".join(t for _, t, _ in _strings(patch))
    for term in bank.do_not_claim:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", printed, re.IGNORECASE):
            warnings.append(f"Mentions '{term}', which is on your do-not-claim list.")

    if strict:
        errors.extend(f"Strict mode: {p}" for p in number_problems)
        if not_in_bank:
            errors.append(f"Strict mode: {len(not_in_bank)} skill(s) not in your bullet bank: {', '.join(not_in_bank)}.")
    else:
        warnings.extend(number_problems)
    return ValidationResult(errors, warnings, not_in_bank)

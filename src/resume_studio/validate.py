"""Validate a tailoring patch before it is rendered. Returns (errors, warnings).

Errors block the build; warnings are shown but allowed.
"""
from __future__ import annotations

import json
import re

from src.resume_studio.bullet_bank import BulletBank, numbers_in, split_top_level

ALLOWED_KEYS = ("base", "tagline", "skills", "experience_bullets", "projects", "missing_keywords", "match_notes")
SOFT_BULLET_CHARS = 190    # longer than this: warning (may push the resume onto a 2nd page)
HARD_BULLET_CHARS = 240    # longer than this: error
MAX_PATCH_BYTES = 60_000
BULLETS_PER_PROJECT = 2

DASH_RE = re.compile("[–—]")
FIRST_PERSON_RE = re.compile(r"(?<![\w/'-])(?:I|me|my|we|our)(?![\w/'-])", re.IGNORECASE)
# TeX primitives/macros that can read or write files, define commands or load packages.
INJECTION_RE = re.compile(
    r"\\(write|input|include|openin|openout|immediate|catcode|read|csname|usepackage|"
    r"def|edef|gdef|xdef|newcommand|renewcommand|let|loop|scantokens|detokenize|directlua|special|"
    r"documentclass)(?![A-Za-z])", re.IGNORECASE)


def _visible(s: str) -> str:
    return s.replace("**", "")


def _strings(patch: dict) -> list[tuple[str, str, bool]]:
    """Every (location, text, is_bullet) in the printed parts of the patch."""
    out: list[tuple[str, str, bool]] = []
    if isinstance(patch.get("tagline"), str):
        out.append(("tagline", patch["tagline"], False))
    for i, row in enumerate(patch.get("skills") or []):
        if isinstance(row, dict):
            for k in ("label", "items"):
                if isinstance(row.get(k), str):
                    out.append((f"skills[{i}].{k}", row[k], False))
    for idx, bullets in (patch.get("experience_bullets") or {}).items() if isinstance(patch.get("experience_bullets"), dict) else []:
        for j, b in enumerate(bullets if isinstance(bullets, list) else []):
            if isinstance(b, str):
                out.append((f"experience_bullets[{idx}][{j}]", b, True))
    for i, p in enumerate(patch.get("projects") or []):
        if isinstance(p, dict):
            for k in ("name", "stack", "tag"):
                if isinstance(p.get(k), str):
                    out.append((f"projects[{i}].{k}", p[k], False))
            for j, b in enumerate(p.get("bullets") if isinstance(p.get("bullets"), list) else []):
                if isinstance(b, str):
                    out.append((f"projects[{i}].bullets[{j}]", b, True))
    return out


def validate_patch(patch, base_res: dict | None, bank: BulletBank) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(patch, dict):
        return ["The patch must be a JSON object ({ ... })."], []
    try:
        if len(json.dumps(patch)) > MAX_PATCH_BYTES:
            return [f"The patch is too large (over {MAX_PATCH_BYTES // 1000} KB)."], []
    except (TypeError, ValueError):
        return ["The patch contains values that are not valid JSON."], []

    for k in patch:
        if k not in ALLOWED_KEYS:
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

    # ---- structure
    if "tagline" in patch:
        t = patch["tagline"]
        if not isinstance(t, str) or not t.strip():
            errors.append("tagline must be a non-empty string.")
        elif len(t) > 220:
            errors.append("tagline is too long (max 220 characters).")

    if "skills" in patch:
        sk = patch["skills"]
        want = len(base_res["skills"]) if base_res else None
        if not isinstance(sk, list) or not sk:
            errors.append("skills must be a list of {label, items} rows.")
        else:
            if want is not None and len(sk) != want:
                errors.append(f"skills must have exactly {want} rows to match this base resume (got {len(sk)}).")
            for i, row in enumerate(sk):
                if not (isinstance(row, dict) and isinstance(row.get("label"), str) and isinstance(row.get("items"), str)
                        and row["label"].strip() and row["items"].strip()):
                    errors.append(f"skills[{i}] needs a non-empty 'label' and 'items' string.")
                elif len(row["label"]) > 40 or len(row["items"]) > 320:
                    errors.append(f"skills[{i}] is too long.")

    if "experience_bullets" in patch:
        eb = patch["experience_bullets"]
        if not isinstance(eb, dict):
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
                if base_res is not None:
                    want = len(base_res["experience"][int(idx)]["bullets"])
                    if len(bullets) != want:
                        errors.append(f"experience_bullets[{idx}] must have exactly {want} bullets to stay on one page (got {len(bullets)}).")

    if "projects" in patch:
        pr = patch["projects"]
        want = len(base_res["projects"]) if base_res else None
        if not isinstance(pr, list) or not pr:
            errors.append("projects must be a list of {name, stack, tag, bullets}.")
        else:
            if want is not None and len(pr) != want:
                errors.append(f"projects must have exactly {want} entries (got {len(pr)}).")
            for i, p in enumerate(pr):
                if not isinstance(p, dict):
                    errors.append(f"projects[{i}] must be an object.")
                    continue
                for k in ("name", "stack"):
                    if not (isinstance(p.get(k), str) and p[k].strip()):
                        errors.append(f"projects[{i}].{k} must be a non-empty string.")
                    elif len(p[k]) > 120:
                        errors.append(f"projects[{i}].{k} is too long.")
                if "tag" in p and not isinstance(p["tag"], str):
                    errors.append(f"projects[{i}].tag must be a string (use \"\" for none).")
                b = p.get("bullets")
                if not isinstance(b, list) or len(b) != BULLETS_PER_PROJECT:
                    errors.append(f"projects[{i}].bullets must be a list of exactly {BULLETS_PER_PROJECT} bullets.")

    if "missing_keywords" in patch:
        mk = patch["missing_keywords"]
        if not (isinstance(mk, list) and len(mk) <= 60 and all(isinstance(x, str) and len(x) <= 100 for x in mk)):
            errors.append("missing_keywords must be a list of short strings.")
    if "match_notes" in patch:
        mn = patch["match_notes"]
        if not (isinstance(mn, str) and len(mn) <= 800):
            errors.append("match_notes must be a string of at most 800 characters.")

    if errors:
        # content checks assume a well-formed patch
        return injection + errors, warnings
    errors.extend(injection)

    # ---- content rules
    seen_numbers: set[tuple[str, str]] = set()
    for loc, text, is_bullet in _strings(patch):
        if DASH_RE.search(text):
            errors.append(f"{loc}: contains an em or en dash. Use a comma, colon or plain hyphen.")
        if any(ord(c) > 127 for c in text) and not DASH_RE.search(text):
            warnings.append(f"{loc}: contains non-ASCII characters; plain ASCII punctuation is safest.")
        if is_bullet:
            if FIRST_PERSON_RE.search(text):
                errors.append(f"{loc}: uses a first-person word (I, me, my, we, our).")
            if text.count("**") % 2:
                warnings.append(f"{loc}: unbalanced ** bold markers.")
            n = len(_visible(text))
            if n > HARD_BULLET_CHARS:
                errors.append(f"{loc}: {n} characters is far too long (max {HARD_BULLET_CHARS}).")
            elif n > SOFT_BULLET_CHARS:
                warnings.append(f"{loc}: {n} characters (over {SOFT_BULLET_CHARS}); it may push the resume past one page.")
            bad = [x for x in numbers_in(text) if x not in bank.numbers and (loc, x) not in seen_numbers]
            for x in bad:
                seen_numbers.add((loc, x))
            if bad:
                errors.append(f"{loc}: number(s) {', '.join(bad)} not found in BULLET_BANK.md (no invented metrics).")

    # tools in skills that the bank doesn't know
    for i, row in enumerate(patch.get("skills") or []):
        for item in split_top_level(row["items"]):
            base_name = re.sub(r"\s*\(.*\)\s*$", "", item).strip()
            if not (bank.knows_tool(item) or bank.knows_tool(base_name)):
                warnings.append(f"skills[{i}]: '{item}' is not in your bullet bank. Only list tools you can back up.")

    # do-not-claim terms anywhere printed
    printed = " \n".join(t for _, t, _ in _strings(patch))
    for term in bank.do_not_claim:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", printed, re.IGNORECASE):
            warnings.append(f"Mentions '{term}', which is on your do-not-claim list.")
    return errors, warnings

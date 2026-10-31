"""Parse BULLET_BANK.md once: the numbers, skills and do-not-claim terms a patch is checked against."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# A number not glued to a preceding letter/digit/dot ("M3", "H1B" are skipped); commas stripped.
_NUM_RE = re.compile(r"(?<![A-Za-z0-9.])(\d[\d,]*(?:\.\d+)?)")


def numbers_in(text: str) -> list[str]:
    out = []
    for m in _NUM_RE.finditer(text):
        n = m.group(1).rstrip(",").replace(",", "")
        n = n.rstrip(".")
        if n:
            out.append(n)
    return out


def split_top_level(s: str) -> list[str]:
    """Split on commas that are not inside parentheses."""
    parts, depth, cur = [], 0, []
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur).strip())
    return [p for p in parts if p]


@dataclass
class BulletBank:
    text_lower: str = ""
    numbers: set[str] = field(default_factory=set)
    skills: set[str] = field(default_factory=set)       # lower-cased allowed skill spellings
    do_not_claim: list[str] = field(default_factory=list)

    def knows_tool(self, name: str) -> bool:
        n = name.strip().lower()
        return bool(n) and (n in self.skills or n in self.text_lower)


_cache: dict[tuple[str, float], BulletBank] = {}


def load_bank(path: Path) -> BulletBank:
    """Parse (and cache by file mtime) the bullet bank at `path`."""
    path = Path(path)
    if not path.is_file():
        return BulletBank()
    key = (str(path), path.stat().st_mtime)
    if key in _cache:
        return _cache[key]

    text = path.read_text(encoding="utf-8-sig")
    bank = BulletBank(text_lower=text.lower(), numbers=set(numbers_in(text)))

    sections = re.split(r"(?m)^##\s+", text)
    for sec in sections:
        head, _, body = sec.partition("\n")
        h = head.strip().lower()
        if h.startswith("skills you may list"):
            for item in split_top_level(" ".join(body.split())):
                it = item.strip().strip(".").lower()
                if not it:
                    continue
                bank.skills.add(it)
                m = re.match(r"^(.*?)\s*\((.*)\)$", it)        # "AWS (EC2, S3)" -> aws, ec2, s3
                if m:
                    bank.skills.add(m.group(1).strip())
                    bank.skills.update(x.strip() for x in m.group(2).split(",") if x.strip())
        elif h.startswith("do not claim"):
            for line in body.splitlines():
                if not line.lstrip().startswith("-"):
                    continue
                for term in split_top_level(line.lstrip()[1:].strip()):
                    t = term.strip().strip(".")
                    words = t.split()
                    # keep tool-name-like terms, drop prose such as "any security clearance"
                    if 0 < len(words) <= 2 and words[0].lower() not in {"any", "more", "no"} and len(t) <= 30:
                        bank.do_not_claim.append(t)
    _cache[key] = bank
    return bank

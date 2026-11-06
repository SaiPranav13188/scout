"""Duplicate and repost detection (plain code).

Three layers, checked in order:
1. exact (source, source_job_id)          -> same record, just seen again
2. canonical apply URL                     -> same posting reached through another source
3. fuzzy (company, title, location) match  -> same role re-posted or listed on several sites
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime

from rapidfuzz import fuzz

from scout_core.normalize import normalize_company, normalize_title

# Seniority words must never be fuzzed away: "Software Engineer" vs "Senior Software Engineer"
# are different roles even though their token sets overlap heavily.
_LEVEL_TOKENS = {
    "senior", "junior", "lead", "staff", "principal", "intern", "trainee", "associate",
    "manager", "director", "head", "i", "ii", "iii", "iv", "1", "2", "3", "graduate", "fresher",
}

FUZZY_TITLE_THRESHOLD = 95


def dedupe_key(company: str, title: str, location_key: str) -> str:
    raw = f"{normalize_company(company)}|{normalize_title(title)}|{location_key}"
    return hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()


@dataclass(frozen=True)
class Candidate:
    """An existing job, reduced to what duplicate matching needs."""

    id: str
    company_norm: str
    title_norm: str
    location_key: str
    canonical_url: str | None
    first_seen_at: datetime | None = None


def _levels(title_norm: str) -> set[str]:
    return {t for t in title_norm.split() if t in _LEVEL_TOKENS}


def titles_match(a: str, b: str) -> bool:
    """Same role? Requires identical seniority tokens and near-identical wording.

    token_sort_ratio tolerates word order and small edits ("Backend Software Engineer" vs
    "Software Engineer Backend") but, unlike token_set_ratio, does not treat a title that merely
    contains another as the same role ("SDE 2 Infra" vs "Data Engineer (SDE 2)").
    """
    if a == b:
        return True
    if _levels(a) != _levels(b):
        return False
    return fuzz.token_sort_ratio(a, b) >= FUZZY_TITLE_THRESHOLD


def find_duplicate(
    *,
    company: str,
    title: str,
    location_key: str,
    canonical_apply_url: str | None,
    candidates: list[Candidate],
) -> tuple[Candidate, str] | None:
    """Return (original, reason) if this job duplicates one of the candidates.

    The earliest-seen candidate wins so the original posting stays the visible one.
    """
    ordered = sorted(candidates, key=lambda c: (c.first_seen_at is None, c.first_seen_at or 0))
    if canonical_apply_url:
        for c in ordered:
            if c.canonical_url and c.canonical_url == canonical_apply_url:
                return c, "same_apply_url"
    company_norm = normalize_company(company)
    title_norm = normalize_title(title)
    for c in ordered:
        if c.company_norm != company_norm:
            continue
        if c.location_key and location_key and c.location_key != location_key:
            continue
        if titles_match(c.title_norm, title_norm):
            return c, "same_company_title_location"
    return None

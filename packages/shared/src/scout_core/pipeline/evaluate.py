"""Plain-code evaluation of one normalised job: location, fresher filter, relevance, trust.

Output is everything the jobs table needs except identity/dedupe, plus a keep/drop decision
with human-readable reasons (so "why didn't I see job X?" always has an answer).
"""

from dataclasses import dataclass, field, replace
from datetime import datetime

from scout_core.filters.fresher import Confidence, Decision, FresherRules, FresherVerdict, classify
from scout_core.filters.relevance import Relevance, RelevanceRules, classify_role
from scout_core.normalize import (
    LocationBucket,
    LocationInfo,
    canonical_url,
    classify_location,
    primary_location_key,
    remote_restriction,
    title_names_foreign_region,
)
from scout_core.sources.ats import country_codes
from scout_core.trust import TrustLevel, TrustReport, evaluate
from scout_core.types import NormalizedJob


@dataclass
class Preferences:
    """Soft by default; only keys listed in `mandatory` act as hard filters (spec §6.3)."""

    allowed_location_buckets: list[str] = field(default_factory=lambda: [
        LocationBucket.INDIA.value, LocationBucket.REMOTE_GLOBAL.value, LocationBucket.UNKNOWN.value])
    preferred_cities: list[str] = field(default_factory=lambda: ["bengaluru"])
    min_relevance: int = 30
    hide_scam_risk: bool = True
    mandatory: dict[str, bool] = field(default_factory=lambda: {"location": True, "relevance": False})


@dataclass
class Evaluation:
    keep: bool
    drop_reasons: list[str]
    canonical_apply_url: str | None
    location: LocationInfo
    location_key: str
    fresher: FresherVerdict
    relevance: Relevance
    trust: TrustReport
    posted_at: datetime | None


def has_full_posting(source: str) -> bool:
    """Email alerts (LinkedIn, Wellfound, Instahyre, Internshala...) carry a title or a one-line snippet,
    never the whole posting. Company boards (Greenhouse, Lever, Ashby) give the complete description."""
    return not source.endswith("_alert")


def require_full_posting(source: str, verdict: FresherVerdict) -> FresherVerdict:
    """My rule (2026-11-08): a job counts as entry-level only if the WHOLE posting was read and says so.
    A snippet can still drop a job ("3 years of exp"), but it can never confirm one."""
    if has_full_posting(source) or verdict.decision == Decision.DROP or verdict.experience_unclear:
        return verdict
    return replace(verdict, confidence=Confidence.LOW, experience_unclear=True,
                   reasons=["only an email snippet was available, the full posting wasn't read", *verdict.reasons])


def evaluate_job(
    job: NormalizedJob,
    *,
    fresher_rules: FresherRules | None = None,
    relevance_rules: RelevanceRules | None = None,
    prefs: Preferences | None = None,
    company_website: str | None = None,
    in_yc_directory: bool = False,
    domain_created_at: datetime | None = None,
) -> Evaluation:
    prefs = prefs or Preferences()
    loc = classify_location(job.locations or ([job.location] if job.location else []),
                            job.remote, country_codes(job))
    if loc.bucket == LocationBucket.REMOTE_GLOBAL and (
            title_names_foreign_region(job.title)
            or (job.description_text and remote_restriction(job.description_text))):
        loc = LocationInfo(LocationBucket.REMOTE_RESTRICTED, (), True)
    fresher = classify(job.title, job.description_text, fresher_rules, job.employment_type)
    fresher = require_full_posting(job.source, fresher)
    relevance = classify_role(job.title, job.description_text, relevance_rules)
    trust = evaluate(source=job.source, apply_url=job.apply_url, company_website=company_website,
                     description=job.description_text, in_yc_directory=in_yc_directory,
                     domain_created_at=domain_created_at)

    drop: list[str] = []
    if fresher.decision == Decision.DROP:
        drop.append("experience: " + "; ".join(fresher.reasons))
    if prefs.mandatory.get("location") and loc.bucket.value not in prefs.allowed_location_buckets:
        drop.append(f"location: {loc.bucket.value.replace('_', ' ')}"
                    + (f" ({', '.join(job.locations[:2])})" if job.locations else ""))
    if prefs.mandatory.get("relevance") and relevance.score < prefs.min_relevance:
        drop.append(f"role not relevant ({relevance.family})")
    if prefs.hide_scam_risk and trust.level == TrustLevel.SCAM_RISK:
        drop.append("scam risk: " + "; ".join(s.detail for s in trust.signals if s.weight <= -100))

    return Evaluation(
        keep=not drop,
        drop_reasons=drop,
        canonical_apply_url=canonical_url(job.apply_url),
        location=loc,
        location_key=primary_location_key(loc, job.locations),
        fresher=fresher,
        relevance=relevance,
        trust=trust,
        posted_at=job.posted_at,
    )

"""Plain-code evaluation of one normalised job: location, fresher filter, relevance, trust.

Output is everything the jobs table needs except identity/dedupe, plus a keep/drop decision
with human-readable reasons (so "why didn't I see job X?" always has an answer).
"""

import re
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
from scout_core.text import fold
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


SNIPPET_ONLY = "only a title or snippet from an email alert; the full posting couldn't be read"
# 2026-11-14 (my decision after a week with nothing): an alert job with nothing in its title or snippet that
# asks for experience is SHOWN, labelled so I check the posting before applying. Alert jobs whose title or
# snippet asks for experience (Senior, Lead, "3+ years", "1-3 yrs"...) are still dropped.
UNVERIFIED_ALERT = "experience not stated in the alert email: check the posting before applying"


NO_DESCRIPTION = "the job board gave no description to read"

# Alert jobs whose TITLE, written by the employer, says it is a fresher role (2026-11-14). The title is the
# posting's own text, unlike a site's level filter ("Entry level" on LinkedIn is not evidence: a 6+ years
# role came through that way on 2026-11-08). Only unambiguous words: "Associate", "Junior", "Engineer I",
# "L1" and a bare "Graduate" often mean 1-3 years or a research post, so they still don't count.
STRONG_TITLE_WORDS = (
    "fresher", "freshers", "entry level", "entry-level", "new grad", "new graduate", "graduate trainee",
    "graduate engineer trainee", "trainee", "campus", "0-1 year", "0-1 years", "0 to 1 year",
    "2025 batch", "2026 batch", "2025 graduates", "2026 graduates", "early career",
)
TITLE_CONFIRMS = "title written by the employer says '{word}' (alert email: the full posting couldn't be read)"
SNIPPET_CONFIRMS = ("text quoted from the posting says {what} (alert email: the full posting couldn't be read)")


def alert_title_word(title: str) -> str | None:
    t = fold(title)
    return next((w for w in STRONG_TITLE_WORDS
                 if re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", t)), None)


def require_full_posting(source: str, verdict: FresherVerdict, description: str | None = None,
                         title: str | None = None) -> FresherVerdict:
    """My rule (2026-11-08): a job is judged only on its WHOLE posting. Email alerts carry a title or a
    one-line snippet, so their jobs are left out (not even listed as "experience unclear"): a snippet
    can't show what the posting asks for. A snippet that already asks for experience still gives that
    as the reason. Exception (2026-11-14): the employer's own title says it is a fresher role."""
    if verdict.decision == Decision.DROP:
        return verdict
    if not has_full_posting(source):
        if title and (word := alert_title_word(title)):
            return replace(verdict, experience_unclear=False, confidence=Confidence.MEDIUM,
                           reasons=[TITLE_CONFIRMS.format(word=word),
                                    *[r for r in verdict.reasons if r != "experience not stated"]])
        # 2026-11-14: Indeed/hirist alerts quote the posting ("Experience: Fresher to 1 Year", a listing's
        # "0 - 1 years"). Employer text that states a fitting experience level counts like the title does;
        # anything that asks for more already dropped the job above. Weak or mixed signals still don't.
        what = None
        if description and (word := alert_title_word(description)):
            what = f"'{word}'"
        elif description and not verdict.experience_unclear and verdict.reasons and \
                verdict.reasons[0].startswith("states "):
            what = verdict.reasons[0].removeprefix("states ")
        if what:
            return replace(verdict, experience_unclear=False, confidence=Confidence.MEDIUM,
                           reasons=[SNIPPET_CONFIRMS.format(what=what), *verdict.reasons])
        return replace(verdict, experience_unclear=True, confidence=Confidence.LOW,
                       reasons=[UNVERIFIED_ALERT, *[r for r in verdict.reasons if r != "experience not stated"]])
    elif not (description or "").strip():  # a board listing with no text at all is just a title
        reason = NO_DESCRIPTION
    else:
        return verdict
    return replace(verdict, decision=Decision.DROP, confidence=Confidence.HIGH, reasons=[reason, *verdict.reasons])


def title_confirmed(verdict: FresherVerdict) -> bool:
    """Judged from an alert's own text (confirmed or unverified): there is no fuller text to re-read."""
    return bool(verdict.reasons) and verdict.reasons[0].startswith(("title written by the employer",
                                                                  "text quoted from the posting", UNVERIFIED_ALERT))


AWAITING_SECOND_READING = "rules found it entry-level; waiting for the full-text check"


def await_second_reading(verdict: FresherVerdict) -> FresherVerdict:
    """Rules-approved jobs stay hidden until the second reader (Gemini, whole posting, verified quote)
    agrees: see scout_worker.work.check_unclear_experience."""
    if verdict.decision == Decision.DROP or verdict.experience_unclear:
        return verdict
    return replace(verdict, experience_unclear=True, reasons=[AWAITING_SECOND_READING, *verdict.reasons])


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
    fresher = require_full_posting(job.source, fresher, job.description_text, job.title)
    # A second reader needs a whole posting; an alert job confirmed by its title has nothing more to read.
    if (fresher_rules or FresherRules()).second_reading and not title_confirmed(fresher):
        fresher = await_second_reading(fresher)
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

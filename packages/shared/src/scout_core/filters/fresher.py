"""Fresher / entry-level filter (spec §6.3). Plain code, every decision carries reasons.

Decision rules for an experience requirement (min, max) with limit L = max_experience_years:
  drop  if min > L
  drop  if max is known and max > L          ("0-2 years" with L=1 is dropped, per spec)
  drop  if max is unknown and min >= L > 0   ("1+ years" with L=1 is dropped)
  keep, but unclear, if min == L == max      (exactly "1 year": borderline)
  keep  otherwise                            ("0-1 years", "0-12 months", "fresher")

A requirement qualified as soft ("preferred", "nice to have", "a plus") that is above the limit
also drops the job (a posting that prefers 3+ years is not looking for a fresher; my rule,
2026-11-07), unless the posting explicitly invites freshers ("3+ years a plus, freshers can apply").
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum

from scout_core.text import fold


class Decision(StrEnum):
    KEEP = "keep"
    DROP = "drop"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class FresherRules:
    """Editable in Settings. Defaults follow the spec."""

    max_experience_years: float = 1.0
    # Two readers (2026-11-08): a job the rules approve is shown only after Gemini has read the whole
    # posting and quoted the sentence that makes it a fresher role (checked against the text).
    second_reading: bool = True
    include_internships: bool = False  # plain internships; intern-to-full-time is always kept
    title_drop_words: list[str] = field(default_factory=lambda: [
        "senior", "sr", "lead", "staff", "principal", "manager", "architect", "director",
        "head of", "vp", "vice president", "chief", "distinguished", "expert", "ii", "iii", "iv",
        "sde 2", "sde-2", "sde2", "sde 3", "sde-3", "engineer 2", "engineer 3", "developer 2",
        "developer 3", "mts 2", "mts-2", "l2", "l3", "l4",
        "sde3", "sde 4", "sde-4", "sde4", "engineer 4", "level 2", "level 3",
        # GitLab-style grades and plain wording that mean "not a fresher" (added 2026-11-07).
        "intermediate", "mid-level", "mid level", "experienced",
    ])
    title_keep_words: list[str] = field(default_factory=lambda: [
        "fresher", "freshers", "graduate", "new grad", "entry level", "entry-level", "trainee",
        "junior", "jr", "associate", "apprentice", "campus", "early career", "university grad",
        "sde 1", "sde-1", "sde i", "software engineer i", "engineer 1", "level 1", "l1",
        "engineer i", "developer i", "analyst i", "new graduate", "university graduate",
    ])
    # Title words that look like drop words but are not seniority.
    title_drop_exceptions: list[str] = field(default_factory=lambda: [
        "lead generation", "leads", "staffing", "management trainee", "graduate engineer trainee",
    ])
    text_keep_phrases: list[str] = field(default_factory=lambda: [
        "fresher", "freshers", "fresh graduate", "recent graduate", "new grad", "entry level",
        "entry-level", "no prior experience", "no experience required", "0 years",
        "2025 batch", "2026 batch", "2025 graduates", "2026 graduates", "class of 2025",
        "class of 2026", "graduating in 2025", "graduating in 2026", "2025 pass-outs",
        "2026 pass-outs", "2025 passouts", "2026 passouts", "early career",
    ])


@dataclass
class Requirement:
    min: float
    max: float | None
    soft: bool
    evidence: str


@dataclass
class FresherVerdict:
    decision: Decision
    confidence: Confidence
    experience_min: float | None
    experience_max: float | None
    experience_unclear: bool
    reasons: list[str]
    requirements: list[Requirement]


_WORDNUM = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15,
}
# Up to 3 digits: Paytm's "810 years of experience" (a typo for 8-10) must still count as a requirement.
_NUM = r"(\d{1,3}(?:\.\d)?|zero|one|two|three|four|five|six|seven|eight|nine|ten|twelve|fifteen)"
_YEARS = r"(?:years?|yrs?|yoe)"
_MONTHS = r"(?:months?|mos?)"
_DASH = r"\s*(?:-|to|–|—)\s*"

_EXPERIENCE_CONTEXT = re.compile(
    r"experience|exp\b|yoe|work(?:ing)? in|worked (?:as|in|on|at)|in (?:the )?industry|professional|hands[- ]on|background|"
    # "3-6 years in data science", "2+ years building backend systems" (real postings, 2026-11-07)
    r"years?\s+(?:in|as|of|building|working|developing|designing|delivering|shipping|writing|running|"
    r"leading|owning|managing)\s+(?!a row|age|growth|history|business|the (?:last|past))",
)
_SOFT = re.compile(
    r"prefer|preferred|nice[- ]to[- ]have|is a plus|a plus|bonus|good to have|desirable|ideally|"
    r"advantage|not mandatory|not required|optional",
)
_NOT_EXPERIENCE = re.compile(
    r"(years? old|founded|in business|years? ago|runway|warranty|years? of (?:age|growth|history)|"
    r"annual|per year|/year|a year\b|each year|years? in a row|anniversary|company is \d)",
)

# Sentences about other people's experience (founders, leaders, mentors, the company).
_OTHERS = re.compile(
    r"\b(?:our|the|its)\s+(?:co-?)?founders?\b|\b(?:co-?)?founders?\s+(?:have|has|bring|with)\b|"
    r"\b(?:our|the)\s+(?:ceo|cto|leadership(?: team)?|leaders|mentors?|senior (?:editors|engineers|researchers)|"
    r"editors|advisors|management team)\b|\bwho (?:have|has|bring) (?:decades|\d)",
)
# ...unless the same sentence is about the applicant.
_CANDIDATE = re.compile(r"\byou (?:have|bring|must|should|need|will need|are expected)\b|\byour (?:experience|background)|"
                        r"candidates?|applicants?|we (?:are looking|need|want|require)|"
                        r"must have|should have|required|requirements?")
# Upper limits: "more than 1 year ... will not be considered", "must not have more than 12 months".
_CAP = re.compile(r"will not be considered|won't be considered|not eligible|need not apply|"
                  r"(?:must|should|can) ?not have more than|cannot have more than|no more than|not more than")

_HISTORY_BEFORE = re.compile(r"(over|for|in|during) the (last|past)\s*$|\bsince\s*$|\bfor over\s*$")

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # "0-1 years", "1 - 3 yrs", "2 to 4 years"
    (re.compile(rf"\b{_NUM}{_DASH}{_NUM}\s*\+?\s*{_YEARS}"), "range_years"),
    # "0-12 months", "6 to 12 months"
    (re.compile(rf"\b{_NUM}{_DASH}{_NUM}\s*{_MONTHS}"), "range_months"),
    # "3+ years", "5 plus years", "2+ yrs"
    (re.compile(rf"\b{_NUM}\s*(?:\+|plus)\s*{_YEARS}"), "plus_years"),
    # "minimum 2 years", "at least 3 years", "min. 1 year"
    (re.compile(rf"\b(?:minimum(?: of)?|min\.?|at least|atleast)\s*{_NUM}[\s-]*{_YEARS}"), "min_years"),
    # "2 or more years", "more than 3 years", "over 4 years"
    (re.compile(rf"\b{_NUM}\s*or more\s*{_YEARS}"), "plus_years"),
    (re.compile(rf"\b(?:more than|over|above|greater than)\s*{_NUM}\s*{_YEARS}"), "more_than_years"),
    # "upto 1 year", "up to 2 years", "less than 1 year", "under 1 year"
    (re.compile(rf"\b(?:up ?to|upto|less than|under|max(?:imum)?(?: of)?)\s*{_NUM}\s*{_YEARS}"), "upto_years"),
    # "3 years of experience", "2 yrs experience", "1 year exp"
    (re.compile(rf"\b{_NUM}[\s-]*{_YEARS}\s*(?:of\s+)?(?:\w+\s+){{0,3}}?(?:experience|exp\b)"), "n_years_exp"),
    # "minimum of 18 months", "at least 24 months", "18+ months" (2026-11-08 eval)
    (re.compile(rf"\b(?:minimum(?: of)?|min\.?|at least|atleast)\s*{_NUM}\s*{_MONTHS}"), "min_months"),
    (re.compile(rf"\b{_NUM}\s*(?:\+|plus|or more)\s*{_MONTHS}"), "min_months"),
    (re.compile(rf"\b(?:more than|over)\s*{_NUM}\s*{_MONTHS}"), "min_months"),
    (re.compile(rf"\b(?:up ?to|upto|less than|under|no more than|not more than|max(?:imum)?(?: of)?)\s*{_NUM}\s*{_MONTHS}"), "upto_months"),
    # "experience: 2 years", "experience of 3 years", "experience - 0 to 1 years" handled by range
    (re.compile(rf"(?:experience|exp)\s*(?:required|needed)?\s*(?:[:\-]|of)\s*{_NUM}\s*{_YEARS}"), "exp_colon_years"),
]


def _num(token: str) -> float:
    token = token.strip()
    return float(_WORDNUM[token]) if token in _WORDNUM else float(token)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.;!?])\s+|\n+|•|·|•", text)
    return [p.strip() for p in parts if p and p.strip()]


def extract_requirements(text: str, limit_for_caps: float = 1.0) -> list[Requirement]:
    """Find experience requirements stated in a text, with the clause they came from. Upper limits
    ("more than 1 year will not be considered") become (0, min(N, limit_for_caps)): they never exclude a fresher."""
    found: list[Requirement] = []
    seen_spans: set[tuple[int, int]] = set()
    for sentence in _sentences(fold(text)):
        if _NOT_EXPERIENCE.search(sentence) and not _EXPERIENCE_CONTEXT.search(sentence):
            continue
        # "Our founders have 20+ years of experience": someone else's experience, not a demand on me.
        if _OTHERS.search(sentence) and not _CANDIDATE.search(sentence):
            continue
        # "Candidates with more than 1 year ... will not be considered": an upper limit, not a minimum.
        is_cap = _CAP.search(sentence) is not None
        occupied: list[tuple[int, int]] = []
        for pattern, kind in _PATTERNS:
            for m in pattern.finditer(sentence):
                span = m.span()
                if any(s < span[1] and span[0] < e for s, e in occupied):
                    continue  # a more specific pattern already claimed this text
                # "5+ years" is a requirement on its own; bare ranges and minimums need an
                # experience word nearby so "2-3 years contract" or "at least 2 years" of
                # something else is not mistaken for one.
                needs_context = kind in ("range_years", "range_months", "min_years",
                                         "more_than_years", "upto_years", "min_months", "upto_months")
                window = sentence[max(0, span[0] - 60): span[1] + 60]
                if needs_context and not _EXPERIENCE_CONTEXT.search(window):
                    continue
                if _NOT_EXPERIENCE.search(window) and not _EXPERIENCE_CONTEXT.search(window):
                    continue
                # "growth over the last 10+ years", "for the past 5 years": company history.
                if _HISTORY_BEFORE.search(sentence[max(0, span[0] - 30): span[0]]):
                    continue
                req = _to_requirement(kind, m)
                if req is None:
                    continue
                if is_cap and (req.max is None or req.min == 0):
                    # An eligibility ceiling ("no more than 18 months") lets someone with 0 years apply.
                    req = Requirement(0, min(req.min if req.max is None else req.max, limit_for_caps), False, "")
                req.soft = bool(_SOFT.search(window))
                req.evidence = sentence[max(0, span[0] - 40): span[1] + 40].strip()
                key = (round(req.min * 100), round((req.max or -1) * 100))
                if key in seen_spans:
                    continue
                seen_spans.add(key)
                occupied.append(span)
                found.append(req)
    return found


def _to_requirement(kind: str, m: re.Match[str]) -> Requirement | None:
    g = m.groups()
    if kind == "range_years":
        lo, hi = _num(g[0]), _num(g[1])
        if hi < lo or hi > 40:
            return None
        return Requirement(lo, hi, False, "")
    if kind == "range_months":
        lo, hi = _num(g[0]) / 12, _num(g[1]) / 12
        if hi < lo:
            return None
        return Requirement(round(lo, 2), round(hi, 2), False, "")
    if kind in ("plus_years", "min_years"):
        return Requirement(_num(g[0]), None, False, "")
    if kind == "more_than_years":
        return Requirement(_num(g[0]), None, False, "")
    if kind == "upto_years":
        return Requirement(0, _num(g[0]), False, "")
    if kind == "min_months":
        return Requirement(round(_num(g[0]) / 12, 2), None, False, "")
    if kind == "upto_months":
        return Requirement(0, round(_num(g[0]) / 12, 2), False, "")
    if kind in ("n_years_exp", "exp_colon_years"):
        n = _num(g[0])
        return Requirement(n, None if n > 0 else 0, False, "")
    return None


def _requirement_fits(req: Requirement, limit: float) -> bool | None:
    """True = fits, False = disqualifies, None = borderline."""
    if req.min > limit:
        return False
    if req.max is not None and req.max > limit:
        return False
    if req.max is None and req.min >= limit and req.min > 0:
        return False
    if req.min == limit and req.min > 0:
        return None
    return True


def _has_word(text: str, word: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text) is not None


_NO_FRESHERS = re.compile(
    r"freshers?\s+(?:need not|should not|must not|cannot|can ?not|can't|do not|don't|won't|will not)\s+(?:apply|be considered)|"
    r"\bno freshers\b|not (?:hiring|considering|open to|for|suitable for|looking for|accepting)\s+freshers|"
    r"freshers? (?:are|is) not (?:eligible|considered)",
)


def classify(title: str, description: str | None, rules: FresherRules | None = None,
             employment_type: str | None = None) -> FresherVerdict:
    rules = rules or FresherRules()
    limit = rules.max_experience_years
    t = fold(title)
    d = fold(description)
    reasons: list[str] = []

    # 1. Title seniority is decisive.
    t_for_drop = t
    for exc in rules.title_drop_exceptions:
        t_for_drop = t_for_drop.replace(exc, " ")
    for word in rules.title_drop_words:
        if _has_word(t_for_drop, word):
            return FresherVerdict(Decision.DROP, Confidence.HIGH, None, None, False,
                                  [f"title contains '{word}'"], [])

    # 2. Internships: keep only conversion roles unless internships are enabled.
    is_intern = _has_word(t, "intern") or _has_word(t, "internship") or (
        fold(employment_type) in ("intern", "internship"))
    # Word boundaries matter: a bare "ppo" matched "equal opportunity" in legal footers (2026-11-08).
    converts = re.search(r"\b(ppo|pre[- ]placement offer|full[- ]time conversion|convert(?:ed|ible)? to "
                         r"(?:a )?full[- ]time|intern(?:ship)?[- ]to[- ](?:full[- ]time|fte|hire)|"
                         r"job offer.{0,40}post[- ]internship)\b",
                         f"{t} {d}") is not None
    if is_intern and not converts and not rules.include_internships:
        return FresherVerdict(Decision.DROP, Confidence.HIGH, None, None, False,
                              ["internship without full-time conversion"], [])
    # 2b. "Freshers need not apply" / "not hiring freshers": the word "fresher" here means the opposite.
    if neg := _NO_FRESHERS.search(d):
        return FresherVerdict(Decision.DROP, Confidence.HIGH, None, None, False,
                              [f"posting excludes freshers: \"{neg.group(0)}\""], [])

    # 3. Explicit experience requirements (title first, then description). Read even for
    # intern-to-full-time roles: "AI Researcher Intern" asking for 2-3 years is not a fresher role.
    reqs = extract_requirements(title, limit) + (extract_requirements(description, limit) if description else [])
    if is_intern and converts:
        too_high = [r for r in reqs if _requirement_fits(r, limit) is False]
        if too_high:
            return FresherVerdict(Decision.DROP, Confidence.HIGH, too_high[0].min, too_high[0].max, False,
                                  [f"internship, but requires {_fmt(too_high[0])} experience: \"{too_high[0].evidence}\""],
                                  reqs)
        # An internship that converts to full time, with no experience demand, is a fresher role.
        return FresherVerdict(Decision.KEEP, Confidence.HIGH, 0, None, False, ["intern-to-full-time role"], reqs)
    hard = [r for r in reqs if not r.soft]
    soft = [r for r in reqs if r.soft]
    hard_fits = [(r, _requirement_fits(r, limit)) for r in hard]

    title_keep = [w for w in rules.title_keep_words if _has_word(t, w)]
    # Whole words only: "0 years" must not match inside "10 years" (2026-11-08).
    text_keep = [p for p in rules.text_keep_phrases if _has_word(d, p)]

    exp_min = min((r.min for r in hard), default=None)
    exp_max_values = [r.max for r in hard if r.max is not None]
    exp_max = max(exp_max_values) if exp_max_values else None

    if hard_fits:
        fitting = [r for r, ok in hard_fits if ok is True]
        failing = [r for r, ok in hard_fits if ok is False]
        borderline = [r for r, ok in hard_fits if ok is None]
        if failing and not fitting and not borderline:
            worst = failing[0]
            return FresherVerdict(Decision.DROP, Confidence.HIGH, exp_min, exp_max, False,
                                  [f"requires {_fmt(worst)} experience: \"{worst.evidence}\""], reqs)
        if fitting and not failing and not borderline:
            reasons.append(f"states {_fmt(fitting[0])} experience")
            return FresherVerdict(Decision.KEEP, Confidence.HIGH, exp_min, exp_max, False,
                                  reasons + _keep_reasons(title_keep, text_keep), reqs)
        # Mixed or borderline signals: keep, but let me decide.
        reasons.append("experience requirements are mixed or borderline: "
                       + "; ".join(_fmt(r) for r, _ in hard_fits))
        return FresherVerdict(Decision.KEEP, Confidence.LOW, exp_min, exp_max, True,
                              reasons + _keep_reasons(title_keep, text_keep), reqs)

    if soft:
        soft_too_high = [r for r in soft if _requirement_fits(r, limit) is False]
        if soft_too_high and text_keep:
            # "3+ years is a plus, freshers can apply": the posting itself invites freshers.
            reasons.append(f"prefers {_fmt(soft_too_high[0])} experience, but welcomes freshers")
            return FresherVerdict(Decision.KEEP, Confidence.MEDIUM, 0, None, False,
                                  reasons + _keep_reasons(title_keep, text_keep), reqs)
        if soft_too_high:
            worst = soft_too_high[0]
            return FresherVerdict(Decision.DROP, Confidence.HIGH, worst.min, worst.max, False,
                                  [f"prefers {_fmt(worst)} experience: \"{worst.evidence}\""], reqs)

    # 4. No numeric requirement: rely on explicit fresher wording. "Associate" / "Junior" alone are
    # not proof (they often mean 1-3 years); they need the description to say so too.
    weak = {"associate", "junior", "jr"}
    if title_keep and not text_keep and set(title_keep) <= weak:
        reasons.append(f"title says '{title_keep[0]}', but nothing confirms 0-1 years")
        return FresherVerdict(Decision.KEEP, Confidence.LOW, None, None, True, reasons, reqs)
    if text_keep or title_keep:
        conf = Confidence.HIGH if text_keep else Confidence.MEDIUM
        return FresherVerdict(Decision.KEEP, conf, 0 if text_keep else None, None, False,
                              reasons + _keep_reasons(title_keep, text_keep), reqs)

    reasons.append("experience not stated")
    return FresherVerdict(Decision.KEEP, Confidence.LOW, None, None, True, reasons, reqs)


def _keep_reasons(title_keep: list[str], text_keep: list[str]) -> list[str]:
    out = []
    if title_keep:
        out.append(f"title says '{title_keep[0]}'")
    if text_keep:
        out.append(f"description says '{text_keep[0]}'")
    return out


def _fmt(r: Requirement) -> str:
    def n(x: float) -> str:
        return str(int(x)) if float(x).is_integer() else f"{x:g}"
    if r.max is None:
        return f"{n(r.min)}+ years"
    if r.min == r.max:
        return f"{n(r.min)} years"
    return f"{n(r.min)}-{n(r.max)} years"

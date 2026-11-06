"""Company / posting trust check (plain code).

Levels:
  verified   strong signals the company and posting are real
  unverified not enough evidence either way (shown honestly as "unverified")
  caution    some warning signs
  scam_risk  hard red flags (fees, deposits, off-platform contact) -> hidden by default

A "verified" badge means "strong signals", never a guarantee.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

import httpx

from scout_core.normalize import registered_domain
from scout_core.text import fold


class TrustLevel(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    CAUTION = "caution"
    SCAM_RISK = "scam_risk"


@dataclass
class Signal:
    code: str
    weight: int  # positive = trust, negative = warning
    detail: str


@dataclass
class TrustReport:
    level: TrustLevel
    score: int
    signals: list[Signal] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "level": self.level.value,
            "score": self.score,
            "signals": [s.__dict__ for s in self.signals],
        }


# Hard red flags: any one of these marks the posting as scam risk.
_HARD_FLAGS: list[tuple[str, str]] = [
    (r"\b(registration|joining|training|processing|onboarding|application|security|kit) (fees?|charges|deposit)\b",
     "asks the candidate to pay a fee or deposit"),
    (r"(pay|deposit|transfer)\s+(rs\.?|inr|₹|\$)?\s*\d+.{0,30}(to (apply|join|register)|for (the )?(job|offer|training))",
     "asks the candidate to pay money"),
    (r"refundable (amount|deposit)", "mentions a 'refundable deposit'"),
    (r"(100%|guaranteed) (job|placement) (guarantee|assured)?", "promises a guaranteed job"),
]
# Soft warnings: each lowers trust.
_SOFT_FLAGS: list[tuple[str, str, int]] = [
    (r"\b(send|share|forward|whatsapp)\b.{0,20}\b(your )?(cv|resume|biodata)\b.{0,30}\b(whatsapp|telegram)\b"
     r"|\b(contact|reach|message|ping|text)\b.{0,25}\b(on|via|at|over)\b.{0,5}\b(whatsapp|telegram)\b"
     r"|\bapply\b.{0,15}\b(via|on|through)\b.{0,5}\b(whatsapp|telegram)\b"
     r"|\b(whatsapp|telegram)\b.{0,10}\b(number|no\.?|hr|recruiter)\b.{0,10}\d",
     "asks to apply or talk over WhatsApp/Telegram", -25),
    (r"[a-z0-9._%+-]+@(gmail|yahoo|outlook|hotmail|rediffmail|ymail)\.(com|co\.in|in)",
     "recruiter uses a personal email address", -20),
    (r"no interview|without interview|direct (joining|selection)", "promises hiring without interview", -20),
    (r"earn (up to|upto)?\s*(rs\.?|inr|₹|\$)?\s*\d[\d,]*\s*(per|/)\s*(day|week)", "pay quoted per day/week", -10),
    (r"work from home.{0,30}(earn|income)|part[- ]time.{0,20}(earn|income)", "'earn from home' wording", -15),
    (r"\bdata entry\b", "data-entry style posting", -5),
]

KNOWN_ATS_HOSTS = {
    "greenhouse.io": "Greenhouse",
    "lever.co": "Lever",
    "ashbyhq.com": "Ashby",
}


# "covered exam and application fees", "no registration fee": the employer pays, or nobody does.
_FEE_NEGATED = re.compile(r"(cover(ed|s)?|reimburs\w*|paid for|we pay|we cover|no|zero|free|waived|"
                          r"never (charge|ask)|do(es)? not (charge|ask)|don'?t (charge|ask)|without any)\b[^.]{0,40}$")


def text_signals(text: str | None) -> list[Signal]:
    d = fold(text)
    out: list[Signal] = []
    for pattern, detail in _HARD_FLAGS:
        for m in re.finditer(pattern, d):
            if _FEE_NEGATED.search(d[max(0, m.start() - 60): m.start()]):
                continue
            out.append(Signal("hard_red_flag", -100, detail))
            break
    for pattern, detail, weight in _SOFT_FLAGS:
        if re.search(pattern, d):
            out.append(Signal("warning", weight, detail))
    return out


def evaluate(
    *,
    source: str,
    apply_url: str | None,
    company_website: str | None,
    description: str | None,
    in_yc_directory: bool = False,
    domain_created_at: datetime | None = None,
    salary_max_inr_per_year: float | None = None,
    now: datetime | None = None,
) -> TrustReport:
    now = now or datetime.now(UTC)
    signals: list[Signal] = []

    apply_domain = registered_domain(apply_url)
    company_domain = registered_domain(company_website)

    if source in ("greenhouse", "lever", "ashby"):
        signals.append(Signal("official_ats_board", 40,
                              f"posted on the company's own {KNOWN_ATS_HOSTS.get(apply_domain or '', source.title())} board"))
    if in_yc_directory:
        signals.append(Signal("yc_company", 25, "listed in the Y Combinator company directory"))
    if company_domain and apply_domain and apply_domain == company_domain:
        signals.append(Signal("apply_on_company_domain", 20, f"apply link is on {company_domain}"))
    if apply_url and apply_url.startswith("http://"):
        signals.append(Signal("no_https", -10, "apply link is not HTTPS"))
    if domain_created_at:
        age_days = (now - domain_created_at).days
        if age_days >= 365 * 2:
            signals.append(Signal("domain_age", 15, f"company domain registered {age_days // 365} years ago"))
        elif age_days < 180:
            signals.append(Signal("young_domain", -20, f"company domain is only {age_days} days old"))
    if salary_max_inr_per_year and salary_max_inr_per_year > 5_000_000:
        signals.append(Signal("unrealistic_pay", -15, "pay looks unusually high for an entry-level role"))

    signals.extend(text_signals(description))

    score = sum(s.weight for s in signals)
    if any(s.code == "hard_red_flag" for s in signals):
        level = TrustLevel.SCAM_RISK
    elif any(s.weight <= -20 for s in signals) or score < 0:
        level = TrustLevel.CAUTION
    elif score >= 40:
        level = TrustLevel.VERIFIED
    else:
        level = TrustLevel.UNVERIFIED
    return TrustReport(level, max(-100, min(100, score)), signals)


async def domain_registration_date(client: httpx.AsyncClient, domain: str) -> datetime | None:
    """Registration date from public RDAP (rdap.org bootstrap). None when unknown; never guessed."""
    try:
        r = await client.get(f"https://rdap.org/domain/{domain}", follow_redirects=True, timeout=10)
        if r.status_code != 200:
            return None
        for event in r.json().get("events", []):
            if event.get("eventAction") == "registration" and event.get("eventDate"):
                return datetime.fromisoformat(event["eventDate"].replace("Z", "+00:00"))
    except (httpx.HTTPError, ValueError):
        return None
    return None

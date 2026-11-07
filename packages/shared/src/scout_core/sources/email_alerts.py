"""Parsers for job-alert emails (spec §5.1 item 5). One parser per sender, plain code only.

Built against real alert emails received in Nov 2026. Email content is untrusted data: it is
only pattern-matched into fields, never interpreted. Links are decoded from tracking URLs where
the real address is embedded in them; tracking links are never followed (no automation on
these sites). Missing details stay None.
"""

import re
from collections.abc import Callable
from urllib.parse import unquote, urlsplit

from selectolax.lexbor import LexborHTMLParser

from scout_core.gmail import EmailMessage
from scout_core.types import NormalizedJob, Salary

# Gmail search covering every supported alert sender (subjects are filtered per parser).
ALERT_QUERY = (
    "(from:jobalerts-noreply@linkedin.com OR from:team@hi.wellfound.com OR from:internshala.com "
    "OR from:noreply@instahyre.com)"
)


def _sender_domain(sender: str) -> str:
    m = re.search(r"@([\w.-]+)", sender)
    return m.group(1).lower() if m else ""


def _parts(node) -> list[str]:
    """Visible text of each element inside a node, in order, without empty pieces."""
    out = []
    for x in node.traverse():
        if x.tag == "-text":
            continue
        t = " ".join(x.text(deep=False).split())
        if t:
            out.append(t)
    return out


def _decode_tracked(href: str) -> str:
    """'https://et.internshala.com/CL0/https:%2F%2Finternshala.com%2Fjob%2F...' -> real URL."""
    m = re.search(r"/CL0/([^/]+)", href)
    return unquote(m.group(1)) if m else href


def _strip_query(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}{p.path}"


# ------------------------------------------------------------------ LinkedIn

_LI_BADGES = re.compile(
    r"^(apply with resume|easy apply|actively recruiting|be an early applicant|promoted|"
    r"\d+ (connections?|school alum|alumni)|.*connections? work here|.*alumni work here|.*school alumni)",
    re.IGNORECASE)


def parse_linkedin(msg: EmailMessage) -> list[NormalizedJob]:
    text = (msg.text or "").replace("\r\n", "\n")
    modes: dict[str, str] = {}
    if msg.html:
        for a in LexborHTMLParser(msg.html).css("a[href]"):
            jid = re.search(r"/jobs/view/(\d+)", a.attributes.get("href", ""))
            mode = re.search(r"\((On-site|Hybrid|Remote)\)", a.text(separator=" "))
            if jid and mode:
                modes[jid.group(1)] = mode.group(1)
    # "Your job alert for startup in Hyderabad": the saved search's place applies to every job in it.
    searched = re.search(r"Your job alert for .+? in ([^\n]+)", text)
    country = None
    if searched:
        from scout_core.normalize import LocationBucket, classify_location
        if classify_location([searched.group(1).strip()]).bucket == LocationBucket.INDIA:
            country = "IN"
    # LinkedIn's experience filter (f_E) is in the alert's "See all jobs" link. Codes 1 = Internship and
    # 2 = Entry level; when the alert is limited to those, LinkedIn already screened for experience.
    entry_level_alert = False
    see_all = re.search(r"See all jobs on LinkedIn:\s*(\S+)", text)
    if see_all:
        from urllib.parse import parse_qs
        query = parse_qs(urlsplit(see_all.group(1)).query)
        codes = set(",".join(query.get("f_E", [])).split(",")) - {""}
        # LinkedIn's newer search pages (2026) write the filter into the search words instead of f_E,
        # e.g. keywords="AI Engineer Internship or Entry level On-site or Hybrid".
        words = " ".join(query.get("keywords", [])).lower()
        worded = (re.search(r"\b(internship|entry[ -]level)\b", words) is not None
                  and re.search(r"\b(associate|mid[- ]senior|senior|director|executive)\b", words) is None)
        entry_level_alert = (bool(codes) and codes <= {"1", "2"}) or (not codes and worded)
    jobs = []
    for block in re.split(r"\n[ \t]*-{10,}[ \t]*\n", text):
        m = re.search(r"View job:\s*https?://(?:www\.)?linkedin\.com/(?:comm/)?jobs/view/(\d+)", block)
        if not m:
            continue
        job_id = m.group(1)
        lines = [ln.strip() for ln in block[: m.start()].splitlines() if ln.strip()]
        lines = [ln for ln in lines if not ln.lower().startswith("your job alert for") and not _LI_BADGES.match(ln)]
        if len(lines) < 2:
            continue
        title, company = lines[0], lines[1]
        location = lines[2] if len(lines) > 2 else None
        mode = modes.get(job_id)
        jobs.append(NormalizedJob(
            source="linkedin_alert", source_job_id=job_id, company_name=company, title=title,
            description_text="Entry level (from a LinkedIn alert filtered to entry level / internship)."
            if entry_level_alert else None,
            location=location, locations=[location] if location else [],
            remote=True if mode == "Remote" else (False if mode == "On-site" else None),
            posted_at=None, posted_at_kind="not_given",  # alert emails don't state the posting date
            apply_url=f"https://www.linkedin.com/jobs/view/{job_id}/",
            extra={"workplace": mode, "gmail_id": msg.id, "country": country, "entry_level_alert": entry_level_alert},
        ))
    return jobs


# ------------------------------------------------------------------ Wellfound

_WF_CARD = re.compile(
    r"<https://wellfound\.com/company/[^>]*>\s*(?P<card>.*?)Learn more\s*"
    r"<(?P<url>https://wellfound\.com/jobs/(?P<id>\d+)-[^>?\s]*)", re.DOTALL)


def parse_wellfound(msg: EmailMessage) -> list[NormalizedJob]:
    if not msg.subject.lower().startswith("new job"):
        return []  # application confirmations, company updates, etc.
    jobs = []
    for m in _WF_CARD.finditer(msg.text or ""):
        card = re.sub(r"\s*\n\s*", " ", m.group("card")).strip()
        segs = [s.strip() for s in card.split("|")]
        head = [p.strip() for p in re.split(r"\s{2,}", segs[0]) if p.strip()]
        if len(head) < 2:
            continue
        title, company = head[0], head[1]
        size = next((p.lstrip("/ ").strip() for p in head[2:] if "Employees" in p), None)
        pay = next((p for p in head[2:] if re.search(r"[₹$€£]", p)), None)
        locations = [x.strip() for x in segs[1].split(",")] if len(segs) > 1 else []
        exp = re.search(r"(\d+)\s+years? of exp", card)
        desc = f"{exp.group(1)} years of experience required." if exp else None
        jobs.append(NormalizedJob(
            source="wellfound_alert", source_job_id=m.group("id"), company_name=company, title=title,
            description_text=desc, location=", ".join(locations) or None, locations=locations,
            posted_at=None, posted_at_kind="not_given",  # alert emails don't state the posting date
            apply_url=m.group("url"),
            salary=Salary(text=pay.split("•")[0].strip()) if pay else None,
            extra={"company_size": size, "gmail_id": msg.id},
        ))
    return jobs


# ------------------------------------------------------------------ Internshala

_IS_ALERT_SUBJECT = re.compile(r"new opportunities|top opportunities|perfect match|hiring now", re.IGNORECASE)
_IS_TARGET = re.compile(r"https?://(?:www\.)?internshala\.com/(job/detail|internship/detail|appcast/job)/([^/?#]+)")


def parse_internshala(msg: EmailMessage) -> list[NormalizedJob]:
    if not _IS_ALERT_SUBJECT.search(msg.subject) or not msg.html:
        return []  # interview invites, seat confirmations, trainings: not job alerts
    jobs, seen = [], set()
    for a in LexborHTMLParser(msg.html).css("a[href]"):
        target = _decode_tracked(a.attributes.get("href", ""))
        m = _IS_TARGET.search(target)
        if not m or m.group(2) in seen:
            continue
        parts = [p for p in _parts(a) if p.lower() not in ("actively hiring", "apply now")]
        if len(parts) < 3:
            continue
        seen.add(m.group(2))
        kind, slug = m.group(1), m.group(2)
        title, company, location = parts[0], parts[1], parts[2]
        pay = next((p for p in parts[3:] if "₹" in p or "salary" in p.lower()), None)
        offer = next((p for p in parts if "post internship" in p.lower()), None)
        is_internship = kind == "internship/detail"
        posted_at, posted_kind = posted_from_relative(parts, msg.received_at)
        job_id = (re.search(r"(\d{6,})$", slug) or re.search(r"(.+)", slug)).group(1)
        jobs.append(NormalizedJob(
            source="internshala_alert", source_job_id=job_id, company_name=company, title=title,
            # The offer line ("Job offer upto ₹3.6LPA post internship") marks intern-to-full-time roles.
            description_text=offer,
            location=location, locations=[location],
            remote=True if re.search(r"remote|work from home", location, re.IGNORECASE) else None,
            employment_type="Intern" if is_internship else "Full Time",
            posted_at=posted_at, posted_at_kind=posted_kind,
            apply_url=_strip_query(f"https://internshala.com/{kind}/{slug}"),
            salary=Salary(text=pay) if pay else None,
            extra={"gmail_id": msg.id, "country": "IN"},  # Internshala lists jobs in India
        ))
    return jobs


_REL = re.compile(r"^(just now|today|few hours ago|\d+ hours? ago|yesterday|\d+ days? ago|\d+ weeks? ago|"
                  r"\d+ months? ago)$", re.IGNORECASE)


def posted_from_relative(parts: list[str], received):
    """Internshala cards say 'Today', 'Few hours ago', '3 days ago'. Estimate from the email time."""
    from datetime import timedelta

    text = next((p.lower() for p in parts if _REL.match(p.strip())), None)
    if text is None:
        return None, "not_given"
    if text in ("just now", "today", "few hours ago") or "hour" in text:
        return received, "relative_estimate"
    if text == "yesterday":
        return received - timedelta(days=1), "relative_estimate"
    n = int(re.match(r"(\d+)", text).group(1))
    days = n * (7 if "week" in text else 30 if "month" in text else 1)
    return received - timedelta(days=days), "relative_estimate"


# ------------------------------------------------------------------ Instahyre

_IH_TARGET = re.compile(r"https?://(?:www\.)?instahyre\.com/job-(\d+)-([^/?#]+)")


def parse_instahyre(msg: EmailMessage) -> list[NormalizedJob]:
    if not msg.html:
        return []
    jobs, seen = [], set()
    for a in LexborHTMLParser(msg.html).css("a[href]"):
        target = _decode_tracked(a.attributes.get("href", ""))
        m = _IH_TARGET.search(target)
        if not m or m.group(1) in seen:
            continue
        parts = _parts(a)
        if "Apply here" not in parts or parts.index("Apply here") < 2:
            continue  # the lone "Apply here" button, not the job card
        seen.add(m.group(1))
        head = parts[: parts.index("Apply here")]
        tail = parts[parts.index("Apply here") + 1:]
        title, company = head[0], head[1]
        # A purely numeric field appears in some cards; its meaning isn't stated, so it's ignored.
        location = next((p for p in head[2:] if not p.isdigit()), None)
        about = tail[0] if tail else None
        skills = tail[1:]
        desc = "\n".join(x for x in [about, ("Skills: " + ", ".join(skills)) if skills else None] if x) or None
        jobs.append(NormalizedJob(
            source="instahyre_alert", source_job_id=m.group(1), company_name=company, title=title,
            description_text=desc, location=location, locations=[location] if location else [],
            remote=True if location and re.search(r"work from home|remote", location, re.IGNORECASE) else None,
            posted_at=None, posted_at_kind="not_given",  # alert emails don't state the posting date
            apply_url=f"https://www.instahyre.com/job-{m.group(1)}-{m.group(2)}/",
            extra={"gmail_id": msg.id, "country": "IN"},  # Instahyre is an India-only job site
        ))
    return jobs


PARSERS: dict[str, Callable[[EmailMessage], list[NormalizedJob]]] = {
    "linkedin.com": parse_linkedin,
    "hi.wellfound.com": parse_wellfound,
    "internshala.com": parse_internshala,
    "instahyre.com": parse_instahyre,
}


def parse_alert(msg: EmailMessage) -> tuple[str | None, list[NormalizedJob]]:
    """(parser name, jobs). Unknown senders return (None, [])."""
    domain = _sender_domain(msg.sender)
    for suffix, parser in PARSERS.items():
        if domain == suffix or domain.endswith("." + suffix):
            if suffix == "linkedin.com" and "jobalerts-noreply@" not in msg.sender.lower():
                return parser.__name__, []  # security, messages and other LinkedIn mail
            return parser.__name__, parser(msg)
    return None, []

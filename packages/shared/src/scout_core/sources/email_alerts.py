"""Parsers for job-alert emails (spec §5.1 item 5). One parser per sender, plain code only.

Built against real alert emails received in Nov 2026. Email content is untrusted data: it is
only pattern-matched into fields, never interpreted. Links are decoded from tracking URLs where
the real address is embedded in them; tracking links are never followed (no automation on
these sites). Missing details stay None.
"""

import html as import_html
import re
from collections.abc import Callable
from urllib.parse import unquote, urlsplit

from selectolax.lexbor import LexborHTMLParser

from scout_core.gmail import EmailMessage
from scout_core.types import NormalizedJob, Salary

# Gmail search covering every supported alert sender (subjects are filtered per parser).
ALERT_QUERY = (
    "(from:jobalerts-noreply@linkedin.com OR from:team@hi.wellfound.com OR from:internshala.com "
    "OR from:noreply@instahyre.com OR from:jobalert.indeed.com OR from:match.indeed.com OR from:hirist.tech "
    "OR from:jobs-noreply@linkedin.com OR from:noreply@glassdoor.com)"
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

# Email chrome that can sit above the first job of a section: "See all jobs on LinkedIn: <url>",
# "Edit alert <url>", "New jobs from your other alerts", "Jobs in Hyderabad" (2026-11-08: these
# became job titles / companies of 12 fake jobs).
_LI_CHROME = re.compile(
    r"^(see all jobs|edit alert|new jobs from your other alerts|new jobs match your preferences|manage alerts|"
    r"expand your search|recommendations based on|jobs you may be interested in|jobs that match your profile|"
    r"based on your (title|profile|activity)|"
    r"jobs in [^\n]{1,60}$|your job alert for)|https?://",
    re.IGNORECASE)

# Newer layout (seen 2026-11-12): the plain-text part repeats the saved search as a header with raw HTML,
# e.g. '<strong class="font-bold" ...>ai engineer</strong> jobs in Hyderabad', above the first job. That
# line shifted every field by one (title = the search, company = the real title, location = the company).
_LI_HTML = re.compile(r"</?[a-z][^>]*>", re.IGNORECASE)

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
    # LinkedIn's experience filter (f_E codes 1 = Internship, 2 = Entry level, or the same words in the
    # search keywords) is recorded for information ONLY. It is never proof: the level is set by the
    # employer, and search words are just matched as text. A 6+ years "AI Engineer (GIS)" posting came
    # through an "Entry level" alert on 2026-11-08. Alert emails carry only the title, so these jobs stay
    # "experience unclear" (hidden from the default feed) until a real description says otherwise.
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
        raw = [ln.strip() for ln in block[: m.start()].splitlines() if ln.strip()]
        # A section title ("Remote jobs") is the line just before its own search link (2026-11-14 layout).
        lines = [ln for i, ln in enumerate(raw) if not (i + 1 < len(raw) and raw[i + 1].startswith("http"))]
        lines = [ln for ln in lines
                 if not _LI_CHROME.search(ln) and not _LI_BADGES.match(ln) and not _LI_HTML.search(ln)]
        if len(lines) < 2:
            continue
        title, company = lines[0], lines[1]
        location = lines[2] if len(lines) > 2 else None
        mode = modes.get(job_id)
        jobs.append(NormalizedJob(
            source="linkedin_alert", source_job_id=job_id, company_name=company, title=title,
            description_text=None,  # the email has no description; never invent one
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


# ------------------------------------------------------------------ Indeed (added 2026-11-14)
# 39 Indeed alert emails in 10 days were ignored before this parser existed. Digests ("11 new machine
# learning fresher internship vacancies") give per job: title, "Company - Location", salary, badges, a
# snippet quoted from the posting ("Experience:* Fresher to 1 Year") and its age. Job-match emails
# give one job with Indeed's own summary of the posting's requirements.

_INDEED_JOB_URL = re.compile(r"^https?://\S*indeed\.com/\S*?[?&]jk=([0-9a-f]{8,})\S*$")
_INDEED_ANY_URL = re.compile(r"^https?://\S*indeed\.com/(?:rc/clk|pagead|viewjob)\S*$")
_INDEED_AGE = re.compile(r"^(just posted|today|active \d+ days? ago|\d+\+? days? ago|\d+ hours? ago)$", re.IGNORECASE)
_INDEED_SALARY = re.compile(r"^(₹|rs\.?|inr|\$|from ₹|up to ₹).*|.*\b(a|an|per) (month|year|hour|day|week)$",
                            re.IGNORECASE)
_INDEED_BADGES = re.compile(r"^(easily apply|urgently hiring|responsive employer|hiring multiple candidates|"
                            r"new|actively hiring|be an early applicant|apply now)$", re.IGNORECASE)


def _indeed_job(lines: list[str], job_key: str, url: str, msg: EmailMessage) -> NormalizedJob | None:
    if len(lines) < 2 or " - " not in lines[1]:
        return None
    title = lines[0]
    company, location = (x.strip() for x in lines[1].rsplit(" - ", 1))
    rest = lines[2:]
    age = rest.pop() if rest and _INDEED_AGE.match(rest[-1]) else None
    salary = next((x for x in rest if _INDEED_SALARY.match(x)), None)
    snippet = " ".join(x for x in rest if x != salary and not _INDEED_BADGES.match(x)).replace("*", "").strip()
    age_norm = re.sub(r"^active ", "", (age or "").lower()).replace("+", "").replace("just posted", "just now")
    posted, kind = posted_from_relative([age_norm] if age else [], msg.received_at)
    return NormalizedJob(
        source="indeed_alert", source_job_id=job_key, company_name=company, title=title,
        description_text=snippet or None,  # a quoted snippet, never the whole posting
        location=location, locations=[location],
        remote=True if re.search(r"\bremote\b|work from home", location, re.IGNORECASE) else None,
        posted_at=posted, posted_at_kind=kind,
        apply_url=f"https://in.indeed.com/viewjob?jk={job_key}" if re.fullmatch(r"[0-9a-f]+", job_key) else url,
        extra={"gmail_id": msg.id, "salary_text": salary, "country": "IN" if "in.indeed.com" in url else None},
    )


def parse_indeed(msg: EmailMessage) -> list[NormalizedJob]:
    text = (msg.text or "").replace("\r\n", "\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    jobs: list[NormalizedJob] = []
    if "match.indeed.com" in msg.sender.lower():
        # One recommended job: "Hi X," / Indeed's summary / title / company / location / Job type / View job.
        view = next((i for i, ln in enumerate(lines) if ln.startswith("View job:")), None)
        if view is None or view < 4:
            return []
        start = next((i for i, ln in enumerate(lines) if re.match(r"^(hi|hello)\b", ln, re.IGNORECASE)), -1)
        block = [ln for ln in lines[start + 1: view] if not ln.lower().startswith("job type:")]
        if len(block) < 4:
            return []
        summary, (title, company, location) = " ".join(block[:-3]), block[-3:]
        url = lines[view].split(":", 1)[1].strip()
        import hashlib
        key = "m" + hashlib.sha256(f"{company}|{title}|{location}".lower().encode()).hexdigest()[:16]
        return [NormalizedJob(
            source="indeed_alert", source_job_id=key, company_name=company, title=title,
            description_text=summary or None, location=location, locations=[location],
            posted_at=None, posted_at_kind="not_given", apply_url=url,
            extra={"gmail_id": msg.id, "country": "IN", "kind": "job_match"},
        )]
    block: list[str] = []
    started = False
    for ln in lines:
        if not started:
            started = ln.startswith("See matching results on Indeed")
            continue
        m = _INDEED_JOB_URL.match(ln)
        if m or _INDEED_ANY_URL.match(ln):
            if m and (job := _indeed_job(block, m.group(1), ln, msg)):
                jobs.append(job)
            block = []
            continue
        if ln.startswith("Do not share this email"):
            break
        block.append(ln)
    return jobs


# ------------------------------------------------------------------ hirist.tech (added 2026-11-14)
# Single-job mails ("Hiring | Data Scientist at Talentiser") carry the WHOLE posting (location,
# "Experience: 1 - 5 Years", requirements), so they are judged like a company-board job. The weekly digest
# lists "DD/MM - Company - Title (1-3 yrs) - Location": a title with the employer's experience range.

def _html_lines(html: str) -> list[tuple[str, str | None]]:
    """(visible line, first link target on that line) for simple mailer HTML."""
    h = re.sub(r"<(style|script|head)\b.*?</\1>", "", html, flags=re.S | re.I)
    h = re.sub(r"<(br|/p|/div|/tr|/td|/h\d|/li|/table)\b[^>]*>", "\n", h, flags=re.I)
    h = re.sub(r'<a\b[^>]*href="([^"]+)"[^>]*>', lambda m: f" \x00{m.group(1)}\x01 ", h, flags=re.I)
    h = re.sub(r"<[^>]+>", " ", h)
    out = []
    for raw in import_html.unescape(h).splitlines():
        link = re.search(r"\x00([^\x01]*)\x01", raw)
        line = " ".join(re.sub(r"\x00[^\x01]*\x01", " ", raw).replace("\xa0", " ").split())
        if line:
            out.append((line, _decode_tracked(link.group(1)) if link else None))
    return out


_HIRIST_DIGEST = re.compile(r"^(?P<date>\d{2}/\d{2}) - (?P<rest>.+\((?P<lo>\d+)\s*-\s*(?P<hi>\d+) yrs\)) - (?P<loc>.+)$")
_HIRIST_JOB = re.compile(r"hirist\.tech/j/(?P<slug>[a-z0-9-]+?)-(?P<id>\d+)(?:\.html)?", re.IGNORECASE)


def parse_hirist(msg: EmailMessage) -> list[NormalizedJob]:
    if not msg.html:
        return []
    lines = _html_lines(msg.html)
    texts = [t for t, _ in lines]
    jobs: list[NormalizedJob] = []
    for text, link in lines:  # weekly digest
        m = _HIRIST_DIGEST.match(text)
        if not m or not link or not (j := _HIRIST_JOB.search(link)):
            continue
        # "AST SpaceMobile - GNC Test Engineer (1-3 yrs)" or "Data Scientist - Machine Learning (1-5 yrs)":
        # the company is not reliably separable, so the whole line is the title.
        title = re.sub(r"\s*\(\d+\s*-\s*\d+ yrs\)\s*$", "", m.group("rest")).strip()
        loc = m.group("loc").strip()
        jobs.append(NormalizedJob(
            source="hirist_alert", source_job_id=j.group("id"), company_name="hirist.tech listing",
            title=title, description_text=f"Experience: {m.group('lo')} - {m.group('hi')} years",  # the listing's range
            location=loc, locations=[x.strip() for x in loc.split("/")],
            posted_at=None, posted_at_kind="not_given",
            apply_url=f"https://www.hirist.tech/j/{j.group('slug')}-{j.group('id')}.html",
            extra={"gmail_id": msg.id, "country": "IN", "experience_text": f"{m.group('lo')}-{m.group('hi')} yrs"},
        ))
    if jobs:
        return jobs
    # Single job: title, company, tags, "Location: ...", "Experience: a - b Years", then the posting.
    try:
        exp_i = next(i for i, t in enumerate(texts) if re.match(r"^Experience:\s*\d", t))
        loc_i = next(i for i, t in enumerate(texts) if t.startswith("Location:"))
        fit_i = max(i for i, t in enumerate(texts) if re.search(r"looks like a strong fit|is hiring talent like you", t))
    except (StopIteration, ValueError):
        return []
    head = [t for t in texts[fit_i + 1: loc_i]
            if not re.match(r"^(opportunity at|get app|hi |hello )", t, re.IGNORECASE)]
    if len(head) < 2:
        return []
    title, company = head[0], head[1]
    location = texts[loc_i].split(":", 1)[1].strip()
    body_start = next((i + 1 for i, t in enumerate(texts) if t.startswith("Early applicants")), exp_i + 1)
    body_end = next((i for i, t in enumerate(texts) if i > body_start and
                     re.match(r"^(Get notified|Best regards|You are receiving|Copyright)", t)), len(texts))
    body = "\n".join([texts[exp_i], *texts[body_start:body_end]])
    apply_link = next((lk for _, lk in lines if lk and "jobId=" in lk), None)
    job_id = re.search(r"jobId=(\d+)", apply_link).group(1) if apply_link else None
    if not job_id:
        import hashlib
        job_id = "h" + hashlib.sha256(f"{company}|{title}".lower().encode()).hexdigest()[:16]
    return [NormalizedJob(
        source="hirist_email", source_job_id=job_id, company_name=company, title=title,
        description_text=body, location=location, locations=[x.strip() for x in location.split("/")],
        posted_at=None, posted_at_kind="not_given",
        apply_url=f"https://www.hirist.tech/one-click-apply?jobId={job_id}" if job_id.isdigit() else "https://www.hirist.tech/",
        extra={"gmail_id": msg.id, "country": "IN"},
    )]


# ------------------------------------------------------------------ Glassdoor (added 2026-11-14)
# Digests ("Job alert: Ai Engineer") and "Jobs for You" mails: cards of company (rating ★), title, location,
# then optional salary / "Easy Apply" / age. Title only, like LinkedIn. Each card is one link, in order.

_GD_OPTIONAL = re.compile(r"^(easy apply|\d+[dh]\+?|24h|new)$|est\.\)$|^₹", re.IGNORECASE)


def parse_glassdoor(msg: EmailMessage) -> list[NormalizedJob]:
    if not msg.html:
        return []
    texts = [re.sub(r"[\u200b-\u200f\ufeff]", "", t).strip() for t, _ in _html_lines(msg.html)]
    texts = [t for t in texts if t]
    start = next((i + 1 for i, t in enumerate(texts) if t.startswith(("Your job listings for", "See the latest roles at"))), None)
    if start is None:
        return []
    alert = next((t.split(":", 1)[1].strip().lower() for t in texts if t.startswith("Job alert:")), None)
    if alert and start + 1 < len(texts) and texts[start].lower() == alert:
        start += 2  # the alert's own search words and place, above the first card
    end = next((i for i, t in enumerate(texts) if i >= start and t.startswith(("See more", "Want more listings"))), len(texts))
    links = [import_html.unescape(h) for h in re.findall(r'href="([^"]+)"', msg.html) if "/partner/jobListing" in h]
    cards: list[tuple[str, str, str, str | None, str | None]] = []
    i = start
    while i + 2 < end:
        company, title, location = texts[i], texts[i + 1], texts[i + 2]
        i += 3
        salary = age = None
        while i < end and _GD_OPTIONAL.search(texts[i]):
            if texts[i].startswith("₹"):
                salary = texts[i]
            elif re.match(r"^\d+[dh]", texts[i]):
                age = texts[i]
            i += 1
        cards.append((re.sub(r"\s+\d\.\d\s*★$", "", company).strip(), title, location, salary, age))
    import hashlib
    jobs = []
    for k, (company, title, location, salary, age) in enumerate(cards):
        days = int(re.match(r"\d+", age).group()) if age and age.endswith(("d", "d+")) else 0 if age else None
        posted = msg.received_at - __import__("datetime").timedelta(days=days) if days is not None else None
        jobs.append(NormalizedJob(
            source="glassdoor_alert",
            source_job_id="g" + hashlib.sha256(f"{company}|{title}|{location}".lower().encode()).hexdigest()[:16],
            company_name=company, title=title, description_text=None, location=location, locations=[location],
            posted_at=posted, posted_at_kind="relative_estimate" if posted else "not_given",
            apply_url=links[k] if len(links) == len(cards) else "https://www.glassdoor.co.in/Job/index.htm",
            extra={"gmail_id": msg.id, "salary_text": salary, "country": "IN" if ".co.in" in msg.html else None},
        ))
    return jobs


PARSERS: dict[str, Callable[[EmailMessage], list[NormalizedJob]]] = {
    "linkedin.com": parse_linkedin,
    "hi.wellfound.com": parse_wellfound,
    "internshala.com": parse_internshala,
    "instahyre.com": parse_instahyre,
    "indeed.com": parse_indeed,
    "hirist.tech": parse_hirist,
    "glassdoor.com": parse_glassdoor,
}


def parse_alert(msg: EmailMessage) -> tuple[str | None, list[NormalizedJob]]:
    """(parser name, jobs). Unknown senders return (None, [])."""
    domain = _sender_domain(msg.sender)
    for suffix, parser in PARSERS.items():
        if domain == suffix or domain.endswith("." + suffix):
            if suffix == "linkedin.com" and not re.search(r"(jobalerts|jobs)-noreply@", msg.sender.lower()):
                return parser.__name__, []  # security, messages and other LinkedIn mail
            return parser.__name__, parser(msg)
    return None, []

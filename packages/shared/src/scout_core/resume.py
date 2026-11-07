"""Master resume: text extraction, local contact redaction, Gemini structuring, fact check.

Integrity rules (spec §7): the master resume is never modified; parsed data is reviewed by me
once; every skill, tool, number and date kept must exist in the original text.
"""

import hashlib
import io
import re
from pathlib import Path

from pydantic import BaseModel, Field

from scout_core.llm import Gemini, untrusted


class Education(BaseModel):
    institution: str
    degree: str | None = None
    field: str | None = None
    start: str | None = None
    end: str | None = None
    grade: str | None = None


class Project(BaseModel):
    name: str
    tech: list[str] = Field(default_factory=list)
    dates: str | None = None
    link_label: str | None = None
    bullets: list[str] = Field(default_factory=list)


class Experience(BaseModel):
    organization: str
    role: str
    start: str | None = None
    end: str | None = None
    location: str | None = None
    bullets: list[str] = Field(default_factory=list)


class SkillGroup(BaseModel):
    category: str
    items: list[str]


class ResumeData(BaseModel):
    headline: str | None = None
    summary: str | None = None
    education: list[Education] = Field(default_factory=list)
    skills: list[SkillGroup] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)


class Contact(BaseModel):
    name: str | None = None
    emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)


class ResumeFileError(ValueError):
    """The uploaded file can't be read as a resume (wrong type, damaged, scanned image...)."""


def extract_pdf_bytes(data: bytes) -> str:
    from pypdf import PdfReader

    if not data.startswith(b"%PDF"):
        raise ResumeFileError("This isn't a PDF file.")
    try:
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        links = []
        for page in reader.pages:  # hyperlinks (LinkedIn, GitHub...) are often only in annotations
            for annot in page.get("/Annots") or []:
                uri = (annot.get_object().get("/A") or {}).get("/URI")
                if uri:
                    links.append(str(uri))
    except Exception as exc:  # pypdf raises many types on damaged or encrypted files
        raise ResumeFileError("The PDF couldn't be read. It may be damaged or password-protected.") from exc
    if len(re.sub(r"\s+", "", text)) < 200:
        raise ResumeFileError("Almost no text found in the PDF. Is it a scanned image? Export it as a text PDF.")
    return text + ("\n" + "\n".join(links) if links else "")


def extract_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return extract_pdf_bytes(path.read_bytes())
    raise ResumeFileError(f"unsupported resume format: {path.suffix} (PDF supported; DOCX coming)")


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Any run of digits/spaces/dashes/brackets holding 10-13 digits ("+91 98765 43210", "(080) 1234-5678").
_PHONE = re.compile(r"\(?\+?\d[\d\s().-]{8,18}\d")
_URL = re.compile(r"(?:https?://|www\.)\S+|(?:linkedin\.com|github\.com)/\S+", re.IGNORECASE)


def split_contact(text: str) -> tuple[Contact, str]:
    """Find contact details locally and return text with them redacted (never sent to the LLM)."""
    emails = sorted(set(_EMAIL.findall(text)))
    links = sorted(set(u.rstrip(").,;|") for u in _URL.findall(text)))
    redacted = _EMAIL.sub("[EMAIL]", text)
    redacted = _URL.sub("[LINK]", redacted)
    found = [p.strip() for p in _PHONE.findall(redacted) if 10 <= len(re.sub(r"\D", "", p)) <= 13]
    for p in sorted(set(found), key=len, reverse=True):
        redacted = redacted.replace(p, "[PHONE]")
    # One entry per number: "(+91) 98765 43210" and "+91-9876543210" are the same phone.
    by_number: dict[str, str] = {}
    for p in found:
        by_number.setdefault(re.sub(r"\D", "", p)[-10:], p)
    phones = sorted(by_number.values())
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), None)
    name = first_line if first_line and len(first_line.split()) <= 5 and not _EMAIL.search(first_line) else None
    if name:
        redacted = redacted.replace(name, "[NAME]")
    return Contact(name=name, emails=emails, phones=phones, links=links), redacted


PROMPT = """Convert this resume into the JSON schema. Copy wording exactly from the resume; do not
rephrase, summarise or add anything. Keep every project and experience bullet as written. Put each
skill/tool once under the category the resume uses (e.g. Languages, Frameworks, Tools). Leave a field
null or empty when the resume does not state it. Contact details were removed and appear as
[NAME], [EMAIL], [PHONE], [LINK]; ignore them.

{resume}"""


async def parse_resume(llm: Gemini, redacted_text: str) -> ResumeData:
    return await llm.json(ResumeData, PROMPT.format(resume=untrusted("resume", redacted_text, 20000)),
                          purpose="parse_resume", temperature=0.0)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9+#.]+", " ", s.lower()).strip()


_NUM = r"(?<![\d.,])\d+(?:[.,]\d+)*%?"


def _numbers(text: str) -> set[str]:
    """Whole numbers in the text ("8.3" is one number; it doesn't vouch for a "3" elsewhere)."""
    out = set()
    for n in re.findall(_NUM, text):
        n = n.replace(",", "")
        out |= {n, n.rstrip("%")}
    return out


def fact_check(data: ResumeData, original: str) -> dict:
    """Every skill/tech, number and date in the parsed resume must appear in the original text.

    `original` is the text of the uploaded file (contact details redacted). An item listed in
    `unsupported` isn't in that file: either a parsing mistake or something I added by hand.
    """
    src = _norm(original)
    src_compact = src.replace(" ", "")
    unsupported: list[str] = []

    def present(term: str) -> bool:
        t = _norm(term)
        return bool(t) and (t in src or t.replace(" ", "") in src_compact)

    for group in data.skills:
        unsupported += [f"skill: {s}" for s in group.items if not present(s)]
    for p in data.projects:
        unsupported += [f"tech in {p.name}: {t}" for t in p.tech if not present(t)]
    texts = [b for p in data.projects for b in p.bullets] + [b for e in data.experience for b in e.bullets]
    texts += data.achievements + data.certifications
    texts += [x for x in (data.headline, data.summary) if x]
    texts += [x for e in data.education for x in (e.grade, e.start, e.end) if x]
    texts += [x for e in data.experience for x in (e.start, e.end) if x]
    texts += [p.dates for p in data.projects if p.dates]
    known = _numbers(original)
    for t in texts:
        for num in re.findall(_NUM, t):
            if num.replace(",", "") not in known:
                unsupported.append(f"number '{num}' in: {t[:60]}")
    return {"ok": not unsupported, "unsupported": unsupported}


def profile_fingerprint(profile: str, prompt_version: str) -> str:
    """Identifies what a match score was computed from: same fingerprint, same inputs."""
    return hashlib.sha256(f"{prompt_version}\n{profile}".encode()).hexdigest()[:16]


async def parse_resume_text(llm: Gemini, raw: str) -> dict:
    """Raw resume text -> the `parsed` document stored on a master_resume version.

    Contact details are split out locally and never sent to the LLM. The redacted text is kept
    (`source_text`) so later edits and tailored resumes can be checked against what I uploaded.
    """
    contact, redacted = split_contact(raw)
    data = await parse_resume(llm, redacted)
    return {**data.model_dump(), "contact": contact.model_dump(), "fact_check": fact_check(data, redacted),
            "source_text": redacted, "parsed_with": llm.usage.by_call[-1]["model"], "reviewed": False}


def _s(v: str | None) -> str | None:
    v = re.sub(r"[ \t]+", " ", v).strip() if v else None
    return v or None


def _items(values: list[str]) -> list[str]:
    seen, out = set(), []
    for v in values:
        t = _s(v)
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def clean_resume(data: ResumeData) -> ResumeData:
    """Tidy hand-edited data: trim text, drop blank rows, remove repeated skills (case-insensitive)."""
    skills, taken = [], set()
    for g in data.skills:
        items = [i for i in _items(g.items) if i.lower() not in taken]
        taken |= {i.lower() for i in items}
        if items:
            skills.append(SkillGroup(category=_s(g.category) or "Skills", items=items))
    return ResumeData(
        headline=_s(data.headline),
        summary=_s(data.summary),
        education=[Education(institution=_s(e.institution) or "", degree=_s(e.degree), field=_s(e.field),
                             start=_s(e.start), end=_s(e.end), grade=_s(e.grade))
                   for e in data.education if _s(e.institution)],
        skills=skills,
        projects=[Project(name=_s(p.name) or "", tech=_items(p.tech), dates=_s(p.dates),
                          link_label=_s(p.link_label), bullets=_items(p.bullets))
                  for p in data.projects if _s(p.name)],
        experience=[Experience(organization=_s(e.organization) or "", role=_s(e.role) or "", start=_s(e.start),
                               end=_s(e.end), location=_s(e.location), bullets=_items(e.bullets))
                    for e in data.experience if _s(e.organization) and _s(e.role)],
        certifications=_items(data.certifications),
        achievements=_items(data.achievements),
    )


def clean_contact(c: Contact) -> Contact:
    return Contact(name=_s(c.name), emails=_items(c.emails), phones=_items(c.phones), links=_items(c.links))

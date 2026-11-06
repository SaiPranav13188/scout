"""Master resume: text extraction, local contact redaction, Gemini structuring, fact check.

Integrity rules (spec §7): the master resume is never modified; parsed data is reviewed by me
once; every skill, tool, number and date kept must exist in the original text.
"""

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


def extract_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        links = []
        for page in reader.pages:  # hyperlinks (LinkedIn, GitHub...) are often only in annotations
            for annot in page.get("/Annots") or []:
                uri = (annot.get_object().get("/A") or {}).get("/URI")
                if uri:
                    links.append(str(uri))
        return text + ("\n" + "\n".join(links) if links else "")
    raise ValueError(f"unsupported resume format: {path.suffix} (PDF supported; DOCX coming)")


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Any run of digits/spaces/dashes/brackets holding 10-13 digits ("+91 98765 43210", "(080) 1234-5678").
_PHONE = re.compile(r"\+?\d[\d\s().-]{8,18}\d")
_URL = re.compile(r"(?:https?://|www\.)\S+|(?:linkedin\.com|github\.com)/\S+", re.IGNORECASE)


def split_contact(text: str) -> tuple[Contact, str]:
    """Find contact details locally and return text with them redacted (never sent to the LLM)."""
    emails = sorted(set(_EMAIL.findall(text)))
    links = sorted(set(u.rstrip(").,;|") for u in _URL.findall(text)))
    redacted = _EMAIL.sub("[EMAIL]", text)
    redacted = _URL.sub("[LINK]", redacted)
    phones = sorted({p.strip() for p in _PHONE.findall(redacted) if 10 <= len(re.sub(r"\D", "", p)) <= 13})
    for p in phones:
        redacted = redacted.replace(p, "[PHONE]")
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


def fact_check(data: ResumeData, original: str) -> dict:
    """Every skill/tech, number and date in the parsed resume must appear in the original text."""
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
    texts += [x for e in data.education for x in (e.grade, e.start, e.end) if x]
    for t in texts:
        for num in re.findall(r"\d+(?:[.,]\d+)?%?", t):
            if num not in original:
                unsupported.append(f"number '{num}' in: {t[:60]}")
    return {"ok": not unsupported, "unsupported": unsupported}

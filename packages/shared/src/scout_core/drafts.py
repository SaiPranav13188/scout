"""Short cover note and recruiter message for a job I'm pursuing (spec §6.4 draft_messages). Drafts only:
I send them myself. Same rule as tailored resumes: nothing that isn't in my master resume."""

from pydantic import BaseModel, Field

from scout_core.llm import Gemini, untrusted
from scout_core.resume import ResumeData
from scout_core.tailor import text_problems

PROMPT_VERSION = "drafts-v1"

PROMPT = """Write two short drafts for this fresher applying to the job below.
1. cover_note: at most 120 words, for the application's cover note box. Plain, specific, no clichés.
2. recruiter_message: at most 60 words, a polite note to a recruiter or hiring manager (no name; start
   with "Hi,"). Ask nothing pushy.
Use ONLY facts from the resume JSON: never add tools, skills, numbers, employers or achievements it doesn't
contain. Mention one or two projects that fit the job. Don't claim years of experience. First person.

{errors}RESUME (JSON):
{master}

{job}"""


class Drafts(BaseModel):
    cover_note: str = Field(description="At most 120 words")
    recruiter_message: str = Field(description="At most 60 words")


async def draft_messages(llm: Gemini, *, master: ResumeData, source_text: str | None, title: str, company: str,
                         description: str | None, job_terms: list[str]) -> dict:
    job = untrusted("job_posting", f"Title: {title}\nCompany: {company}\n\n{description or '(title only)'}")
    errors: list[str] = []
    for attempt in (1, 2):
        note = ("Your previous drafts were rejected for claims the resume doesn't support; remove them:\n- "
                + "\n- ".join(errors[:12]) + "\n\n") if errors else ""
        d = await llm.json(Drafts, PROMPT.format(errors=note, master=master.model_dump_json(exclude_none=True), job=job),
                           purpose="draft_messages", temperature=0.4)
        errors = (text_problems("cover note", d.cover_note, master, source_text, job_terms)
                  + text_problems("recruiter message", d.recruiter_message, master, source_text, job_terms))
        if not errors:
            return {"ok": True, "cover_note": d.cover_note.strip(), "recruiter_message": d.recruiter_message.strip(),
                    "attempts": attempt, "prompt_version": PROMPT_VERSION}
    return {"ok": False, "unsupported": errors[:12], "attempts": 2, "prompt_version": PROMPT_VERSION}

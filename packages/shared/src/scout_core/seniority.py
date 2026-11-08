"""Second opinion on jobs whose experience level the plain rules couldn't decide (added 2026-11-07).

Many postings never state years ("Software Engineer, Backend (Python)"). Gemini reads the
description and must quote the exact sentence its answer rests on; plain code then checks that the
quote really is in the posting. Only a verified quote changes anything:
  fresher_ok       -> shown in the feed (it says freshers / new grads / 0 years / entry level)
  needs_experience -> dropped (it asks for prior professional experience)
  unclear          -> stays hidden, as before
"""

import re
from typing import Literal

from pydantic import BaseModel, Field

from scout_core.llm import Gemini, untrusted

PROMPT_VERSION = "seniority-v2"
MARK = "AI check:"  # prefix on fresher_reasons; also marks a job as already checked

PROMPT = """You decide whether a job is open to a fresher: a 2026 graduate with 0 years of full-time
work experience (internships and personal projects only).

Answer "fresher_ok" ONLY if the posting itself says so: freshers, new grads, recent graduates,
0 years, no experience required, entry level, campus / graduate programme, level-1 role, or an
internship that converts to full time.
Answer "needs_experience" if it asks for prior professional experience: any number of years above 1,
"proven experience", "experience building/shipping X in production", "you have worked as", mentoring
others, owning architecture, or similar.
Read the WHOLE posting, including requirements, qualifications and "who you are" sections, before
answering. Any demand for prior professional experience anywhere in it wins: "entry level", "intern",
"early career" or "freshers welcome" elsewhere does not cancel "2-3 years of experience" or a required
Master's / PhD research record. Ignore legal or EEO footers ("equal opportunity employer").
Otherwise answer "unclear". Do not guess from the job title or company.

"evidence" must be one sentence copied exactly, word for word, from the posting. Use "" for unclear.

{job}"""


class SeniorityRead(BaseModel):
    verdict: Literal["fresher_ok", "needs_experience", "unclear"]
    min_years: float | None = Field(default=None, description="Minimum years asked for, if stated")
    evidence: str = Field(default="", description="Exact sentence copied from the posting")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9%+]+", " ", s.lower()).strip()


def quote_found(evidence: str, title: str, description: str) -> bool:
    """The quote must be real: at least 4 words, found (ignoring case and punctuation) in the posting."""
    q = _norm(evidence)
    return len(q.split()) >= 4 and q in _norm(f"{title}\n{description}")


_FRESHER_SIGNAL = re.compile(
    r"fresher|fresh graduate|new grad|recent grad|graduat|entry[- ]level|0 years|zero years|no (prior )?"
    r"experience|campus|batch|class of|early career|level 1|level i\b|intern|trainee|no minimum experience|"
    # "0-1 years", "0 to 1 year", "zero to one year", "no more than 12 months" (2026-11-08 eval)
    r"\b0\s*(?:-|to|–)\s*(?:1|12)\s*(?:years?|yrs?|months?)|zero to one|(?:no|not)(?: have)? more than (?:12|twelve) months|"
    r"(?:completing|finishing|complete|completed) (?:their|your|the) degree (?:in|by) 20\d\d",
    re.IGNORECASE)


def supports_fresher(quote: str) -> bool:
    """A "fresher_ok" quote must itself say so, must not ask for experience, and must not be an instruction."""
    from scout_core.filters.fresher import Decision, classify
    from scout_core.llm import suspected_injection

    if suspected_injection(quote) or not _FRESHER_SIGNAL.search(quote):
        return False
    return classify("role", quote).decision != Decision.DROP and not re.search(
        r"\b([2-9]|1\d)\s*\+?\s*(years?|yrs)", quote, re.IGNORECASE)


async def read_seniority(llm: Gemini, *, title: str, description: str) -> tuple[str, str]:
    """Returns (verdict, reason line). The verdict is "unclear" unless the quote checks out."""
    from scout_core.llm import suspected_injection

    if hit := suspected_injection(f"{title}\n{description}"):
        # A posting that tries to instruct the model is never settled by the model: stays hidden.
        return "unclear", f"{MARK} posting contains instruction-like text ('{hit[:40]}'), left hidden"
    r = await llm.json(SeniorityRead, PROMPT.format(job=untrusted("job_posting", f"Title: {title}\n\n{description}")),
                       purpose="seniority", temperature=0.0)
    if r.verdict == "unclear":
        return "unclear", f"{MARK} description doesn't say whether freshers can apply"
    if not quote_found(r.evidence, title, description):
        return "unclear", f"{MARK} no verifiable quote, left hidden"
    quote = r.evidence.strip()[:160]
    if r.verdict == "fresher_ok":
        if not supports_fresher(quote):
            return "unclear", f"{MARK} quote doesn't show it's open to freshers, left hidden"
        return "fresher_ok", f"{MARK} open to freshers: \"{quote}\""
    return "needs_experience", f"{MARK} asks for experience: \"{quote}\""

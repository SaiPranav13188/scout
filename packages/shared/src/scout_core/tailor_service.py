"""Generate, store and fetch tailored resumes (one new `resumes_generated` row per version).

The stored row holds the final document (after one-page fitting) and every check's result. PDFs and
DOCX files are rendered from it on request, so the same version always produces the same file.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scout_core.db.models import Job, ResumeGenerated
from scout_core.llm import Gemini
from scout_core.render_resume import ats_text_check, fit_one_page, page_count
from scout_core.resume import Contact
from scout_core.resume_store import active_resume, resume_data
from scout_core.runs import audit
from scout_core.tailor import PROMPT_VERSION, tailor


class NoMasterResume(LookupError):
    pass


async def generate(session: AsyncSession, llm: Gemini, job: Job, *, actor: str) -> ResumeGenerated:
    """Tailor, validate, fit to one page, check text extraction, and store a new version. Caller commits."""
    master_row = await active_resume(session)
    if master_row is None:
        raise NoMasterResume("Upload your resume first (Resume page).")
    parsed = master_row.parsed or {}
    master, contact = resume_data(parsed), Contact.model_validate(parsed.get("contact") or {})
    result = await tailor(llm, master=master, contact=contact, source_text=parsed.get("source_text"),
                          title=job.title, company=job.company_name, description=job.description,
                          job_terms=[*(job.required_skills or []), *(job.preferred_skills or [])])
    doc, pdf, dropped = fit_one_page(result["content"])
    checks = {**result["validation"], "pages": page_count(pdf), "ats": ats_text_check(pdf, doc),
              "dropped_to_fit": dropped}
    checks["passed"] = bool(checks["ok"] and checks["pages"] == 1 and checks["ats"]["ok"])
    row = ResumeGenerated(
        job_id=job.id, master_resume_id=master_row.id, file_url_pdf="render:pdf", file_url_docx="render:docx",
        content={"document": doc, "tailored": result["tailored"], "attempts": result["attempts"],
                 "emphasis": result["emphasis"], "prompt_version": PROMPT_VERSION,
                 "model": llm.usage.by_call[-1]["model"] if llm.usage.by_call else None,
                 "master_version": master_row.version},
        validation_result=checks)
    session.add(row)
    await session.flush()
    audit(session, actor=actor, action="generate_resume", entity="resumes_generated", entity_id=str(row.id),
          after={"job_id": str(job.id), "tailored": result["tailored"], "passed": checks["passed"]})
    return row


def summary(row: ResumeGenerated, job: Job | None = None) -> dict[str, Any]:
    c, v = row.content or {}, row.validation_result or {}
    out = {"id": row.id, "job_id": row.job_id, "created_at": row.created_at, "tailored": c.get("tailored", False),
           "passed": v.get("passed", False), "unsupported": v.get("unsupported", []),
           "fallback_reason": v.get("fallback_reason", []), "pages": v.get("pages"),
           "ats_ok": (v.get("ats") or {}).get("ok"), "dropped_to_fit": v.get("dropped_to_fit", []),
           "emphasis": c.get("emphasis", []), "master_version": c.get("master_version")}
    if job is not None:
        out |= {"job_title": job.title, "company": job.company_name}
    return out


async def latest_for_job(session: AsyncSession, job_id) -> ResumeGenerated | None:
    return await session.scalar(select(ResumeGenerated).where(ResumeGenerated.job_id == job_id)
                                .order_by(ResumeGenerated.created_at.desc()).limit(1))


def file_name(row: ResumeGenerated, job: Job | None, ext: str) -> str:
    import re
    name = ((row.content or {}).get("document", {}).get("contact") or {}).get("name") or "Resume"
    parts = [name, job.company_name if job else None, job.title if job else None]
    stem = "_".join(re.sub(r"[^A-Za-z0-9]+", "-", p).strip("-") for p in parts if p)[:90]
    return f"{stem or 'Resume'}.{ext}"

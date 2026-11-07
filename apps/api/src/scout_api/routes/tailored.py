"""Tailored resumes (spec §6.4, §7, §9.1 "Download tailored resume", §9.4 version history)."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scout_api.deps import db
from scout_core.db.models import Job, ResumeGenerated
from scout_core.llm import Gemini, LLMError, LLMNotConfigured
from scout_core.render_resume import render_docx, render_pdf
from scout_core.tailor_service import NoMasterResume, file_name, generate, summary

router = APIRouter(prefix="/api", tags=["tailored"])

MEDIA = {"pdf": "application/pdf",
         "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


@router.post("/jobs/{job_id}/resume")
async def create_for_job(job_id: UUID, session: Annotated[AsyncSession, Depends(db)]):
    """Make a new tailored version for this job (every call is a new version; old ones are kept)."""
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    from scout_core.resume_store import active_resume
    if await active_resume(session) is None:  # the more useful message first
        raise HTTPException(409, "Upload your resume first (Resume page).")
    try:
        llm = Gemini.from_settings()
        row = await generate(session, llm, job, actor="me")
    except NoMasterResume as exc:
        raise HTTPException(409, str(exc)) from exc
    except LLMNotConfigured as exc:
        raise HTTPException(503, "Gemini isn't configured on the server (GEMINI_API_KEY).") from exc
    except LLMError as exc:
        raise HTTPException(502, f"Gemini didn't answer: {str(exc)[:160]}. Try again in a minute.") from exc
    await session.commit()
    return summary(row, job)


@router.get("/jobs/{job_id}/resumes")
async def list_for_job(job_id: UUID, session: Annotated[AsyncSession, Depends(db)]):
    rows = (await session.scalars(select(ResumeGenerated).where(ResumeGenerated.job_id == job_id)
                                  .order_by(ResumeGenerated.created_at.desc()))).all()
    return [summary(r) for r in rows]


@router.get("/resumes")
async def list_all(session: Annotated[AsyncSession, Depends(db)], limit: int = 100):
    rows = (await session.execute(select(ResumeGenerated, Job).join(Job, Job.id == ResumeGenerated.job_id)
                                  .order_by(ResumeGenerated.created_at.desc()).limit(min(limit, 300)))).all()
    return [summary(r, j) for r, j in rows]


@router.get("/resumes/{resume_id}/file")
async def download(resume_id: UUID, session: Annotated[AsyncSession, Depends(db)],
                   format: Literal["pdf", "docx"] = "pdf"):  # noqa: A002 - query parameter name
    row = await session.get(ResumeGenerated, resume_id)
    if row is None:
        raise HTTPException(404, "resume not found")
    job = await session.get(Job, row.job_id)
    doc = (row.content or {}).get("document") or {}
    data = render_pdf(doc) if format == "pdf" else render_docx(doc)
    return Response(data, media_type=MEDIA[format], headers={
        "Content-Disposition": f'attachment; filename="{file_name(row, job, format)}"',
        "Cache-Control": "no-store"})


@router.post("/jobs/{job_id}/drafts")
async def drafts_for_job(job_id: UUID, session: Annotated[AsyncSession, Depends(db)]):
    """Cover note + recruiter message for this job. Drafts only; I send them myself."""
    from scout_core.drafts import draft_messages
    from scout_core.resume_store import active_resume, resume_data
    from scout_core.runs import audit

    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    master_row = await active_resume(session)
    if master_row is None:
        raise HTTPException(409, "Upload your resume first (Resume page).")
    parsed = master_row.parsed or {}
    try:
        result = await draft_messages(Gemini.from_settings(), master=resume_data(parsed), source_text=parsed.get("source_text"),
                                      title=job.title, company=job.company_name, description=job.description,
                                      job_terms=[*(job.required_skills or []), *(job.preferred_skills or [])])
    except LLMNotConfigured as exc:
        raise HTTPException(503, "Gemini isn't configured on the server (GEMINI_API_KEY).") from exc
    except LLMError as exc:
        raise HTTPException(502, f"Gemini didn't answer: {str(exc)[:160]}. Try again in a minute.") from exc
    audit(session, actor="me", action="draft_messages", entity="job", entity_id=str(job_id), after=result)
    await session.commit()
    return result

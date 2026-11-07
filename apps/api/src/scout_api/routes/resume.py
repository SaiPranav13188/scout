"""Resume Manager (spec §7, §9.4): review and correct the parsed master resume, upload a new one,
switch between versions. Saved versions are never edited; every change is a new version."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from scout_api.deps import db
from scout_core.db.models import Job, JobScore, MasterResume
from scout_core.llm import Gemini, LLMError, LLMNotConfigured
from scout_core.matching import PROMPT_VERSION, profile_text
from scout_core.resume import (
    Contact,
    ResumeData,
    ResumeFileError,
    clean_contact,
    clean_resume,
    extract_resume_bytes,
    fact_check,
    parse_resume_text,
    profile_fingerprint,
)
from scout_core.resume_store import activate, active_resume, add_version, resume_data
from scout_core.runs import audit
from scout_core.services import visible_feed_query
from scout_core.settings_store import get_setting
from scout_core.types import ApplicationStatus

router = APIRouter(prefix="/api/resume", tags=["resume"])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_EDIT_CHARS = 60_000  # a one-page resume is ~5k characters; this only stops runaway input


class ResumeEdit(BaseModel):
    base_version: int  # the version the edit started from; a newer active version means a stale edit
    data: ResumeData
    contact: Contact


class CheckIn(BaseModel):
    data: ResumeData


def _source(row: MasterResume) -> str:
    kind, _, name = row.file_url.partition(":")
    return {"local": f"Parsed from {name}", "upload": f"Uploaded {name}",
            "edit": f"Your corrections to version {name}"}.get(kind, row.file_url)


def version_summary(row: MasterResume) -> dict[str, Any]:
    p = row.parsed or {}
    return {"version": row.version, "is_active": row.is_active, "uploaded_at": row.uploaded_at,
            "source": _source(row), "reviewed": bool(p.get("reviewed")),
            "unsupported": len((p.get("fact_check") or {}).get("unsupported", []))}


def version_detail(row: MasterResume) -> dict[str, Any]:
    p = row.parsed or {}
    return {**version_summary(row), "data": resume_data(p).model_dump(),
            "contact": Contact.model_validate(p.get("contact") or {}).model_dump(),
            "fact_check": p.get("fact_check") or {"ok": True, "unsupported": []},
            "parsed_with": p.get("parsed_with"), "edited_from": p.get("edited_from"),
            "can_fact_check": bool(p.get("source_text"))}


async def scoring_status(session: AsyncSession, row: MasterResume | None) -> dict[str, int]:
    """How many feed jobs are scored against this resume, against an older one, or not yet."""
    if row is None:
        return {"current": 0, "outdated": 0, "unscored": 0}
    targets = (await get_setting(session, "matching")).get("target_roles", [])
    fp = profile_fingerprint(profile_text(resume_data(row.parsed or {}), targets), PROMPT_VERSION)
    feed = visible_feed_query(include_unclear=True).where(Job.status == ApplicationStatus.DISCOVERED)
    feed_ids = feed.with_only_columns(Job.id).subquery()
    latest = (select(JobScore.job_id, JobScore.profile_hash).where(
        JobScore.prompt_version == PROMPT_VERSION, JobScore.job_id.in_(select(feed_ids.c.id)))
        .ext(distinct_on(JobScore.job_id)).order_by(JobScore.job_id, JobScore.created_at.desc()).subquery())
    current = await session.scalar(select(func.count()).select_from(latest).where(latest.c.profile_hash == fp))
    scored = await session.scalar(select(func.count()).select_from(latest))
    total = await session.scalar(select(func.count()).select_from(feed_ids))
    return {"current": int(current or 0), "outdated": int((scored or 0) - (current or 0)),
            "unscored": int((total or 0) - (scored or 0))}


async def _overview(session: AsyncSession) -> dict[str, Any]:
    rows = (await session.scalars(select(MasterResume).order_by(MasterResume.version.desc()))).all()
    active = next((r for r in rows if r.is_active), None)
    return {"active": version_detail(active) if active else None,
            "versions": [version_summary(r) for r in rows],
            "scoring": await scoring_status(session, active)}


@router.get("")
async def get_resume(session: Annotated[AsyncSession, Depends(db)]):
    return await _overview(session)


@router.get("/versions/{version}")
async def get_version(version: int, session: Annotated[AsyncSession, Depends(db)]):
    row = await session.scalar(select(MasterResume).where(MasterResume.version == version))
    if row is None:
        raise HTTPException(404, "version not found")
    return version_detail(row)


@router.post("/check")
async def check(body: CheckIn, session: Annotated[AsyncSession, Depends(db)]):
    """Dry run of the fact check while I edit: what isn't in the uploaded file."""
    row = await active_resume(session)
    source = (row.parsed or {}).get("source_text") if row else None
    if not source:
        return {"ok": True, "unsupported": [], "available": False}
    return {**fact_check(clean_resume(body.data), source), "available": True}


@router.put("")
async def save_resume(body: ResumeEdit, session: Annotated[AsyncSession, Depends(db)]):
    """Save my reviewed/corrected resume as a new version (the version it was based on stays as is)."""
    if len(body.model_dump_json()) > MAX_EDIT_CHARS:
        raise HTTPException(413, "Resume is too long.")
    current = await active_resume(session)
    if current is None:
        raise HTTPException(409, "No resume yet. Upload one first.")
    if current.version != body.base_version:
        raise HTTPException(409, f"Version {current.version} became active while you were editing. Reload to continue.")
    data, contact = clean_resume(body.data), clean_contact(body.contact)
    p = current.parsed or {}
    unchanged = (data.model_dump() == resume_data(p).model_dump()
                 and contact.model_dump() == Contact.model_validate(p.get("contact") or {}).model_dump())
    if unchanged and p.get("reviewed"):
        return {**await _overview(session), "saved": False}
    source = p.get("source_text")
    check_result = fact_check(data, source) if source else {"ok": True, "unsupported": []}
    row = await add_version(session, file_url=f"edit:{current.version}", parsed={
        **data.model_dump(), "contact": contact.model_dump(), "fact_check": check_result,
        "source_text": source, "parsed_with": p.get("parsed_with"), "reviewed": True,
        "edited_from": current.version})
    audit(session, actor="me", action="edit_resume", entity="master_resume", entity_id=str(row.version),
          before={"version": current.version}, after={"version": row.version, "unchanged": unchanged,
                                                      "unsupported": len(check_result["unsupported"])})
    await session.commit()
    return {**await _overview(session), "saved": True}


@router.post("/versions/{version}/activate")
async def activate_version(version: int, session: Annotated[AsyncSession, Depends(db)]):
    before = await active_resume(session)
    if await activate(session, version) is None:
        raise HTTPException(404, "version not found")
    audit(session, actor="me", action="activate_resume", entity="master_resume", entity_id=str(version),
          before={"version": before.version if before else None}, after={"version": version})
    await session.commit()
    return await _overview(session)


@router.post("/upload")
async def upload_resume(session: Annotated[AsyncSession, Depends(db)], file: Annotated[UploadFile, File()]):
    """Upload a new master resume (PDF or DOCX). Parsed with Gemini; contact details never leave the server."""
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "The file is larger than 5 MB.")
    try:
        raw = extract_resume_bytes(data, file.filename or "")
    except ResumeFileError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        llm = Gemini.from_settings()
        parsed = await parse_resume_text(llm, raw)
    except LLMNotConfigured as exc:
        raise HTTPException(503, "Gemini isn't configured on the server (GEMINI_API_KEY).") from exc
    except LLMError as exc:
        raise HTTPException(502, f"Parsing failed: {str(exc)[:200]}. Try again in a minute.") from exc
    name = (file.filename or "resume.pdf").replace("/", "_").replace("\\", "_")[:120]
    row = await add_version(session, file_url=f"upload:{name}", parsed=parsed)
    audit(session, actor="me", action="upload_resume", entity="master_resume", entity_id=str(row.version),
          after={"file": name, "unsupported": len(parsed["fact_check"]["unsupported"])})
    await session.commit()
    return await _overview(session)


@router.get("/autofill-profile")
async def autofill_profile(session: Annotated[AsyncSession, Depends(db)]):
    """Profile for the Chrome autofill extension (Phase 4b), built from my active, reviewed resume.

    Only plain facts a job form asks for. Questions that are my call (work authorisation, salary,
    notice period, demographics, consent) are never part of it.
    """
    from fastapi.responses import JSONResponse

    row = await active_resume(session)
    if row is None:
        raise HTTPException(409, "No resume yet. Upload one first.")
    from scout_core.autofill import build_profile
    profile = build_profile(row)
    return JSONResponse(profile, headers={"Content-Disposition": 'attachment; filename="scout-autofill-profile.json"'})

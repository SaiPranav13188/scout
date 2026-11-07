"""Pipeline (spec §9.2) and Agent health summary (spec §9.6)."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from scout_api.deps import db
from scout_core.db.models import AgentRun, Application, Job, ResumeGenerated
from scout_core.runs import audit
from scout_core.schedule import next_runs
from scout_core.services import set_status
from scout_core.settings_store import get_setting
from scout_core.types import ApplicationStatus

router = APIRouter(prefix="/api", tags=["pipeline"])

PIPELINE = ("applied", "interview", "offer", "rejected")


class ApplicationPatch(BaseModel):
    status: Literal["applied", "interview", "offer", "rejected", "discovered"] | None = None
    notes: str | None = Field(default=None, max_length=4000)
    follow_up_at: datetime | None = None
    clear_follow_up: bool = False


def _out(app: Application, job: Job, resume_id) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {"job_id": job.id, "title": job.title, "company": job.company_name, "location": job.location,
            "apply_url": job.apply_url, "source": job.source, "status": app.status.value,
            "applied_at": app.applied_at, "notes": app.notes, "follow_up_at": app.follow_up_at,
            "follow_up_due": bool(app.follow_up_at and app.follow_up_at <= now + timedelta(days=1)),
            "updated_at": app.updated_at, "resume_id": resume_id}


async def _rows(session: AsyncSession, job_id: UUID | None = None):
    latest = (select(ResumeGenerated.job_id, func.max(ResumeGenerated.created_at).label("at"))
              .group_by(ResumeGenerated.job_id).subquery())
    resume = (select(ResumeGenerated.job_id, ResumeGenerated.id).join(
        latest, (latest.c.job_id == ResumeGenerated.job_id) & (latest.c.at == ResumeGenerated.created_at)).subquery())
    q = (select(Application, Job, resume.c.id).join(Job, Job.id == Application.job_id)
         .outerjoin(resume, resume.c.job_id == Job.id)
         .where(Application.status.in_([ApplicationStatus(s) for s in PIPELINE])))
    if job_id:
        q = q.where(Application.job_id == job_id)
    return (await session.execute(q.order_by(Application.updated_at.desc()))).all()


@router.get("/applications")
async def list_applications(session: Annotated[AsyncSession, Depends(db)]):
    return [_out(a, j, r) for a, j, r in await _rows(session)]


@router.patch("/applications/{job_id}")
async def update_application(job_id: UUID, body: ApplicationPatch, session: Annotated[AsyncSession, Depends(db)]):
    """Move between columns, edit notes, set or clear the follow-up date. Only I do this."""
    app = await session.scalar(select(Application).where(Application.job_id == job_id))
    if app is None:
        raise HTTPException(404, "not in your pipeline")
    if body.status and body.status != app.status.value:
        await set_status(session, job_id, ApplicationStatus(body.status), actor="me")
        await session.refresh(app)  # set_status wrote the row with a bulk upsert
    before = {"notes": app.notes, "follow_up_at": app.follow_up_at.isoformat() if app.follow_up_at else None}
    if body.notes is not None:
        app.notes = body.notes.strip() or None
    if body.clear_follow_up:
        app.follow_up_at = None
    elif body.follow_up_at is not None:
        app.follow_up_at = body.follow_up_at if body.follow_up_at.tzinfo else body.follow_up_at.replace(tzinfo=UTC)
    if body.notes is not None or body.clear_follow_up or body.follow_up_at is not None:
        audit(session, actor="me", action="edit_application", entity="application", entity_id=str(job_id), before=before,
              after={"notes": app.notes, "follow_up_at": app.follow_up_at.isoformat() if app.follow_up_at else None})
    await session.commit()
    rows = await _rows(session, job_id)
    return _out(*rows[0]) if rows else {"job_id": job_id, "status": body.status}


WORKERS = ("ingest", "gmail", "scoring", "verify", "discovery", "daily_digest", "evening_summary")


@router.get("/health/summary")
async def health_summary(session: Annotated[AsyncSession, Depends(db)]):
    """Per worker: last run, last success, next due time, 24h runs/failures/retries; LLM usage."""
    now = datetime.now(UTC)
    day = now - timedelta(hours=24)
    schedule = await get_setting(session, "schedule")
    upcoming = next_runs(now, schedule)
    paused = bool(await get_setting(session, "paused"))
    workers = []
    for w in WORKERS:
        last = await session.scalar(select(AgentRun).where(AgentRun.worker == w).order_by(AgentRun.started_at.desc()).limit(1))
        ok = await session.scalar(select(func.max(AgentRun.finished_at)).where(
            AgentRun.worker == w, AgentRun.status.in_(["success", "partial"])))
        stats = (await session.execute(select(
            func.count(), func.count().filter(AgentRun.status == "error"), func.coalesce(func.sum(AgentRun.retries), 0))
            .where(AgentRun.worker == w, AgentRun.started_at >= day))).one()
        workers.append({"worker": w, "last_status": last.status if last else None,
                        "last_started_at": last.started_at if last else None,
                        "last_error": last.error if last and last.status == "error" else None,
                        "last_success_at": ok, "next_due_at": None if paused else upcoming.get(w),
                        "runs_24h": stats[0], "failures_24h": stats[1], "retries_24h": int(stats[2])})
    usage: dict[str, int] = {"llm_calls": 0, "llm_input_tokens": 0, "llm_output_tokens": 0}
    for (counts,) in (await session.execute(select(AgentRun.counts).where(AgentRun.started_at >= day))).all():
        for k in usage:
            usage[k] += int((counts or {}).get(k, 0) or 0)
    return {"paused": paused, "workers": workers, "llm_24h": usage,
            "llm_cost_note": "Gemini free tier: no charge. Calls made from the dashboard (resume parsing, "
                             "tailoring on demand) aren't counted here."}

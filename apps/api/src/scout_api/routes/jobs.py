from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from scout_api.deps import db
from scout_api.schemas import ExperienceCorrectionIn, JobDetail, JobPage, LabelIn, StatusIn, job_out
from scout_core.db.models import Company, Job, JobLabel, JobScore
from scout_core.matching import PROMPT_VERSION
from scout_core.runs import audit
from scout_core.services import MIN_FEED_RELEVANCE, ai_field, feed_query, set_status
from scout_core.settings_store import get_setting
from scout_core.types import ApplicationStatus

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value) if isinstance(value, str) else value


@router.get("", response_model=JobPage)
async def list_jobs(
    session: Annotated[AsyncSession, Depends(db)],
    q: str | None = Query(None, max_length=200),
    source: Annotated[list[str] | None, Query()] = None,
    family: Annotated[list[str] | None, Query()] = None,
    location: Annotated[list[str] | None, Query()] = None,
    remote: bool | None = None,
    posted_within_hours: int | None = Query(None, ge=1, le=24 * 60),
    min_relevance: int = Query(0, ge=0, le=100),
    all_roles: bool = False,
    include_unclear: bool = False,
    status: str = Query("discovered"),
    sort: str = Query("newest", pattern="^(newest|relevance)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
):
    stmt = feed_query()
    if status != "all":
        stmt = stmt.where(Job.status == ApplicationStatus(status))
    if not all_roles:
        stmt = stmt.where(Job.role_family != "unrelated", Job.relevance_score >= MIN_FEED_RELEVANCE, ai_field())
    stmt = stmt.where(Job.trust_level != "scam_risk")
    unclear_count_q = stmt.where(Job.experience_unclear.is_(True))
    if not include_unclear:
        stmt = stmt.where(Job.experience_unclear.is_(False))
    # Facets are computed before the user-selected facet filters so counts stay useful.
    base = stmt
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Job.title.ilike(like), Job.company_name.ilike(like), Job.location.ilike(like)))
        base = stmt
    if source:
        stmt = stmt.where(Job.source.in_(source))
    if family:
        stmt = stmt.where(Job.role_family.in_(family))
    if location:
        stmt = stmt.where(Job.location_bucket.in_(location))
    if remote is not None:
        stmt = stmt.where(Job.remote.is_(remote))
    if posted_within_hours:
        since = datetime.now(UTC) - timedelta(hours=posted_within_hours)
        stmt = stmt.where(func.coalesce(Job.posted_at, Job.first_seen_at) >= since)
    stmt = stmt.where(Job.relevance_score >= min_relevance)

    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    posted = func.coalesce(Job.posted_at, Job.first_seen_at)
    # One row per job: its newest score (a job is re-scored when my resume changes).
    scored = (select(JobScore.job_id, JobScore.score).where(JobScore.prompt_version == PROMPT_VERSION)
              .ext(distinct_on(JobScore.job_id)).order_by(JobScore.job_id, JobScore.created_at.desc()).subquery())
    # "Best fit": the resume match score when a job has one, otherwise the plain-code role fit.
    fit = func.coalesce(scored.c.score, Job.relevance_score)
    order = [posted.desc(), fit.desc()] if sort == "newest" else [fit.desc(), posted.desc()]
    rows = (await session.execute(
        stmt.add_columns(Company).outerjoin(Company, Company.id == Job.company_id)
        .outerjoin(scored, scored.c.job_id == Job.id)
        .order_by(*order, Job.id).offset(offset).limit(limit))).all()

    facets: dict[str, dict[str, int]] = {}
    sub = base.subquery()
    for name, col in (("source", sub.c.source), ("family", sub.c.role_family), ("location", sub.c.location_bucket)):
        facets[name] = {k: v for k, v in (await session.execute(
            select(col, func.count()).group_by(col))).all()}
    facets["experience"] = {"unclear": int(await session.scalar(
        select(func.count()).select_from(unclear_count_q.subquery())) or 0)}

    last_visit = _parse_dt(await get_setting(session, "last_visit_at"))
    ids = [job.id for job, _ in rows]
    labels = dict((await session.execute(select(JobLabel.job_id, JobLabel.label).where(JobLabel.job_id.in_(ids)))).all()) if ids else {}
    scores = await _scores(session, ids)
    bands = (await get_setting(session, "matching")).get("bands")
    items = [job_out(job, company, last_visit, label=labels.get(job.id), score=scores.get(job.id), bands=bands)
             for job, company in rows]
    next_offset = offset + limit if offset + limit < (total or 0) else None
    return JobPage(items=items, total=int(total or 0), next_offset=next_offset, facets=facets)


async def _scores(session: AsyncSession, ids: list) -> dict:
    if not ids:
        return {}
    rows = (await session.scalars(select(JobScore).where(
        JobScore.job_id.in_(ids), JobScore.prompt_version == PROMPT_VERSION).order_by(JobScore.created_at))).all()
    return {r.job_id: r for r in rows}  # newest wins


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(job_id: UUID, session: Annotated[AsyncSession, Depends(db)]):
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    company = await session.get(Company, job.company_id) if job.company_id else None
    last_visit = _parse_dt(await get_setting(session, "last_visit_at"))
    label = await session.scalar(select(JobLabel.label).where(JobLabel.job_id == job_id))
    bands = (await get_setting(session, "matching")).get("bands")
    return job_out(job, company, last_visit, detail=True, label=label, score=(await _scores(session, [job_id])).get(job_id),
                   bands=bands)


@router.post("/{job_id}/status", response_model=JobDetail)
async def update_status(job_id: UUID, body: StatusIn, session: Annotated[AsyncSession, Depends(db)]):
    try:
        job = await set_status(session, job_id, ApplicationStatus(body.status), actor="me", notes=body.notes)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    company = await session.get(Company, job.company_id) if job.company_id else None
    return job_out(job, company, None, detail=True)


@router.post("/{job_id}/experience", response_model=JobDetail)
async def correct_experience(job_id: UUID, body: ExperienceCorrectionIn,
                             session: Annotated[AsyncSession, Depends(db)]):
    """My correction for an 'experience unclear' job (spec §6.3)."""
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    before = {"passed_filters": job.passed_filters, "experience_unclear": job.experience_unclear}
    job.experience_unclear = False
    job.experience_corrected_by_me = True
    if body.entry_level:
        job.fresher_confidence = "high"
        job.fresher_reasons = ["confirmed entry-level by me", *(job.fresher_reasons or [])]
    else:
        job.passed_filters = False
        job.filter_reasons = ["experience: marked not entry-level by me", *(job.filter_reasons or [])]
    audit(session, actor="me", action="correct_experience", entity="job", entity_id=str(job_id),
          before=before, after={"passed_filters": job.passed_filters, "entry_level": body.entry_level})
    await session.commit()
    company = await session.get(Company, job.company_id) if job.company_id else None
    return job_out(job, company, None, detail=True)


@router.put("/{job_id}/label")
async def set_label(job_id: UUID, body: LabelIn, session: Annotated[AsyncSession, Depends(db)]):
    """My apply / maybe / no judgement. These labels become the Phase 3 matching eval set."""
    if await session.get(Job, job_id) is None:
        raise HTTPException(404, "job not found")
    existing = await session.scalar(select(JobLabel).where(JobLabel.job_id == job_id))
    if body.label is None:
        if existing:
            await session.delete(existing)
    elif existing:
        existing.label, existing.note = body.label, body.note
    else:
        session.add(JobLabel(job_id=job_id, label=body.label, note=body.note))
    audit(session, actor="me", action="label_job", entity="job", entity_id=str(job_id),
          after={"label": body.label})
    await session.commit()
    return await label_summary(session)


labels_router = APIRouter(prefix="/api/labels", tags=["labels"])


@labels_router.get("/summary")
async def label_summary_route(session: Annotated[AsyncSession, Depends(db)]):
    return await label_summary(session)


LABEL_TARGET = 40  # spec: 30-50 labelled jobs before trusting the match score


async def label_summary(session: AsyncSession) -> dict:
    counts = dict((await session.execute(select(JobLabel.label, func.count()).group_by(JobLabel.label))).all())
    total = sum(counts.values())
    return {"apply": counts.get("apply", 0), "maybe": counts.get("maybe", 0), "no": counts.get("no", 0),
            "total": total, "target": LABEL_TARGET}

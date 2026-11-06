from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from scout_api.deps import db
from scout_api.schemas import ExperienceCorrectionIn, JobDetail, JobPage, StatusIn, job_out
from scout_core.db.models import Company, Job
from scout_core.runs import audit
from scout_core.services import MIN_FEED_RELEVANCE, feed_query, set_status
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
    status: str = Query("discovered"),
    sort: str = Query("newest", pattern="^(newest|relevance)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
):
    stmt = feed_query()
    if status != "all":
        stmt = stmt.where(Job.status == ApplicationStatus(status))
    if not all_roles:
        stmt = stmt.where(Job.role_family != "unrelated", Job.relevance_score >= MIN_FEED_RELEVANCE)
    stmt = stmt.where(Job.trust_level != "scam_risk")
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
    order = [posted.desc(), Job.relevance_score.desc()] if sort == "newest" else \
            [Job.relevance_score.desc(), posted.desc()]
    rows = (await session.execute(
        stmt.add_columns(Company).outerjoin(Company, Company.id == Job.company_id)
        .order_by(*order, Job.id).offset(offset).limit(limit))).all()

    facets: dict[str, dict[str, int]] = {}
    sub = base.subquery()
    for name, col in (("source", sub.c.source), ("family", sub.c.role_family), ("location", sub.c.location_bucket)):
        facets[name] = {k: v for k, v in (await session.execute(
            select(col, func.count()).group_by(col))).all()}

    last_visit = _parse_dt(await get_setting(session, "last_visit_at"))
    items = [job_out(job, company, last_visit) for job, company in rows]
    next_offset = offset + limit if offset + limit < (total or 0) else None
    return JobPage(items=items, total=int(total or 0), next_offset=next_offset, facets=facets)


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(job_id: UUID, session: Annotated[AsyncSession, Depends(db)]):
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    company = await session.get(Company, job.company_id) if job.company_id else None
    last_visit = _parse_dt(await get_setting(session, "last_visit_at"))
    return job_out(job, company, last_visit, detail=True)


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

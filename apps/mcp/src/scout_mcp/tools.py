"""What the Scout MCP server can do, as plain async functions over a DB session (tested directly).

Reads use the same feed rules as the dashboard, Telegram and stats (visible_feed_query), so Claude
never sees a job the feed would hide. Writes go through the same services as the API (set_status,
set_label), so they are audited with actor "claude-mcp". Job text comes from third-party postings
and is returned inside <untrusted_job_posting> markers: data to read, never instructions.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from scout_core import services
from scout_core.db.models import Application, Job, JobLabel, JobScore
from scout_core.matching import PROMPT_VERSION
from scout_core.types import ApplicationStatus

ACTOR = "claude-mcp"
DESCRIPTION_CHARS = 3000


def _newest_scores():
    return (select(JobScore.job_id, JobScore.score, JobScore.breakdown)
            .where(JobScore.prompt_version == PROMPT_VERSION)
            .ext(distinct_on(JobScore.job_id)).order_by(JobScore.job_id, JobScore.created_at.desc()).subquery())


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _summary(job: Job, score: int | None) -> dict:
    return {"id": str(job.id), "title": job.title, "company": job.company_name, "location": job.location,
            "match_score": score, "role_fit": job.relevance_score, "why": services.why_line(job, score),
            "salary": services.salary_text(job), "posted": _iso(job.posted_at),
            "first_seen": _iso(job.first_seen_at), "source": job.source, "apply_url": job.apply_url,
            "status": job.status.value}


def _job_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"'{raw}' is not a job id (ids look like 3f2b…-…; get them from top_jobs)") from exc


async def top_jobs(session: AsyncSession, *, limit: int = 10, posted_within_hours: int | None = None,
                   include_unclear: bool = False) -> dict:
    """Open feed jobs I haven't acted on, best match first (match score, else role fit), then newest."""
    limit = max(1, min(limit, 50))
    scored = _newest_scores()
    q = services.visible_feed_query(include_unclear).where(Job.status == ApplicationStatus.DISCOVERED)
    posted = func.coalesce(Job.posted_at, Job.first_seen_at)
    if posted_within_hours:
        q = q.where(posted >= datetime.now(UTC) - timedelta(hours=posted_within_hours))
    total = await session.scalar(select(func.count()).select_from(q.subquery()))
    rows = (await session.execute(
        q.add_columns(scored.c.score).outerjoin(scored, scored.c.job_id == Job.id)
        .order_by(func.coalesce(scored.c.score, Job.relevance_score).desc(), posted.desc(), Job.id)
        .limit(limit))).all()
    return {"total_matching": int(total or 0), "jobs": [_summary(job, score) for job, score in rows]}


async def job_details(session: AsyncSession, job_id: str) -> dict:
    job = await session.get(Job, _job_id(job_id))
    if job is None:
        raise LookupError("job not found")
    score = (await session.execute(
        select(JobScore).where(JobScore.job_id == job.id, JobScore.prompt_version == PROMPT_VERSION)
        .order_by(JobScore.created_at.desc()).limit(1))).scalar_one_or_none()
    label = await session.scalar(select(JobLabel.label).where(JobLabel.job_id == job.id))
    desc = (job.description or "")[:DESCRIPTION_CHARS]
    return {
        **_summary(job, score.score if score else None),
        "my_label": label,
        "entry_level": {"confidence": job.fresher_confidence, "reasons": job.fresher_reasons or [],
                        "unclear": job.experience_unclear},
        "trust": job.trust_level,
        "match": None if score is None else {
            "parts": {k: (score.breakdown or {}).get(k) for k in ("skills", "embedding", "rubric")},
            "reasons": score.reasons or [], "matched_skills": score.matched_skills or [],
            "missing_skills": score.missing_skills or [], "red_flags": score.red_flags or []},
        "description": f"<untrusted_job_posting>\n{desc}\n</untrusted_job_posting>" if desc else None,
    }


async def stats(session: AsyncSession) -> dict:
    return {**await services.today_stats(session), "eval_labels": await services.label_summary(session)}


async def pipeline(session: AsyncSession, *, status: str | None = None) -> dict:
    """Jobs I've moved past "discovered" (applied, interview, offer, rejected; skipped only if asked)."""
    q = select(Job, Application).join(Application, Application.job_id == Job.id)
    if status:
        q = q.where(Application.status == ApplicationStatus(status))
    else:
        q = q.where(Application.status != ApplicationStatus.SKIPPED)
    rows = (await session.execute(q.order_by(Application.updated_at.desc()).limit(100))).all()
    now = datetime.now(UTC)
    return {"applications": [{
        "id": str(job.id), "title": job.title, "company": job.company_name, "status": app.status.value,
        "applied_at": _iso(app.applied_at), "follow_up_at": _iso(app.follow_up_at),
        "follow_up_due": bool(app.follow_up_at and app.follow_up_at <= now), "notes": app.notes,
        "apply_url": job.apply_url} for job, app in rows]}


async def set_job_status(session: AsyncSession, job_id: str, status: str, notes: str | None = None) -> dict:
    st = ApplicationStatus(status)
    job = await services.set_status(session, _job_id(job_id), st, actor=ACTOR, notes=notes)
    return {"id": str(job.id), "title": job.title, "company": job.company_name, "status": job.status.value}


async def label_job(session: AsyncSession, job_id: str, label: str, note: str | None = None) -> dict:
    extra = {"note": note} if note is not None else {}  # no note given: keep the saved one
    counts = await services.set_label(session, _job_id(job_id), label, actor=ACTOR, **extra)
    return {"saved": label, "labels": counts}


async def jobs_to_label(session: AsyncSession, *, limit: int = 10) -> dict:
    """Scored jobs I haven't labelled yet, for the matching eval. Full-description jobs first (their
    scores mean the most), spread across score bands so the eval sees highs and lows."""
    limit = max(1, min(limit, 30))
    scored = _newest_scores()
    rows = (await session.execute(
        select(Job, scored.c.score, scored.c.breakdown["info"].astext).join(scored, scored.c.job_id == Job.id)
        .outerjoin(JobLabel, JobLabel.job_id == Job.id).where(JobLabel.id.is_(None))
        .order_by(scored.c.score.desc(), Job.id))).all()
    full = [(j, sc) for j, sc, info in rows if info == "full"]
    rest = [(j, sc) for j, sc, info in rows if info != "full"]
    picked = (_high_low(full) + _high_low(rest))[:limit]
    return {"unlabelled_scored_jobs": len(rows), "labels": await services.label_summary(session),
            "jobs": [_summary(job, score) for job, score in picked]}


def _high_low(rows: list) -> list:
    """[highest, lowest, 2nd highest, 2nd lowest, ...] of a list sorted high to low."""
    out, lo, hi = [], 0, len(rows) - 1
    while lo <= hi:
        out.append(rows[lo])
        if lo != hi:
            out.append(rows[hi])
        lo, hi = lo + 1, hi - 1
    return out

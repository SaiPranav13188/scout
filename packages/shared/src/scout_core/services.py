"""Use-case services shared by the API, the workers and the Telegram webhook."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from scout_core.db.models import Application, Job, JobLabel, Notification
from scout_core.notify.telegram import DigestItem
from scout_core.runs import audit
from scout_core.types import TERMINAL_FOR_FEED, ApplicationStatus

# Statuses I can set from the dashboard or Telegram in Phase 1.
SETTABLE = {ApplicationStatus.DISCOVERED, ApplicationStatus.SKIPPED, ApplicationStatus.APPLIED,
            ApplicationStatus.INTERVIEW, ApplicationStatus.REJECTED, ApplicationStatus.OFFER}


def feed_query() -> Select:
    """Jobs eligible for the feed: passed filters, not a duplicate, not closed."""
    return select(Job).where(
        Job.passed_filters.is_(True),
        Job.duplicate_of.is_(None),
        or_(Job.is_open.is_(None), Job.is_open.is_(True)),
    )


MIN_FEED_RELEVANCE = 30

# My field is AI (2026-11-07): the feed, digests and alerts show AI roles, plus software / FDE roles
# whose description is clearly AI work (at least two AI keywords such as LLM, RAG, agents).
AI_KEYWORDS = ("llm", "rag", "langchain", "langgraph", "agents", "openai", "gemini", "vector", "mcp", "embeddings")


def ai_field():
    from sqlalchemy import literal_column
    ai_words = select(func.count()).select_from(
        func.jsonb_array_elements_text(Job.relevance_matched).table_valued("value").alias("w")
    ).where(literal_column("w.value").in_(AI_KEYWORDS)).scalar_subquery()
    return or_(Job.role_family == "applied_ai",
               and_(Job.role_family.in_(["fde", "backend", "fullstack", "software"]), ai_words >= 2))


# 2026-11-14: I apply to software / full-stack / backend roles as well as AI, so the feed shows all of them.
TECH_FAMILIES = ("applied_ai", "fde", "backend", "fullstack", "software")


def tech_field():
    return Job.role_family.in_(TECH_FAMILIES)


def unverified_alert():
    """Alert-email jobs shown although the email doesn't state the experience (nothing in it asks for more)."""
    from scout_core.pipeline.evaluate import UNVERIFIED_ALERT
    return and_(Job.source.like("%\\_alert", escape="\\"),
                Job.fresher_reasons.op("->>")(0) == UNVERIFIED_ALERT)


def visible_feed_query(include_unclear: bool = False) -> Select:
    """What the Today page, Telegram and stats show by default: feed jobs minus scam risk and
    off-target roles; entry-level confirmed jobs plus alert-email jobs that state no experience (labelled).
    Board jobs whose full posting doesn't settle the experience are shown only when asked for."""
    q = feed_query().where(Job.trust_level != "scam_risk", Job.role_family != "unrelated",
                           Job.relevance_score >= MIN_FEED_RELEVANCE, tech_field())
    if not include_unclear:
        q = q.where(or_(Job.experience_unclear.is_(False), unverified_alert()))
    return q


async def latest_scores(session: AsyncSession, ids: list) -> dict:
    """Match score per job id (current prompt version), for digests and alerts."""
    from scout_core.db.models import JobScore
    from scout_core.matching import PROMPT_VERSION
    if not ids:
        return {}
    rows = (await session.execute(select(JobScore.job_id, JobScore.score).where(
        JobScore.job_id.in_(ids), JobScore.prompt_version == PROMPT_VERSION).order_by(JobScore.created_at))).all()
    return {r.job_id: r.score for r in rows}  # newest wins


def why_line(job: Job, score: int | None = None) -> str:
    parts = [f"Match {score}/100"] if score is not None else []
    if job.role_family not in ("other", "unrelated"):
        parts.append(job.role_family.replace("_", " ").replace("fde", "FDE"))
    if job.fresher_reasons:
        parts.append(job.fresher_reasons[0])
    if job.relevance_matched:
        parts.append("mentions " + ", ".join(job.relevance_matched[:3]))
    return " · ".join(parts)


def salary_text(job: Job) -> str | None:
    s = job.salary or {}
    if s.get("text"):
        return s["text"]
    if s.get("min") or s.get("max"):
        cur = s.get("currency") or ""
        lo, hi = s.get("min"), s.get("max")
        rng = f"{lo:,.0f}–{hi:,.0f}" if lo and hi else f"{(lo or hi):,.0f}"
        return f"{cur} {rng} {('/' + s['interval']) if s.get('interval') else ''}".strip()
    return None


# 2026-11-14: the same posting arrives as several rows (LinkedIn sends one job under different ids, per city or
# as a repost; other sites repeat it). Once I skip or apply to one, every copy (same company and title) is
# hidden, now and when more copies arrive, so nothing I acted on comes back.
COPY_REASON = "same job as one you already marked"
SAME_JOB_REASON = "same job listed again (another city, site or id)"


_COLLAPSE_SQL = """
    update jobs j set duplicate_of = k.keep_id, duplicate_reason = :reason
    from (select company_norm, title_norm, (array_agg(id order by first_seen_at, id))[1] as keep_id
          from jobs where duplicate_of is null and status = 'discovered' and passed_filters
            and (:all or company_norm = any(:c))
          group by 1, 2 having count(*) > 1) k
    where j.company_norm = k.company_norm and j.title_norm = k.title_norm and j.id <> k.keep_id
      and j.duplicate_of is null and j.status = 'discovered' and j.passed_filters"""


async def collapse_copies(session: AsyncSession, company_norms: list[str] | None = None) -> int:
    """Waiting jobs with the same company and title are one job: keep the first seen, hide the rest
    (LinkedIn lists one posting per city and under several ids; other sites repeat it)."""
    from sqlalchemy import text
    result = await session.execute(text(_COLLAPSE_SQL), {
        "reason": SAME_JOB_REASON, "all": company_norms is None, "c": company_norms or []})
    return result.rowcount or 0


async def hide_copies(session: AsyncSession, job: Job) -> int:
    result = await session.execute(update(Job).where(
        Job.company_norm == job.company_norm, Job.title_norm == job.title_norm, Job.id != job.id,
        Job.status == ApplicationStatus.DISCOVERED, Job.duplicate_of.is_(None),
    ).values(duplicate_of=job.id, duplicate_reason=COPY_REASON))
    return result.rowcount or 0


async def unhide_copies(session: AsyncSession, job: Job) -> int:
    result = await session.execute(update(Job).where(
        Job.duplicate_of == job.id, Job.duplicate_reason == COPY_REASON).values(duplicate_of=None, duplicate_reason=None))
    return result.rowcount or 0


async def set_status(session: AsyncSession, job_id: uuid.UUID, status: ApplicationStatus, *,
                     actor: str, notes: str | None = None) -> Job:
    """Set a job's status. "applied" only ever comes from me (dashboard or my Telegram chat)."""
    if status not in SETTABLE:
        raise ValueError(f"status '{status}' cannot be set manually")
    job = await session.get(Job, job_id, with_for_update=True)
    if job is None:
        raise LookupError("job not found")
    before = job.status.value
    job.status = status
    now = datetime.now(UTC)
    values: dict[str, Any] = {"job_id": job_id, "status": status, "platform": job.source}
    if status == ApplicationStatus.APPLIED:
        from scout_core.settings_store import get_setting
        values["applied_at"] = now
        days = int((await get_setting(session, "notifications")).get("follow_up_days", 7) or 0)
        existing = await session.scalar(select(Application.follow_up_at).where(Application.job_id == job_id))
        if days > 0 and existing is None:
            values["follow_up_at"] = now + timedelta(days=days)
    if notes is not None:
        values["notes"] = notes
    update_values = {k: v for k, v in values.items() if k != "job_id"}
    if status != ApplicationStatus.APPLIED:
        update_values.pop("applied_at", None)
    stmt = insert(Application).values(**values).on_conflict_do_update(
        index_elements=[Application.job_id], set_=update_values)
    await session.execute(stmt)
    if status in TERMINAL_FOR_FEED:
        await hide_copies(session, job)
    elif status == ApplicationStatus.DISCOVERED:  # undo: copies come back, still listed as one job
        await unhide_copies(session, job)
        await collapse_copies(session, [job.company_norm])
    audit(session, actor=actor, action="set_status", entity="job", entity_id=str(job_id),
          before={"status": before}, after={"status": status.value})
    await session.commit()
    return job


async def claim_notification(session: AsyncSession, type_: str, dedupe_key: str,
                             payload: dict | None = None) -> uuid.UUID | None:
    """Reserve a notification before sending it. Returns None if it was already sent/claimed.

    Writing the row first (unique on type+key) is what makes retries unable to double-send.
    A row left 'pending' by a crash before sending is retried on the next tick.
    """
    stmt = insert(Notification).values(type=type_, dedupe_key=dedupe_key, payload=payload,
                                       status="pending").on_conflict_do_nothing().returning(Notification.id)
    new_id = (await session.execute(stmt)).scalar()
    if new_id is not None:
        await session.commit()
        return new_id
    existing = await session.scalar(select(Notification).where(
        Notification.type == type_, Notification.dedupe_key == dedupe_key))
    if existing and existing.status in ("pending", "failed"):
        return existing.id
    return None


async def finish_notification(session: AsyncSession, notification_id: uuid.UUID, *, ok: bool,
                              error: str | None = None, status: str | None = None) -> None:
    n = await session.get(Notification, notification_id)
    if n is None:
        return
    n.status = status or ("sent" if ok else "failed")
    n.sent_at = datetime.now(UTC) if ok else None
    n.error = error
    await session.commit()


async def digest_items(session: AsyncSession, *, since: datetime, limit: int) -> tuple[list[DigestItem], int]:
    """New feed jobs since `since`, newest first, ranked by relevance. Excludes jobs I acted on and jobs
    Telegram already sent me (instant alerts): every job is suggested once (2026-11-14)."""
    base = visible_feed_query().where(Job.first_seen_at >= since, Job.status == ApplicationStatus.DISCOVERED,
                                      Job.notified_at.is_(None))
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    rows = (await session.scalars(base.order_by(
        Job.relevance_score.desc(), func.coalesce(Job.posted_at, Job.first_seen_at).desc()).limit(limit))).all()
    scores = await latest_scores(session, [j.id for j in rows])
    items = [DigestItem(str(j.id), j.title, j.company_name, j.location, salary_text(j), j.posted_at,
                        j.fresher_confidence, j.trust_level, why_line(j, scores.get(j.id)), j.apply_url) for j in rows]
    return items, int(total or 0)


async def today_stats(session: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(UTC)
    day_ago = now - timedelta(hours=24)
    feed = visible_feed_query().subquery()
    new_24h = await session.scalar(select(func.count()).select_from(feed).where(feed.c.first_seen_at >= day_ago))
    applied_24h = await session.scalar(select(func.count()).select_from(Application).where(
        and_(Application.status == ApplicationStatus.APPLIED, Application.applied_at >= day_ago)))
    follow_ups = await session.scalar(select(func.count()).select_from(Application).where(
        Application.follow_up_at.is_not(None), Application.follow_up_at <= now + timedelta(days=1)))
    total_feed = await session.scalar(select(func.count()).select_from(feed).where(
        feed.c.status == ApplicationStatus.DISCOVERED.value))
    return {"new_24h": int(new_24h or 0), "applied_24h": int(applied_24h or 0),
            "follow_ups_due": int(follow_ups or 0), "open_in_feed": int(total_feed or 0)}


# ------------------------------------------------------------------ eval labels (spec §11 Phase 3)
LABELS = ("apply", "maybe", "no")
LABEL_TARGET = 40  # spec: 30-50 labelled jobs before trusting the match score
_KEEP = object()


async def label_summary(session: AsyncSession) -> dict:
    counts = dict((await session.execute(select(JobLabel.label, func.count()).group_by(JobLabel.label))).all())
    total = sum(counts.values())
    return {"apply": counts.get("apply", 0), "maybe": counts.get("maybe", 0), "no": counts.get("no", 0),
            "total": total, "target": LABEL_TARGET}


async def set_label(session: AsyncSession, job_id: uuid.UUID, label: str | None, *, actor: str,
                    note: Any = _KEEP) -> dict:
    """My apply / maybe / no judgement of a job (None clears it). Leaving `note` out keeps the saved note.
    Returns the label counts. Raises LookupError for an unknown job, ValueError for an unknown label."""
    if label is not None and label not in LABELS:
        raise ValueError(f"label must be one of {', '.join(LABELS)}")
    if await session.get(Job, job_id) is None:
        raise LookupError("job not found")
    existing = await session.scalar(select(JobLabel).where(JobLabel.job_id == job_id))
    if label is None:
        if existing:
            await session.delete(existing)
    elif existing:
        existing.label = label
        if note is not _KEEP:
            existing.note = note
    else:
        session.add(JobLabel(job_id=job_id, label=label, note=None if note is _KEEP else note))
    audit(session, actor=actor, action="label_job", entity="job", entity_id=str(job_id), after={"label": label})
    await session.commit()
    return await label_summary(session)

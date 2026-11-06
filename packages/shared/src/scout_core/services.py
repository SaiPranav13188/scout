"""Use-case services shared by the API, the workers and the Telegram webhook."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from scout_core.db.models import Application, Job, Notification
from scout_core.notify.telegram import DigestItem
from scout_core.runs import audit
from scout_core.types import ApplicationStatus

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


def visible_feed_query() -> Select:
    """What the Today page shows by default: feed jobs minus scam risk and off-target roles."""
    return feed_query().where(Job.trust_level != "scam_risk", Job.role_family != "unrelated",
                              Job.relevance_score >= MIN_FEED_RELEVANCE)


def why_line(job: Job) -> str:
    parts = []
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
        values["applied_at"] = now
    if notes is not None:
        values["notes"] = notes
    update_values = {k: v for k, v in values.items() if k != "job_id"}
    if status != ApplicationStatus.APPLIED:
        update_values.pop("applied_at", None)
    stmt = insert(Application).values(**values).on_conflict_do_update(
        index_elements=[Application.job_id], set_=update_values)
    await session.execute(stmt)
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
    """New feed jobs since `since`, newest first, ranked by relevance. Excludes jobs I acted on."""
    base = visible_feed_query().where(Job.first_seen_at >= since, Job.status == ApplicationStatus.DISCOVERED)
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    rows = (await session.scalars(base.order_by(
        Job.relevance_score.desc(), func.coalesce(Job.posted_at, Job.first_seen_at).desc()).limit(limit))).all()
    items = [DigestItem(str(j.id), j.title, j.company_name, j.location, salary_text(j), j.posted_at,
                        j.fresher_confidence, j.trust_level, why_line(j), j.apply_url) for j in rows]
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

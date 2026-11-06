"""Async implementations of every scheduled job. Celery tasks are thin wrappers around these."""

import asyncio
import html
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from scout_core.config import get_settings
from scout_core.db.models import AgentRun, Company, CompanyWatchlist, Job
from scout_core.db.session import worker_session
from scout_core.discovery import YC_DATASET_URL, RobotsCache, discover_boards, yc_seeds
from scout_core.http import PoliteClient
from scout_core.normalize import registered_domain
from scout_core.notify.telegram import DigestItem, TelegramClient, digest_header, format_job
from scout_core.pipeline.ingest import get_or_create_company, ingest_board, store_jobs
from scout_core.rawstore import make_raw_store
from scout_core.runs import track_run
from scout_core.schedule import due_tasks, in_quiet_hours
from scout_core.services import (
    claim_notification,
    digest_items,
    feed_query,
    finish_notification,
    salary_text,
    today_stats,
    visible_feed_query,
    why_line,
)
from scout_core.settings_store import get_setting, load_rules, set_setting
from scout_core.sources.base import BoardNotFound, SourceError
from scout_core.trust import domain_registration_date
from scout_core.types import ApplicationStatus, AtsType
from scout_core.verify import verify_url

log = structlog.get_logger()
BOARD_CONCURRENCY = 6


# ------------------------------------------------------------------ scheduler

async def slots_to_run(session: AsyncSession, now: datetime) -> list[tuple[str, str]]:
    schedule = await get_setting(session, "schedule")
    paused = bool(await get_setting(session, "paused"))
    due = due_tasks(now, schedule, paused=paused)
    if not due:
        return []
    keys = [d.slot_key for d in due]
    stale = now - timedelta(hours=2)
    done = set((await session.scalars(select(AgentRun.task_key).where(
        AgentRun.task_key.in_(keys),
        # success/partial = done; a recent 'running' row means it is in progress.
        (AgentRun.status.in_(["success", "partial"])) |
        ((AgentRun.status == "running") & (AgentRun.started_at > stale)),
    ))).all())
    failed_counts: dict[str, int] = {}
    for key in (await session.scalars(select(AgentRun.task_key).where(
            AgentRun.task_key.in_(keys), AgentRun.status == "error"))).all():
        failed_counts[key] = failed_counts.get(key, 0) + 1
    # A slot that failed 3 times waits for the next slot (and the failures alert me).
    return [(d.task, d.slot_key) for d in due if d.slot_key not in done and failed_counts.get(d.slot_key, 0) < 3]


# ------------------------------------------------------------------ ingestion

async def run_ingest(slot_key: str | None = None) -> dict:
    async with worker_session() as session, track_run(session, "ingest", slot_key) as run:
        sources_on = await get_setting(session, "sources")
        enabled = [AtsType(s) for s in ("greenhouse", "lever", "ashby") if sources_on.get(s)]
        boards = (await session.execute(
            select(CompanyWatchlist.id).where(CompanyWatchlist.active.is_(True),
                                              CompanyWatchlist.ats_type.in_(enabled)))).scalars().all()
        run.inc("boards", len(boards))
        raw_store = make_raw_store()
        sem = asyncio.Semaphore(BOARD_CONCURRENCY)
        async with PoliteClient() as client:

            async def one(watch_id):
                async with sem, worker_session() as s:
                    watch = await s.get(CompanyWatchlist, watch_id)
                    company = await s.get(Company, watch.company_id)
                    fr, rr, prefs = await load_rules(s)
                    try:
                        stats = await ingest_board(s, client, raw_store, watch, company,
                                                   fresher_rules=fr, relevance_rules=rr, prefs=prefs)
                    except BoardNotFound as exc:
                        run.error(f"{watch.ats_type.value}/{watch.board_token}: {exc}")
                        return
                    except (SourceError, httpx.HTTPError) as exc:
                        await s.rollback()
                        watch = await s.get(CompanyWatchlist, watch_id)
                        watch.consecutive_failures += 1
                        watch.last_error = str(exc)[:500]
                        await s.commit()
                        run.error(f"{watch.ats_type.value}/{watch.board_token}: {exc}")
                        return
                    for k, v in stats.as_counts().items():
                        run.inc(k, v)

            await asyncio.gather(*(one(b) for b in boards))
        await raw_store.close()
        return run.counts


# ------------------------------------------------------------------ verification

async def run_verify(slot_key: str | None = None, limit: int = 150) -> dict:
    """Check apply links of non-ATS jobs (ATS jobs are verified by being on the live board)."""
    async with worker_session() as session, track_run(session, "verify", slot_key) as run:
        cutoff = datetime.now(UTC) - timedelta(hours=24)
        jobs = (await session.scalars(feed_query().where(
            Job.source.not_in(["greenhouse", "lever", "ashby"]),
            (Job.verified_at.is_(None)) | (Job.verified_at < cutoff),
        ).order_by(Job.first_seen_at.desc()).limit(limit))).all()
        async with PoliteClient(max_retries=1) as client:
            for job in jobs:
                result = await verify_url(client, job.apply_url)
                job.verify_status = result.status.value
                job.verify_reason = result.reason
                if result.verified:
                    job.verified_at = result.checked_at
                    job.is_open = result.is_open
                    if result.is_open is False:
                        job.closed_at = result.checked_at
                run.inc(f"link_{result.status.value}")
            await session.commit()
        return run.counts


# ------------------------------------------------------------------ discovery

async def run_discovery(slot_key: str | None = None) -> dict:
    async with worker_session() as session, track_run(session, "discovery", slot_key) as run:
        cfg = await get_setting(session, "discovery")
        if not cfg.get("yc_enabled"):
            return run.counts
        async with PoliteClient(min_interval_per_host=1.0) as client:
            r = await client.get(YC_DATASET_URL)
            if r.status_code != 200:
                raise SourceError(f"YC dataset returned HTTP {r.status_code}")
            seeds = [s for s in yc_seeds(r.json()) if s.website]
            run.inc("yc_hiring_companies", len(seeds))

            # Skip companies scanned in the last 30 days; new companies first.
            recent = datetime.now(UTC) - timedelta(days=30)
            scanned = set((await session.scalars(select(Company.normalized_domain).where(
                Company.last_scanned_at > recent))).all())
            todo = [s for s in seeds if registered_domain(s.website) not in scanned]
            todo = todo[: int(cfg.get("max_companies_per_run", 800))]
            run.inc("scanned", len(todo))

            robots = RobotsCache(client, get_settings().http_user_agent)
            sem = asyncio.Semaphore(8)

            async def one(seed):
                async with sem:
                    try:
                        boards = await discover_boards(client, robots, seed.website, seed.name)
                        created = await domain_registration_date(client.raw, registered_domain(seed.website) or "")
                    except Exception as exc:  # noqa: BLE001 - one bad site must not stop the run
                        run.error(f"{seed.name}: {type(exc).__name__}")
                        return
                async with worker_session() as s:
                    company = await get_or_create_company(
                        s, name=seed.name, website=seed.website, source=seed.source,
                        description=seed.description, hq=seed.hq, size=seed.size,
                        funding_stage=seed.funding_stage, funding_info=seed.funding_info)
                    company.in_yc_directory = True
                    company.last_scanned_at = datetime.now(UTC)
                    if created:
                        company.domain_created_at = created
                    for b in boards:
                        exists = await s.scalar(select(CompanyWatchlist).where(
                            CompanyWatchlist.ats_type == AtsType(b.ats), CompanyWatchlist.board_token == b.token))
                        if exists is None:
                            s.add(CompanyWatchlist(company_id=company.id, ats_type=AtsType(b.ats),
                                                   board_token=b.token, discovery_method="website_link",
                                                   evidence_url=b.evidence_url))
                            run.inc("boards_added")
                        if company.career_url is None:
                            company.career_url = b.evidence_url
                    await s.commit()

            await asyncio.gather(*(one(s) for s in todo))
        return run.counts


# ------------------------------------------------------------------ notifications

async def run_daily_digest(slot_key: str) -> dict:
    async with worker_session() as session, track_run(session, "daily_digest", slot_key) as run:
        tg = TelegramClient.from_settings()
        if tg is None:
            run.error("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
            return run.counts
        notif_id = await claim_notification(session, "daily_digest", slot_key)
        if notif_id is None:
            run.inc("already_sent")
            return run.counts
        cfg = await get_setting(session, "notifications")
        since = datetime.now(UTC) - timedelta(hours=24)
        items, total = await digest_items(session, since=since, limit=int(cfg.get("digest_size", 8)))
        chat = get_settings().telegram_chat_id
        try:
            await tg.send(chat, digest_header(len(items), total))
            for item in items:
                text, buttons = format_job(item)
                await tg.send(chat, text, buttons)
                job = await session.get(Job, uuid.UUID(item.job_id))
                if job is not None:
                    job.notified_at = datetime.now(UTC)
            await finish_notification(session, notif_id, ok=True)
        except Exception as exc:
            await finish_notification(session, notif_id, ok=False, error=str(exc)[:500])
            raise
        finally:
            await tg.aclose()
        run.inc("jobs_sent", len(items))
        return run.counts


async def run_evening_summary(slot_key: str) -> dict:
    async with worker_session() as session, track_run(session, "evening_summary", slot_key) as run:
        tg = TelegramClient.from_settings()
        if tg is None:
            run.error("Telegram not configured")
            return run.counts
        notif_id = await claim_notification(session, "evening_summary", slot_key)
        if notif_id is None:
            return run.counts
        stats = await today_stats(session)
        errors = await session.scalar(select(AgentRun).where(
            AgentRun.status == "error", AgentRun.started_at > datetime.now(UTC) - timedelta(hours=24)).limit(1))
        text = (f"🌙 <b>Evening summary</b>\n"
                f"New jobs (24h): {stats['new_24h']}\n"
                f"Marked applied (24h): {stats['applied_24h']}\n"
                f"Follow-ups due: {stats['follow_ups_due']}\n"
                f"Waiting in your feed: {stats['open_in_feed']}"
                + ("\n⚠️ Some worker runs failed today. See Agent health." if errors else ""))
        try:
            await tg.send(get_settings().telegram_chat_id, text)
            await finish_notification(session, notif_id, ok=True)
        except Exception as exc:
            await finish_notification(session, notif_id, ok=False, error=str(exc)[:500])
            raise
        finally:
            await tg.aclose()
        return run.counts


async def alert_failure(worker: str, error: str) -> None:
    """Every failure gets a Telegram alert (spec §12), at most one per worker per hour."""
    tg = TelegramClient.from_settings()
    if tg is None:
        return
    hour = datetime.now(UTC).strftime("%Y-%m-%dT%H")
    async with worker_session() as session:
        notif_id = await claim_notification(session, "error_alert", f"{worker}:{hour}", {"error": error[:500]})
        if notif_id is None:
            return
        try:
            await tg.send(get_settings().telegram_chat_id,
                          f"🛑 <b>{html.escape(worker)}</b> failed\n<code>{html.escape(error[:600])}</code>")
            await finish_notification(session, notif_id, ok=True)
        except Exception as exc:  # noqa: BLE001 - alerting must never raise
            await finish_notification(session, notif_id, ok=False, error=str(exc)[:300])
        finally:
            await tg.aclose()


# ------------------------------------------------------------------ instant alerts

INSTANT_LOOKBACK = timedelta(hours=2)


async def run_instant_alerts(now: datetime | None = None) -> dict:
    """Telegram message for new relevant jobs not yet sent (grouped). Runs after every check.

    Jobs are marked notified *before* sending (so a retry never repeats them) and unmarked if the
    send fails (so nothing is lost). During quiet hours nothing is sent; jobs found then reach me
    in the morning digest.
    """
    now = now or datetime.now(UTC)
    async with worker_session() as session:
        if await get_setting(session, "paused"):
            return {"skipped": "paused"}
        cfg = await get_setting(session, "notifications")
        schedule = await get_setting(session, "schedule")
        if not cfg.get("instant_alerts_enabled", True):
            return {"skipped": "disabled"}
        if in_quiet_hours(now, cfg["quiet_hours"], schedule.get("timezone", "Asia/Kolkata")):
            return {"skipped": "quiet_hours"}
        tg = TelegramClient.from_settings()
        if tg is None:
            return {"skipped": "telegram_not_configured"}

        pending = visible_feed_query().where(
            Job.status == ApplicationStatus.DISCOVERED, Job.notified_at.is_(None),
            Job.first_seen_at >= now - INSTANT_LOOKBACK).with_only_columns(Job.id)
        claimed = (await session.execute(
            update(Job).where(Job.id.in_(pending)).values(notified_at=now).returning(Job.id))).scalars().all()
        await session.commit()
        if not claimed:
            await tg.aclose()
            return {"sent": 0}

        async with track_run(session, "instant_alerts", f"instant:{now.isoformat(timespec='minutes')}") as run:
            jobs = (await session.scalars(select(Job).where(Job.id.in_(claimed)).order_by(
                Job.relevance_score.desc(), func.coalesce(Job.posted_at, Job.first_seen_at).desc()))).all()
            limit = int(cfg.get("instant_alert_max_jobs", 8))
            chat = get_settings().telegram_chat_id
            try:
                n = len(jobs)
                await tg.send(chat, f"🔔 <b>{n} new fresher job{'s' if n != 1 else ''}</b> just found")
                for job in jobs[:limit]:
                    text, buttons = format_job(DigestItem(
                        str(job.id), job.title, job.company_name, job.location, salary_text(job),
                        job.posted_at, job.fresher_confidence, job.trust_level, why_line(job), job.apply_url))
                    await tg.send(chat, text, buttons)
                    await asyncio.sleep(0.4)  # stay well under Telegram's per-chat rate limit
                if n > limit:
                    await tg.send(chat, f"…and {n - limit} more in your dashboard.")
            except Exception:
                await session.rollback()
                await session.execute(update(Job).where(Job.id.in_(claimed)).values(notified_at=None))
                await session.commit()
                raise
            finally:
                await tg.aclose()
            run.inc("jobs_sent", len(jobs))
            return run.counts


# ------------------------------------------------------------------ one scheduler pass (cloud cron)

RUNNERS = {
    "ingest": run_ingest,
    "verify": run_verify,
    "discovery": run_discovery,
    "daily_digest": run_daily_digest,
    "evening_summary": run_evening_summary,
}


async def run_tick(*, only: set[str] | None = None, skip: set[str] | None = None) -> tuple[dict, bool]:
    """Run every due slot once, then instant alerts. Returns (results, any_failed)."""
    async with worker_session() as session:
        slots = await slots_to_run(session, datetime.now(UTC))
    results: dict = {}
    failed = False
    for task, slot_key in slots:
        if (only and task not in only) or (skip and task in skip):
            continue
        try:
            results[slot_key] = await RUNNERS[task](slot_key)
        except Exception as exc:  # noqa: BLE001 - recorded in agent_runs, alerted, and reported
            failed = True
            results[slot_key] = f"error: {type(exc).__name__}: {exc}"[:500]
            await alert_failure(task, f"{type(exc).__name__}: {exc}")
    if not only or "instant_alerts" in only:
        try:
            results["instant_alerts"] = await run_instant_alerts()
        except Exception as exc:  # noqa: BLE001
            failed = True
            results["instant_alerts"] = f"error: {type(exc).__name__}: {exc}"[:500]
            await alert_failure("instant_alerts", f"{type(exc).__name__}: {exc}")
    return results, failed


# ------------------------------------------------------------------ Gmail job alerts

GMAIL_OVERLAP_S = 3600  # re-scan the last hour each run; job ids make reprocessing harmless


async def run_gmail(slot_key: str | None = None) -> dict:
    """Read new job-alert emails (read-only) and store their jobs like any other source."""
    from scout_core.gmail import GmailClient, GmailNotConnected
    from scout_core.secrets import SecretError
    from scout_core.sources.email_alerts import ALERT_QUERY, parse_alert

    async with worker_session() as session:
        if not (await get_setting(session, "sources")).get("gmail_alerts", True):
            return {"skipped": "disabled"}
        try:
            gm = await GmailClient.from_db(session)
        except GmailNotConnected:
            return {"skipped": "gmail_not_connected"}
        except SecretError:
            # ENCRYPTION_KEY missing in this environment: shown in the run output, not alerted every 10 min.
            return {"skipped": "encryption_key_missing"}
        # GmailAuthExpired propagates: the run is recorded as an error and I get a Telegram alert.

    async with worker_session() as session, track_run(session, "gmail", slot_key) as run:
        state = await get_setting(session, "gmail_state")
        last_ms = int(state.get("last_internal_ms") or 0)
        since = f" after:{max(0, last_ms // 1000 - GMAIL_OVERLAP_S)}" if last_ms else " newer_than:3d"
        fr, rr, prefs = await load_rules(session)
        raw_store = make_raw_store()
        newest = last_ms
        try:
            ids = await gm.search(ALERT_QUERY + since, max_results=200)
            run.inc("emails_seen", len(ids))
            for mid in ids:
                msg = await gm.get(mid)
                newest = max(newest, int(msg.received_at.timestamp() * 1000))
                try:
                    parser, jobs = parse_alert(msg)
                except Exception as exc:  # noqa: BLE001 - one odd email must not stop the run
                    run.error(f"parse failed for {msg.sender} / {msg.subject[:60]}: {type(exc).__name__}")
                    await raw_store.save_alert_email({"gmail_message_id": msg.id, "received_at": msg.received_at,
                                                      "sender": msg.sender, "parsed_items": 0,
                                                      "parse_errors": [str(exc)[:300]]})
                    continue
                if parser is None or not jobs:
                    run.inc("emails_without_jobs")
                    continue
                run.inc("alert_emails")
                by_company: dict[str, list] = {}
                for job in jobs:
                    by_company.setdefault(job.company_name, []).append(job)
                for company_name, company_jobs in by_company.items():
                    company = await get_or_create_company(session, name=company_name, website=None,
                                                          source=company_jobs[0].source)
                    stats = await store_jobs(session, company_jobs, company=company, fresher_rules=fr,
                                             relevance_rules=rr, prefs=prefs)
                    for k, v in stats.as_counts().items():
                        run.inc(k, v)
                await session.commit()
                await raw_store.save_alert_email({"gmail_message_id": msg.id, "received_at": msg.received_at,
                                                  "sender": msg.sender, "parser": parser,
                                                  "parsed_items": len(jobs), "parse_errors": []})
        finally:
            await gm.aclose()
            await raw_store.close()
        if newest > last_ms:
            await set_setting(session, "gmail_state", {"last_internal_ms": newest})
            await session.commit()
        return run.counts


RUNNERS["gmail"] = run_gmail

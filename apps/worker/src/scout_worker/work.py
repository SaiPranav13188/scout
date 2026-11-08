"""Async implementations of every scheduled job. Celery tasks are thin wrappers around these."""

import asyncio
import html
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import structlog
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from scout_core.config import get_settings
from scout_core.db.models import AgentRun, Application, Company, CompanyWatchlist, Job
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
    latest_scores,
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
        due = (await session.execute(select(Job.title, Job.company_name, Application.applied_at, Application.status)
                                     .join(Application, Application.job_id == Job.id).where(
            Application.follow_up_at.is_not(None), Application.follow_up_at <= datetime.now(UTC) + timedelta(days=1),
            Application.status.in_([ApplicationStatus.APPLIED, ApplicationStatus.INTERVIEW]))
            .order_by(Application.follow_up_at).limit(8))).all()
        errors = await session.scalar(select(AgentRun).where(
            AgentRun.status == "error", AgentRun.started_at > datetime.now(UTC) - timedelta(hours=24)).limit(1))
        text = (f"🌙 <b>Evening summary</b>\n"
                f"New jobs (24h): {stats['new_24h']}\n"
                f"Marked applied (24h): {stats['applied_24h']}\n"
                f"Follow-ups due: {stats['follow_ups_due']}\n"
                f"Waiting in your feed: {stats['open_in_feed']}"
                + "".join(f"\n  • {html.escape(t)} at {html.escape(c)}"
                          + (f" (applied {(datetime.now(UTC) - a).days} days ago)" if a else "") for t, c, a, _ in due)
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

# Long enough that jobs found overnight (quiet hours) or confirmed later (experience check) still reach
# me; each job is sent once (notified_at), so a long window never repeats anything.
INSTANT_LOOKBACK = timedelta(hours=24)


async def run_instant_alerts(now: datetime | None = None) -> dict:
    """Telegram message for new relevant jobs not yet sent (grouped). Runs after every check.

    Jobs are marked notified *before* sending (so a retry never repeats them) and unmarked if the
    send fails (so nothing is lost). During quiet hours nothing is sent; jobs found then are sent on the
    first check after quiet hours end.
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
            scores = await latest_scores(session, [j.id for j in jobs])
            chat = get_settings().telegram_chat_id
            try:
                n = len(jobs)
                await tg.send(chat, f"🔔 <b>{n} new fresher job{'s' if n != 1 else ''}</b> just found")
                for job in jobs[:limit]:
                    text, buttons = format_job(DigestItem(
                        str(job.id), job.title, job.company_name, job.location, salary_text(job),
                        job.posted_at, job.fresher_confidence, job.trust_level, why_line(job, scores.get(job.id)),
                        job.apply_url))
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


# ------------------------------------------------------------------ resume + match scoring (Phase 3)

async def save_master_resume(path: str) -> dict:
    """Parse a resume file into a new master_resume version (older versions are kept, never edited)."""
    from pathlib import Path

    from scout_core.llm import Gemini
    from scout_core.resume import extract_text, parse_resume_text
    from scout_core.resume_store import add_version

    llm = Gemini.from_settings()
    parsed = await parse_resume_text(llm, extract_text(Path(path)))
    async with worker_session() as session:
        row = await add_version(session, file_url=f"local:{Path(path).name}", parsed=parsed)
        await session.commit()
        return {"version": row.version, "fact_check": parsed["fact_check"], "usage": llm.usage.by_call}


async def _with_db_retry(fn, attempts: int = 3):
    """Run fn(session) in a fresh session, retrying when the database connection drops."""
    from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

    for attempt in range(attempts):
        try:
            async with worker_session() as s:
                return await fn(s)
        except (DBAPIError, InterfaceError, OperationalError, ConnectionError) as exc:
            if attempt == attempts - 1:
                raise
            log.info("db.retry", attempt=attempt + 1, error=str(exc)[:120])
            await asyncio.sleep(2 * (attempt + 1))


async def check_unclear_experience(llm, limit: int = 15) -> dict:
    """Ask Gemini about feed jobs whose experience level the rules couldn't decide (see seniority.py).

    Only jobs with a real description, not checked before, not corrected by me. Each verdict is
    saved on its own so a quota error part-way keeps the work already done.
    """
    from sqlalchemy import Text, cast

    from scout_core.llm import LLMError
    from scout_core.seniority import MARK, read_seniority

    async def load(s):
        return (await s.execute(visible_feed_query(include_unclear=True).where(
            Job.status == ApplicationStatus.DISCOVERED, Job.experience_unclear.is_(True),
            Job.experience_corrected_by_me.is_not(True), func.length(Job.description) > 300,
            ~cast(Job.fresher_reasons, Text).contains(MARK))
            .order_by(Job.relevance_score.desc(), Job.first_seen_at.desc()).limit(limit)
            .with_only_columns(Job.id, Job.title, Job.description))).all()

    counts = {"checked": 0, "fresher_ok": 0, "needs_experience": 0, "unclear": 0, "errors": 0}
    for job_id, title, description in await _with_db_retry(load):
        try:
            verdict, reason = await read_seniority(llm, title=title, description=description)
        except LLMError as exc:
            counts["errors"] += 1
            if "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc):
                break
            continue

        async def save(s, job_id=job_id, verdict=verdict, reason=reason):
            job = await s.get(Job, job_id)
            job.fresher_reasons = [reason, *(job.fresher_reasons or [])]
            if verdict == "fresher_ok":
                job.experience_unclear, job.fresher_confidence = False, "medium"
            elif verdict == "needs_experience":
                job.passed_filters = False
                job.filter_reasons = ["experience: " + reason, *(job.filter_reasons or [])]
            await s.commit()

        await _with_db_retry(save)
        counts["checked"] += 1
        counts[verdict] += 1
    return counts


async def resumes_for_top_matches(llm, limit: int = 3) -> dict:
    """Spec §6.5: a tailored resume is ready for every "Top match" (85+) in my feed."""
    from scout_core.db.models import JobScore, ResumeGenerated
    from scout_core.llm import LLMError
    from scout_core.matching import PROMPT_VERSION
    from scout_core.settings_store import get_setting as _gs
    from scout_core.tailor_service import NoMasterResume, generate

    async def load(s):
        top = int(((await _gs(s, "matching")).get("bands") or {}).get("top", 85))
        latest = (select(JobScore.job_id, JobScore.score, JobScore.breakdown).where(JobScore.prompt_version == PROMPT_VERSION)
                  .ext(distinct_on(JobScore.job_id)).order_by(JobScore.job_id, JobScore.created_at.desc()).subquery())
        has_resume = select(ResumeGenerated.job_id)
        return (await s.scalars(visible_feed_query().join(latest, latest.c.job_id == Job.id).where(
            Job.status == ApplicationStatus.DISCOVERED, latest.c.score >= top, Job.id.not_in(has_resume))
            .order_by(latest.c.score.desc()).limit(limit).with_only_columns(Job.id))).all()

    counts = {"generated": 0, "fallback": 0, "errors": 0}
    for job_id in await _with_db_retry(load):
        try:
            async with worker_session() as s:
                row = await generate(s, llm, await s.get(Job, job_id), actor="worker")
                await s.commit()
            counts["generated" if (row.content or {}).get("tailored") else "fallback"] += 1
        except NoMasterResume:
            break
        except LLMError as exc:
            counts["errors"] += 1
            if "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc):
                break
    return counts


async def run_scoring(slot_key: str | None = None, limit: int = 25) -> dict:
    """Score feed jobs against the active master resume: unscored jobs first, then jobs whose
    score was computed from an older resume or older target roles (different profile fingerprint).

    No database connection is held while waiting for Gemini: reads happen in one short session,
    and each score is saved in its own fresh session (with retries if the connection drops).
    """
    from scout_core.db.models import JobScore
    from scout_core.llm import Gemini, LLMError, LLMNotConfigured
    from scout_core.matching import PROMPT_VERSION, Weights, profile_text, score_job
    from scout_core.resume import profile_fingerprint
    from scout_core.resume_store import active_resume, resume_data

    async def load(session):
        master = await active_resume(session)
        if master is None:
            return None
        cfg = await get_setting(session, "matching")
        resume = resume_data(master.parsed or {})
        fingerprint = profile_fingerprint(profile_text(resume, cfg.get("target_roles", [])), PROMPT_VERSION)
        current = select(JobScore.job_id).where(JobScore.prompt_version == PROMPT_VERSION,
                                                JobScore.profile_hash == fingerprint)
        has_any = select(JobScore.job_id).where(JobScore.prompt_version == PROMPT_VERSION)
        jobs = (await session.execute(visible_feed_query(include_unclear=True).where(
            Job.status == ApplicationStatus.DISCOVERED, Job.id.not_in(current))
            .order_by(Job.id.in_(has_any), Job.experience_unclear, Job.first_seen_at.desc()).limit(limit)
            .with_only_columns(Job.id, Job.title, Job.company_name, Job.location, Job.description))).all()
        return resume, fingerprint, cfg, [tuple(j) for j in jobs]

    if slot_key and not slot_key.startswith("manual:"):
        async def per_run(s):
            return int((await get_setting(s, "matching")).get("jobs_per_run", limit))
        limit = await _with_db_retry(per_run)
    loaded = await _with_db_retry(load)
    if loaded is None:
        return {"skipped": "no_master_resume"}
    resume, fingerprint, cfg, jobs = loaded
    try:
        llm = Gemini.from_settings()
    except LLMNotConfigured:
        return {"skipped": "gemini_not_configured"}

    async with worker_session() as session, track_run(session, "scoring", slot_key) as run:
        # First settle "experience unclear" jobs, so alerts only ever carry checked jobs.
        for k, v in (await check_unclear_experience(llm)).items():
            run.inc(f"seniority_{k}", v)
        resume, fingerprint, cfg, jobs = await _with_db_retry(load)  # without jobs the check just dropped
        weights = Weights(**cfg.get("weights", {}))
        targets = cfg.get("target_roles", [])
        run.inc("to_score", len(jobs))
        if jobs:
            resume_vec = (await llm.embed([profile_text(resume, targets)], purpose="embed_profile"))[0]
        for job_id, title, company, location, description in jobs:
            try:
                r = await score_job(llm, resume=resume, resume_vec=resume_vec, targets=targets, title=title,
                                    company=company, location=location, description=description, weights=weights)
            except LLMError as exc:
                run.error(f"{title[:40]}: {exc}"[:300])
                if "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc):
                    break  # out of free quota for now; the next run continues
                continue

            async def save(s, job_id=job_id, r=r):
                s.add(JobScore(job_id=job_id, score=r.score, breakdown=r.breakdown, reasons=r.reasons,
                               matched_skills=r.matched_skills, missing_skills=r.missing_skills,
                               red_flags=r.red_flags, model=llm.usage.by_call[-1]["model"],
                               prompt_version=PROMPT_VERSION, profile_hash=fingerprint))
                await s.execute(update(Job).where(Job.id == job_id).values(
                    required_skills=r.assessment.required_skills, preferred_skills=r.assessment.preferred_skills))
                await s.commit()

            await _with_db_retry(save)
            run.inc("scored")
        for k, v in (await resumes_for_top_matches(llm)).items():
            run.inc(f"resumes_{k}", v)
        run.inc("llm_calls", llm.usage.calls)
        run.inc("llm_input_tokens", llm.usage.input_tokens)
        run.inc("llm_output_tokens", llm.usage.output_tokens)
        return run.counts


RUNNERS["scoring"] = run_scoring


INVENTED_LINKEDIN_TEXT = "Entry level (from a LinkedIn alert filtered to entry level / internship)."


async def recheck_feed(apply: bool = False) -> dict:
    """Re-run the fresher filter (current rules) on jobs still waiting in the feed.

    Filter rules only reach new jobs at ingest; this applies a stricter rule to jobs already
    stored. Jobs I corrected by hand are left alone. Dry run unless apply=True.
    """
    from scout_core.filters.fresher import Decision, classify
    from scout_core.seniority import MARK
    from scout_core.settings_store import load_rules

    async with worker_session() as session:
        fresher_rules, _, _ = await load_rules(session)
        jobs = (await session.scalars(select(Job).where(
            Job.passed_filters.is_(True), Job.status == ApplicationStatus.DISCOVERED,
            Job.experience_corrected_by_me.is_not(True)))).all()
        dropped: list[str] = []
        cleared = 0
        for job in jobs:
            if job.description == INVENTED_LINKEDIN_TEXT:
                # Written by the LinkedIn alert parser until 2026-11-08 instead of a real description.
                cleared += 1
                if apply:
                    job.description = None
            desc = None if job.description == INVENTED_LINKEDIN_TEXT else job.description
            v = classify(job.title, desc, fresher_rules, job.employment_type)
            if v.decision == Decision.DROP:
                dropped.append(f"{job.title} @ {job.company_name}: {v.reasons[0]}"[:160])
                if apply:
                    job.passed_filters = False
                    job.filter_reasons = ["experience: " + "; ".join(v.reasons), *(job.filter_reasons or [])]
            elif any(str(r).startswith(MARK) for r in (job.fresher_reasons or [])):
                continue  # Gemini already read this posting and quoted its evidence: keep that verdict
            elif apply:
                job.experience_min, job.experience_max = v.experience_min, v.experience_max
                job.experience_unclear, job.fresher_confidence = v.experience_unclear, v.confidence.value
                job.fresher_reasons = v.reasons
        if apply:
            await session.commit()
        unclear = sum(1 for j in jobs if j.passed_filters and j.experience_unclear) if apply else None
        return {"checked": len(jobs), "invented_text_removed": cleared, "dropped": len(dropped),
                "now_experience_unclear": unclear, "examples": dropped[:15], "applied": apply}


async def resume_backfill(pdf_path: str, apply: bool = False) -> dict:
    """One-time upgrade for resume versions saved before 2026-11-07 (safe to run again).

    - Stores the redacted text of the PDF on the active version if it is missing (needed by the
      fact check); only if the PDF's emails match the stored ones, i.e. it is the same resume.
    - Stamps scores that have no profile fingerprint with the active resume's fingerprint, so
      they aren't all re-scored (they were computed from it). Dry run unless apply=True.
    """
    from pathlib import Path

    from scout_core.db.models import JobScore
    from scout_core.matching import PROMPT_VERSION, profile_text
    from scout_core.resume import extract_text, fact_check, profile_fingerprint, split_contact
    from scout_core.resume_store import active_resume, resume_data

    async with worker_session() as session:
        row = await active_resume(session)
        if row is None:
            return {"skipped": "no active resume"}
        parsed = dict(row.parsed or {})
        out: dict = {"version": row.version}
        if not parsed.get("source_text"):
            contact, redacted = split_contact(extract_text(Path(pdf_path)))
            if sorted(contact.emails) != sorted((parsed.get("contact") or {}).get("emails", [])):
                raise ValueError("this PDF's email doesn't match the active resume version; not the same file?")
            check = fact_check(resume_data(parsed), redacted)
            out["source_text"] = f"{len(redacted)} characters; fact check {'ok' if check['ok'] else check['unsupported']}"
            if apply:
                row.parsed = {**parsed, "source_text": redacted, "fact_check": check}
        else:
            out["source_text"] = "already stored"
        targets = (await get_setting(session, "matching")).get("target_roles", [])
        fp = profile_fingerprint(profile_text(resume_data(parsed), targets), PROMPT_VERSION)
        legacy = JobScore.profile_hash.is_(None)
        out["scores_to_stamp"] = await session.scalar(select(func.count()).select_from(JobScore).where(legacy))
        if apply:
            await session.execute(update(JobScore).where(legacy).values(profile_hash=fp))
            await session.commit()
        out["applied"] = apply
        return out


async def tailor_eval(n: int = 6) -> dict:
    """Spec §13.5-6 on real data: tailor my real resume for n real AI jobs (nothing is stored).

    Reports how often Gemini's first draft was rejected by the validator, fallbacks, unsupported
    claims in the final output (must be 0), page count and text extraction.
    """
    from scout_core.llm import Gemini
    from scout_core.render_resume import ats_text_check, fit_one_page
    from scout_core.resume import Contact
    from scout_core.resume_store import active_resume, resume_data
    from scout_core.tailor import tailor, validate

    async with worker_session() as s:
        master_row = await active_resume(s)
        jobs = (await s.scalars(select(Job).where(Job.role_family == "applied_ai", func.length(Job.description) > 1200)
                                .order_by(Job.first_seen_at.desc()).limit(n))).all()
    parsed = master_row.parsed or {}
    master, contact = resume_data(parsed), Contact.model_validate(parsed.get("contact") or {})
    llm = Gemini.from_settings()
    rows = []
    for job in jobs:
        r = await tailor(llm, master=master, contact=contact, source_text=parsed.get("source_text"), title=job.title,
                         company=job.company_name, description=job.description,
                         job_terms=[*(job.required_skills or []), *(job.preferred_skills or [])])
        doc, pdf, dropped = fit_one_page(r["content"])
        final = validate(doc, master, parsed.get("source_text"))
        from scout_core.render_resume import page_count
        rows.append({"job": f"{job.title} @ {job.company_name}"[:70], "tailored": r["tailored"], "attempts": r["attempts"],
                     "final_unsupported": len(final["unsupported"]), "pages": page_count(pdf),
                     "ats_ok": ats_text_check(pdf, doc)["ok"], "dropped_to_fit": len(dropped),
                     "rejected_claims": (r["validation"].get("fallback_reason") or [])[:3]})
    return {"jobs": len(rows), "tailored": sum(x["tailored"] for x in rows),
            "first_try": sum(x["attempts"] == 1 and x["tailored"] for x in rows),
            "fallbacks": sum(not x["tailored"] for x in rows),
            "final_unsupported_claims": sum(x["final_unsupported"] for x in rows),
            "one_page": sum(x["pages"] == 1 for x in rows), "ats_ok": sum(x["ats_ok"] for x in rows),
            "llm_calls": llm.usage.calls, "rows": rows}


async def match_eval(maybe_counts_as_apply: bool = False):
    """My labels vs the match score (newest score per job). Read-only."""
    from scout_core.db.models import JobLabel, JobScore
    from scout_core.match_eval import Example, evaluate
    from scout_core.matching import PROMPT_VERSION

    latest = (select(JobScore.job_id, JobScore.score, JobScore.breakdown).where(JobScore.prompt_version == PROMPT_VERSION)
              .ext(distinct_on(JobScore.job_id)).order_by(JobScore.job_id, JobScore.created_at.desc()).subquery())
    async with worker_session() as session:
        rows = (await session.execute(
            select(Job.title, Job.company_name, JobLabel.label, latest.c.score, Job.relevance_score,
                   latest.c.breakdown)
            .join(JobLabel, JobLabel.job_id == Job.id).outerjoin(latest, latest.c.job_id == Job.id))).all()
    return evaluate([Example(title=t, company=c, label=lab, score=sc, baseline=rel or 0, parts=bd)
                     for t, c, lab, sc, rel, bd in rows], maybe_counts_as_apply=maybe_counts_as_apply)

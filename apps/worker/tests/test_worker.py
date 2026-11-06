from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from pydantic import SecretStr
from sqlalchemy import func, select

from scout_core.config import get_settings
from scout_core.db.models import AgentRun, Notification
from scout_core.schedule import due_tasks, in_quiet_hours
from scout_core.settings_store import DEFAULTS, set_setting
from scout_worker import work

IST = ZoneInfo("Asia/Kolkata")
SCHED = DEFAULTS["schedule"]


def _ist(*args):
    return datetime(*args, tzinfo=IST).astimezone(UTC)


def _tasks(now, **kw):
    return {d.task: d.slot_key for d in due_tasks(now, SCHED, **kw)}


def test_digest_due_from_8am_until_evening():
    assert "daily_digest" not in _tasks(_ist(2026, 11, 6, 7, 59))
    assert _tasks(_ist(2026, 11, 6, 8, 0))["daily_digest"] == "daily_digest:2026-11-06"
    assert _tasks(_ist(2026, 11, 6, 13, 0))["daily_digest"] == "daily_digest:2026-11-06"  # catch-up after downtime
    t = _tasks(_ist(2026, 11, 6, 20, 5))
    assert "daily_digest" not in t and t["evening_summary"] == "evening_summary:2026-11-06"


def test_ingest_slots_change_every_interval():
    # Default: every 10 minutes, aligned to UTC.
    a = _tasks(_ist(2026, 11, 6, 10, 0))["ingest"]
    b = _tasks(_ist(2026, 11, 6, 10, 9))["ingest"]
    c = _tasks(_ist(2026, 11, 6, 10, 10))["ingest"]
    assert a == b != c


def test_discovery_weekly_slot():
    # 2026-11-08 is a Sunday.
    assert _tasks(_ist(2026, 11, 8, 3, 1))["discovery"] == "discovery:2026-11-08"
    assert _tasks(_ist(2026, 11, 8, 2, 59))["discovery"] == "discovery:2026-11-01"
    assert _tasks(_ist(2026, 11, 11, 12, 0))["discovery"] == "discovery:2026-11-08"


def test_pause_stops_everything():
    assert due_tasks(_ist(2026, 11, 6, 9, 0), SCHED, paused=True) == []


def test_quiet_hours_wrap_midnight():
    q = {"start": "22:30", "end": "07:30"}
    assert in_quiet_hours(_ist(2026, 11, 6, 23, 0), q)
    assert in_quiet_hours(_ist(2026, 11, 6, 6, 0), q)
    assert not in_quiet_hours(_ist(2026, 11, 6, 12, 0), q)


@pytest.mark.db
async def test_done_slots_are_not_rerun_and_failed_retry_limit(session):
    now = _ist(2026, 11, 6, 9, 0)
    slots = dict(await work.slots_to_run(session, now))
    assert set(slots) >= {"ingest", "verify", "discovery", "daily_digest"}
    session.add(AgentRun(worker="ingest", task_key=slots["ingest"], status="success"))
    for _ in range(3):
        session.add(AgentRun(worker="verify", task_key=slots["verify"], status="error"))
    session.add(AgentRun(worker="discovery", task_key=slots["discovery"], status="error"))
    await session.commit()
    again = dict(await work.slots_to_run(session, now))
    assert "ingest" not in again          # done
    assert "verify" not in again          # failed 3 times: wait for next slot
    assert "discovery" in again           # failed once: retried


@pytest.mark.db
async def test_paused_setting_respected(session):
    await set_setting(session, "paused", True)
    await session.commit()
    assert await work.slots_to_run(session, _ist(2026, 11, 6, 9, 0)) == []


@pytest.mark.db
@respx.mock
async def test_digest_is_never_sent_twice(session, db_url, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "database_url", db_url)
    monkeypatch.setattr(s, "telegram_bot_token", SecretStr("123:abc"))
    monkeypatch.setattr(s, "telegram_chat_id", "42")
    sent = respx.post(url__regex=r"https://api\.telegram\.org/.*").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}}))
    await work.run_daily_digest("daily_digest:2026-11-06")
    first = sent.call_count
    assert first >= 1
    # Retry of the same slot (e.g. after a worker crash) must not message me again.
    await work.run_daily_digest("daily_digest:2026-11-06")
    assert sent.call_count == first
    n = await session.scalar(select(func.count()).select_from(Notification))
    assert n == 1


@pytest.mark.db
@respx.mock
async def test_failed_send_is_recorded_and_retried(session, db_url, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "database_url", db_url)
    monkeypatch.setattr(s, "telegram_bot_token", SecretStr("123:abc"))
    monkeypatch.setattr(s, "telegram_chat_id", "42")
    route = respx.post(url__regex=r"https://api\.telegram\.org/.*")
    route.mock(return_value=httpx.Response(500, json={"ok": False, "error_code": 500, "description": "down"}))
    with pytest.raises(Exception, match="down"):
        await work.run_daily_digest("daily_digest:2026-11-07")
    run = await session.scalar(select(AgentRun).where(AgentRun.task_key == "daily_digest:2026-11-07"))
    assert run.status == "error" and "down" in run.error
    route.mock(return_value=httpx.Response(200, json={"ok": True, "result": {}}))
    await work.run_daily_digest("daily_digest:2026-11-07")
    note = await session.scalar(select(Notification).where(Notification.dedupe_key == "daily_digest:2026-11-07"))
    await session.refresh(note)
    assert note.status == "sent"


async def _seed_visible_job(session, title="AI Engineer", sid="1"):
    from scout_core.filters.fresher import FresherRules
    from scout_core.filters.relevance import RelevanceRules
    from scout_core.pipeline.evaluate import Preferences
    from scout_core.pipeline.ingest import get_or_create_company, store_jobs
    from scout_core.types import NormalizedJob
    company = await get_or_create_company(session, name="Acme AI", website=None, source="test")
    await store_jobs(session, [NormalizedJob(
        source="lever", source_job_id=sid, company_name="Acme AI", title=title,
        description_text="Freshers welcome. Python, LLM, RAG.", locations=["Bengaluru"],
        apply_url=f"https://jobs.lever.co/acme/{sid}", board_token="acme")],
        company=company, fresher_rules=FresherRules(), relevance_rules=RelevanceRules(), prefs=Preferences())
    await session.commit()


async def _alert_env(session, db_url, monkeypatch, quiet=("00:00", "00:00")):
    s = get_settings()
    monkeypatch.setattr(s, "database_url", db_url)
    monkeypatch.setattr(s, "telegram_bot_token", SecretStr("123:abc"))
    monkeypatch.setattr(s, "telegram_chat_id", "42")
    monkeypatch.setattr(work.asyncio, "sleep", lambda *_: _noop())
    notif = {**DEFAULTS["notifications"], "quiet_hours": {"start": quiet[0], "end": quiet[1]}}
    await set_setting(session, "notifications", notif)
    await session.commit()


async def _noop():
    return None


@pytest.mark.db
@respx.mock
async def test_instant_alert_sent_once(session, db_url, monkeypatch):
    await _alert_env(session, db_url, monkeypatch)
    await _seed_visible_job(session)
    sent = respx.post(url__regex=r"https://api\.telegram\.org/.*").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}}))
    first = await work.run_instant_alerts()
    assert first["jobs_sent"] == 1 and sent.call_count == 2  # header + one job card
    second = await work.run_instant_alerts()
    assert second == {"sent": 0} and sent.call_count == 2


@pytest.mark.db
@respx.mock
async def test_instant_alert_quiet_hours(session, db_url, monkeypatch):
    await _alert_env(session, db_url, monkeypatch, quiet=("00:00", "23:59"))
    await _seed_visible_job(session)
    sent = respx.post(url__regex=r"https://api\.telegram\.org/.*").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}}))
    assert (await work.run_instant_alerts())["skipped"] == "quiet_hours"
    assert sent.call_count == 0


@pytest.mark.db
@respx.mock
async def test_instant_alert_failure_is_retried_not_lost(session, db_url, monkeypatch):
    from scout_core.db.models import Job
    await _alert_env(session, db_url, monkeypatch)
    await _seed_visible_job(session)
    route = respx.post(url__regex=r"https://api\.telegram\.org/.*")
    route.mock(return_value=httpx.Response(500, json={"ok": False, "error_code": 500, "description": "down"}))
    with pytest.raises(Exception, match="down"):
        await work.run_instant_alerts()
    job = await session.scalar(select(Job))
    await session.refresh(job)
    assert job.notified_at is None  # unmarked, so the next run sends it
    route.mock(return_value=httpx.Response(200, json={"ok": True, "result": {}}))
    assert (await work.run_instant_alerts())["jobs_sent"] == 1


@pytest.mark.db
@respx.mock
async def test_unrelated_jobs_not_alerted(session, db_url, monkeypatch):
    await _alert_env(session, db_url, monkeypatch)
    await _seed_visible_job(session, title="Inside Sales Executive")
    respx.post(url__regex=r"https://api\.telegram\.org/.*").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}}))
    assert await work.run_instant_alerts() == {"sent": 0}


@pytest.mark.db
async def test_gmail_skips_cleanly_without_connection_or_key(session, db_url, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "database_url", db_url)
    assert await work.run_gmail() == {"skipped": "gmail_not_connected"}
    from scout_core.db.models import EncryptedSecret
    session.add(EncryptedSecret(name="gmail_client", ciphertext=b"x"))
    session.add(EncryptedSecret(name="gmail_refresh_token", ciphertext=b"x"))
    await session.commit()
    monkeypatch.setattr(s, "encryption_key", None)
    assert await work.run_gmail() == {"skipped": "encryption_key_missing"}

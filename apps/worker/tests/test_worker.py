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


class _FakeLLM:
    """Stands in for Gemini: fixed rubric answer, fixed embeddings, records which jobs were scored."""

    def __init__(self):
        from scout_core.llm import Usage
        self.usage, self.jobs = Usage(), []

    seniority = None  # optional fixed answer for seniority checks

    async def json(self, schema, prompt, *, purpose, temperature=0.1):
        self.usage.calls += 1
        self.usage.by_call.append({"model": "fake-model"})
        if purpose == "seniority":
            return schema(**(self.seniority or {"verdict": "unclear"}))
        self.jobs.append(prompt.split("Title: ")[1].split("\n")[0])
        return schema(required_skills=["Python"], rubric_score=80, reasons=["fits"])

    async def embed(self, texts, *, purpose, dims=768):
        return [[1.0, 0.0] for _ in texts]


@pytest.mark.db
async def test_scoring_rescores_jobs_after_resume_change(session, db_url, monkeypatch):
    from scout_core.db.models import JobScore, MasterResume
    from scout_core.llm import Gemini
    from scout_core.resume_store import add_version
    s = get_settings()
    monkeypatch.setattr(s, "database_url", db_url)
    fake = _FakeLLM()
    monkeypatch.setattr(Gemini, "from_settings", classmethod(lambda cls: fake))
    assert await work.run_scoring("manual:x") == {"skipped": "no_master_resume"}

    resume = {"skills": [{"category": "Languages", "items": ["Python"]}], "projects": [], "education": []}
    session.add(MasterResume(file_url="local:r.pdf", version=1, is_active=True, parsed=resume))
    await session.commit()
    await _seed_visible_job(session, "AI Engineer", "1")
    await _seed_visible_job(session, "Backend Engineer", "2")

    await work.run_scoring("manual:1", limit=10)
    assert sorted(fake.jobs) == ["AI Engineer", "Backend Engineer"]
    await work.run_scoring("manual:2", limit=10)
    assert len(fake.jobs) == 2  # nothing new, nothing re-scored

    # A resume edit that changes the profile makes both scores outdated; a new job comes first.
    await add_version(session, file_url="edit:1", parsed={**resume, "skills": [
        {"category": "Languages", "items": ["Python", "Go"]}]})
    await session.commit()
    await _seed_visible_job(session, "ML Engineer", "3")
    fake.jobs.clear()
    await work.run_scoring("manual:3", limit=1)
    assert fake.jobs == ["ML Engineer"]
    await work.run_scoring("manual:4", limit=10)
    assert sorted(fake.jobs) == ["AI Engineer", "Backend Engineer", "ML Engineer"]
    hashes = (await session.execute(select(JobScore.profile_hash, func.count()).group_by(JobScore.profile_hash))).all()
    assert sorted(n for _, n in hashes) == [2, 3] and all(h for h, _ in hashes)

    # A correction that doesn't touch what scoring reads (contact, a fixed typo elsewhere) costs no quota.
    await add_version(session, file_url="edit:2", parsed={**resume, "skills": [
        {"category": "Languages", "items": ["Python", "Go"]}], "contact": {"name": "Jane"}, "achievements": ["x"]})
    await session.commit()
    fake.jobs.clear()
    await work.run_scoring("manual:5", limit=10)
    assert fake.jobs == []


@pytest.mark.db
async def test_match_eval_uses_newest_score_and_my_labels(session, db_url, monkeypatch):
    from scout_core.db.models import Job, JobLabel, JobScore
    from scout_core.matching import PROMPT_VERSION
    monkeypatch.setattr(get_settings(), "database_url", db_url)
    await _seed_visible_job(session, "AI Engineer", "1")
    await _seed_visible_job(session, "Backend Engineer", "2")
    await _seed_visible_job(session, "Data Analyst", "3")
    ids = {t: i for i, t in (await session.execute(select(Job.id, Job.title))).all()}
    session.add_all([
        JobScore(job_id=ids["AI Engineer"], score=40, model="m", prompt_version=PROMPT_VERSION),  # older
        JobScore(job_id=ids["Backend Engineer"], score=30, model="m", prompt_version=PROMPT_VERSION),
        JobLabel(job_id=ids["AI Engineer"], label="apply"), JobLabel(job_id=ids["Backend Engineer"], label="no"),
        JobLabel(job_id=ids["Data Analyst"], label="no"),  # labelled, never scored
    ])
    await session.commit()
    session.add(JobScore(job_id=ids["AI Engineer"], score=91, model="m", prompt_version=PROMPT_VERSION))
    await session.commit()
    rep = await work.match_eval()
    assert rep.labelled == 3 and rep.scored == 2 and rep.auc == 1.0
    at85 = next(t for t in rep.thresholds if t["floor"] == 85)
    assert at85["precision"] == 1.0 and at85["recall"] == 1.0  # used 91, not the older 40


@pytest.mark.db
async def test_resume_backfill_stamps_old_scores_once(session, db_url, monkeypatch, tmp_path):
    from scout_core.db.models import Job, JobScore, MasterResume
    from scout_core.matching import PROMPT_VERSION
    from scout_core.resume import ResumeFileError
    monkeypatch.setattr(get_settings(), "database_url", db_url)
    resume = {"skills": [{"category": "L", "items": ["Python"]}], "contact": {"emails": ["me@x.com"]},
              "source_text": "Skills: Python"}
    session.add(MasterResume(file_url="local:r.pdf", version=1, is_active=True, parsed=resume))
    await _seed_visible_job(session, "AI Engineer", "1")
    job_id = await session.scalar(select(Job.id))
    session.add(JobScore(job_id=job_id, score=70, model="m", prompt_version=PROMPT_VERSION))
    await session.commit()
    dry = await work.resume_backfill(str(tmp_path / "unused.pdf"))
    assert dry == {"version": 1, "source_text": "already stored", "scores_to_stamp": 1, "applied": False}
    await work.resume_backfill(str(tmp_path / "unused.pdf"), apply=True)
    assert (await work.resume_backfill(str(tmp_path / "unused.pdf")))["scores_to_stamp"] == 0
    # The stamped score counts as current: nothing to re-score.
    fake = _FakeLLM()
    from scout_core.llm import Gemini
    monkeypatch.setattr(Gemini, "from_settings", classmethod(lambda cls: fake))
    await work.run_scoring("manual:x", limit=5)
    assert fake.jobs == []
    # Missing source text + a file that isn't the same resume (or isn't a PDF) is refused.
    row = await session.scalar(select(MasterResume))
    row.parsed = {k: v for k, v in resume.items() if k != "source_text"}
    await session.commit()
    (tmp_path / "x.pdf").write_bytes(b"not a pdf")
    with pytest.raises(ResumeFileError):
        await work.resume_backfill(str(tmp_path / "x.pdf"))



async def _seed_unclear_job(session, title, sid, description):
    from scout_core.filters.fresher import FresherRules
    from scout_core.filters.relevance import RelevanceRules
    from scout_core.pipeline.evaluate import Preferences
    from scout_core.pipeline.ingest import get_or_create_company, store_jobs
    from scout_core.types import NormalizedJob
    company = await get_or_create_company(session, name="Acme AI", website=None, source="test")
    await store_jobs(session, [NormalizedJob(
        source="lever", source_job_id=sid, company_name="Acme AI", title=title, description_text=description,
        locations=["Bengaluru"], apply_url=f"https://jobs.lever.co/acme/{sid}", board_token="acme")],
        company=company, fresher_rules=FresherRules(), relevance_rules=RelevanceRules(), prefs=Preferences())
    await session.commit()


FRESHER_TEXT = ("We are building an LLM platform in Python and FastAPI. This role suits people who graduated "
                "this year; strong projects matter more than a long CV. You will write tests. " * 2)

BACKEND_TEXT = ("We are building an LLM platform in Python and FastAPI. You will design APIs, write tests, "
                "and ship features with a small team. Experience building backend services in production "
                "for several years is expected. You will own the architecture of our retrieval stack. " * 2)


@pytest.mark.db
@pytest.mark.parametrize(("answer", "visible", "passed"), [
    ({"verdict": "needs_experience", "evidence": "Experience building backend services in production for several years is expected."}, False, False),
    ({"verdict": "fresher_ok", "evidence": "Experience building backend services in production for several years is expected."}, False, True),  # quote contradicts the answer
    ({"verdict": "fresher_ok", "evidence": "and ship features with a small team"}, False, True),  # real, but says nothing about freshers
    ({"verdict": "fresher_ok", "evidence": "Freshers are welcome to apply for this role."}, False, True),  # invented quote
    ({"verdict": "unclear", "evidence": ""}, False, True),
])
async def test_unclear_experience_settled_only_by_verified_quotes(session, db_url, monkeypatch, answer, visible, passed):
    from scout_core.db.models import Job
    from scout_core.services import visible_feed_query
    monkeypatch.setattr(get_settings(), "database_url", db_url)
    await _seed_unclear_job(session, "Backend Engineer (Python)", "u1", BACKEND_TEXT)
    job = await session.scalar(select(Job))
    assert job.passed_filters and job.experience_unclear
    fake = _FakeLLM()
    fake.seniority = answer
    counts = await work.check_unclear_experience(fake)
    assert counts["checked"] == 1
    await session.refresh(job)
    assert job.passed_filters is passed
    shown = (await session.execute(visible_feed_query().with_only_columns(func.count()))).scalar()
    assert (shown == 1) is visible
    assert job.fresher_reasons[0].startswith("AI check:")
    # Checked once: never sent to Gemini again.
    assert (await work.check_unclear_experience(fake))["checked"] == 0


@pytest.mark.db
async def test_unclear_job_with_fresher_quote_reaches_feed(session, db_url, monkeypatch):
    from scout_core.db.models import Job
    monkeypatch.setattr(get_settings(), "database_url", db_url)
    # No numbers and no phrase the plain rules know: unclear until the description is read.
    await _seed_unclear_job(session, "Backend Engineer (Python)", "f1", FRESHER_TEXT)
    job = await session.scalar(select(Job))
    assert job.experience_unclear
    fake = _FakeLLM()
    fake.seniority = {"verdict": "fresher_ok",
                      "evidence": "This role suits people who graduated this year; strong projects matter more than a long CV."}
    assert (await work.check_unclear_experience(fake))["fresher_ok"] == 1
    await session.refresh(job)
    assert job.experience_unclear is False and job.passed_filters

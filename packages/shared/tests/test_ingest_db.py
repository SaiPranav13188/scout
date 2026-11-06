"""Ingestion against a real Postgres: idempotency, dedupe, never re-surfacing, freshness."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from scout_core.db.models import CompanyWatchlist, Job
from scout_core.filters.fresher import FresherRules
from scout_core.filters.relevance import RelevanceRules
from scout_core.pipeline.evaluate import Preferences
from scout_core.pipeline.ingest import get_or_create_company, mark_missing_closed, store_jobs
from scout_core.sources.ats import SOURCES
from scout_core.types import ApplicationStatus, AtsType, NormalizedJob, RawFetch

pytestmark = pytest.mark.db
FIX = Path(__file__).parent / "fixtures"
RULES = dict(fresher_rules=FresherRules(), relevance_rules=RelevanceRules(), prefs=Preferences())


def _parse(source, token, name):
    raw = RawFetch(source=source, board_token=token, url="x", status_code=200,
                   fetched_at=datetime.now(UTC), payload=json.loads((FIX / name).read_text()))
    return SOURCES[source].parse(raw)


def _job(**kw):
    base = dict(source="lever", source_job_id="a1", company_name="Acme", title="Backend Engineer",
                description_text="Freshers welcome. 0-1 years of experience.", locations=["Bengaluru"],
                apply_url="https://jobs.lever.co/acme/a1", board_token="acme")
    base.update(kw)
    return NormalizedJob(**base)


async def _count(session, *where):
    return await session.scalar(select(func.count()).select_from(Job).where(*where))


async def test_real_payload_ingested_once(session):
    company = await get_or_create_company(session, name="Bolna AI", website="https://bolna.ai", source="test")
    jobs = _parse("ashby", "bolna", "ashby_full.json")
    first = await store_jobs(session, jobs, company=company, **RULES)
    await session.commit()
    assert first.new == len(jobs) and first.kept + first.dropped + first.duplicates == len(jobs)
    second = await store_jobs(session, jobs, company=company, **RULES)
    await session.commit()
    assert second.new == 0 and second.seen_again == len(jobs)
    assert await _count(session) == len(jobs)


async def test_dropped_jobs_keep_reasons_but_not_text(session):
    company = await get_or_create_company(session, name="Acme", website=None, source="test")
    await store_jobs(session, [_job(title="Senior Backend Engineer")], company=company, **RULES)
    await session.commit()
    j = await session.scalar(select(Job))
    assert j.passed_filters is False and j.description is None
    assert "senior" in j.filter_reasons[0]


async def test_same_job_from_another_source_is_a_duplicate(session):
    company = await get_or_create_company(session, name="Acme", website=None, source="test")
    await store_jobs(session, [_job()], company=company, **RULES)
    await store_jobs(session, [_job(source="adzuna", source_job_id="z9",
                                    apply_url="https://jobs.lever.co/acme/a1?utm_source=adzuna")],
                     company=company, **RULES)
    await session.commit()
    assert await _count(session) == 2
    assert await _count(session, Job.duplicate_of.is_(None)) == 1
    dup = await session.scalar(select(Job).where(Job.duplicate_of.is_not(None)))
    assert dup.duplicate_reason == "same_apply_url"


async def test_skipped_job_is_not_resurfaced_by_repost(session):
    company = await get_or_create_company(session, name="Acme", website=None, source="test")
    await store_jobs(session, [_job()], company=company, **RULES)
    original = await session.scalar(select(Job))
    original.status = ApplicationStatus.SKIPPED
    await session.commit()
    # Same role re-posted under a new id and URL a week later.
    await store_jobs(session, [_job(source_job_id="a2", apply_url="https://jobs.lever.co/acme/a2")],
                     company=company, **RULES)
    await session.commit()
    visible = await _count(session, Job.duplicate_of.is_(None), Job.status == ApplicationStatus.DISCOVERED)
    assert visible == 0


async def test_jobs_missing_from_board_are_closed_and_can_reopen(session):
    company = await get_or_create_company(session, name="Acme", website=None, source="test")
    await store_jobs(session, [_job(), _job(source_job_id="a3", title="Frontend Engineer",
                                            apply_url="https://jobs.lever.co/acme/a3")], company=company, **RULES)
    await session.commit()
    closed = await mark_missing_closed(session, source="lever", board_token="acme", seen_ids={"a1"},
                                       now=datetime.now(UTC))
    await session.commit()
    assert closed == 1
    gone = await session.scalar(select(Job).where(Job.source_job_id == "a3"))
    assert gone.is_open is False and gone.closed_at is not None
    stats = await store_jobs(session, [_job(source_job_id="a3", title="Frontend Engineer",
                                            apply_url="https://jobs.lever.co/acme/a3")], company=company, **RULES)
    assert stats.reopened == 1


async def test_company_dedupes_on_name_and_domain(session):
    a = await get_or_create_company(session, name="Razorpay Software Private Limited",
                                    website="https://razorpay.com", source="test")
    b = await get_or_create_company(session, name="Razorpay Software Pvt Ltd", website=None, source="test")
    c = await get_or_create_company(session, name="RZP", website="https://www.razorpay.com/careers", source="test")
    assert a.id == b.id == c.id


async def test_watchlist_board_unique(session):
    company = await get_or_create_company(session, name="Acme", website=None, source="test")
    session.add(CompanyWatchlist(company_id=company.id, ats_type=AtsType.LEVER, board_token="acme",
                                 discovery_method="manual"))
    await session.commit()
    session.add(CompanyWatchlist(company_id=company.id, ats_type=AtsType.LEVER, board_token="acme",
                                 discovery_method="manual"))
    with pytest.raises(Exception, match="uq_watchlist_board"):
        await session.commit()


async def test_duplicates_within_one_batch(session):
    company = await get_or_create_company(session, name="Acme", website=None, source="test")
    stats = await store_jobs(session, [_job(), _job(source_job_id="a9", apply_url="https://jobs.lever.co/acme/a9")],
                             company=company, **RULES)
    await session.commit()
    assert stats.new == 2 and stats.duplicates == 1
    assert await _count(session, Job.duplicate_of.is_(None)) == 1

"""Ingestion: source payload -> raw store -> normalize -> dedupe -> filters -> Postgres.

Idempotent: a job is identified by (source, source_job_id). Seeing it again only refreshes
last_seen_at; it is never re-processed and never re-surfaced after I skipped or applied.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from scout_core.db.models import Company, CompanyWatchlist, Job
from scout_core.dedupe import Candidate, dedupe_key, find_duplicate
from scout_core.filters.fresher import FresherRules
from scout_core.filters.relevance import RelevanceRules
from scout_core.http import PoliteClient
from scout_core.normalize import normalize_company, normalize_title, registered_domain
from scout_core.pipeline.evaluate import Preferences, evaluate_job
from scout_core.rawstore import RawStore
from scout_core.sources.ats import SOURCES
from scout_core.sources.base import BoardNotFound
from scout_core.types import NormalizedJob

log = structlog.get_logger()

MAX_BOARD_FAILURES = 3


@dataclass
class IngestStats:
    fetched: int = 0
    new: int = 0
    seen_again: int = 0
    duplicates: int = 0
    kept: int = 0
    dropped: int = 0
    closed: int = 0
    reopened: int = 0
    drop_reasons: dict[str, int] = field(default_factory=dict)

    def as_counts(self) -> dict[str, int]:
        return {k: v for k, v in self.__dict__.items() if isinstance(v, int)}


async def get_or_create_company(session: AsyncSession, *, name: str, website: str | None,
                                source: str, **fields) -> Company:
    norm = normalize_company(name) or name.lower()
    domain = registered_domain(website)
    match = Company.normalized_name == norm
    if domain:
        match = or_(match, Company.normalized_domain == domain)
    existing = await session.scalar(select(Company).where(match).limit(1))
    if existing:
        return existing
    stmt = insert(Company).values(name=name, normalized_name=norm, website=website,
                                  normalized_domain=domain, source=source, **fields)
    await session.execute(stmt.on_conflict_do_nothing())
    # Re-read with the same match: a concurrent insert may have won on either unique key.
    company = await session.scalar(select(Company).where(match).limit(1))
    if company is None:
        raise RuntimeError(f"could not create or find company '{name}'")
    return company


async def _load_candidates(session: AsyncSession, company_norms: set[str],
                           canonicals: set[str]) -> list[Candidate]:
    """All possible originals for a batch of new jobs, in one query."""
    if not company_norms and not canonicals:
        return []
    cond = Job.company_norm.in_(company_norms)
    if canonicals:
        cond = or_(cond, Job.canonical_apply_url.in_(canonicals))
    rows = (await session.execute(
        select(Job.id, Job.company_norm, Job.title_norm, Job.location_key, Job.canonical_apply_url,
               Job.first_seen_at)
        .where(Job.duplicate_of.is_(None), cond))).all()
    return [Candidate(str(r.id), r.company_norm, r.title_norm, r.location_key, r.canonical_apply_url,
                      r.first_seen_at) for r in rows]


INSERT_BATCH = 200


async def store_jobs(
    session: AsyncSession,
    jobs: list[NormalizedJob],
    *,
    company: Company | None,
    fresher_rules: FresherRules,
    relevance_rules: RelevanceRules,
    prefs: Preferences,
    now: datetime | None = None,
) -> IngestStats:
    """Store a batch of jobs from one fetch.

    Round trips are per batch, not per job (the database may be far away): one query finds jobs
    already stored, one finds duplicate candidates, and new rows go in with batched inserts.
    """
    now = now or datetime.now(UTC)
    stats = IngestStats(fetched=len(jobs))
    if not jobs:
        return stats

    # 1. Jobs seen before: refresh last_seen_at, reopen if they were marked closed.
    keys = {(j.source, j.source_job_id) for j in jobs}
    existing: dict[tuple[str, str], tuple[uuid.UUID, bool]] = {}
    for source in {k[0] for k in keys}:
        ids = [k[1] for k in keys if k[0] == source]
        for i in range(0, len(ids), 1000):
            rows = (await session.execute(
                select(Job.id, Job.source_job_id, Job.is_open, Job.closed_at)
                .where(Job.source == source, Job.source_job_id.in_(ids[i:i + 1000])))).all()
            for r in rows:
                existing[(source, r.source_job_id)] = (r.id, r.is_open is False and r.closed_at is not None)
    if existing:
        seen_ids = [v[0] for v in existing.values()]
        reopen_ids = [v[0] for v in existing.values() if v[1]]
        await session.execute(update(Job).where(Job.id.in_(seen_ids)).values(last_seen_at=now))
        if reopen_ids:
            await session.execute(update(Job).where(Job.id.in_(reopen_ids)).values(is_open=True, closed_at=None))
        stats.seen_again = len(existing)
        stats.reopened = len(reopen_ids)

    # 2. New jobs: evaluate in plain code, then dedupe against stored and same-batch jobs.
    fresh = []
    batch_keys: set[tuple[str, str]] = set()
    for nj in jobs:
        key = (nj.source, nj.source_job_id)
        if key in existing or key in batch_keys:
            continue
        batch_keys.add(key)
        ev = evaluate_job(nj, fresher_rules=fresher_rules, relevance_rules=relevance_rules, prefs=prefs,
                          company_website=company.website if company else None,
                          in_yc_directory=bool(company and company.in_yc_directory),
                          domain_created_at=company.domain_created_at if company else None)
        fresh.append((nj, ev, normalize_company(nj.company_name)))
    if not fresh:
        return stats

    candidates = await _load_candidates(session, {c for _, _, c in fresh},
                                        {ev.canonical_apply_url for _, ev, _ in fresh if ev.canonical_apply_url})
    rows: list[dict] = []
    for nj, ev, company_norm in fresh:
        dup = find_duplicate(company=nj.company_name, title=nj.title, location_key=ev.location_key,
                             canonical_apply_url=ev.canonical_apply_url, candidates=candidates)
        job_id = uuid.uuid4()
        title_norm = normalize_title(nj.title)
        if not dup:
            candidates.append(Candidate(str(job_id), company_norm, title_norm, ev.location_key,
                                        ev.canonical_apply_url, now))
        rows.append(dict(
            id=job_id,
            company_id=company.id if company else None,
            company_name=nj.company_name,
            company_norm=company_norm,
            source=nj.source,
            source_job_id=nj.source_job_id,
            board_token=nj.board_token,
            title=nj.title,
            title_norm=title_norm,
            # Dropped jobs keep their reasons but not the full text (the raw store has it).
            description=nj.description_text if ev.keep else None,
            department=nj.department,
            experience_min=ev.fresher.experience_min,
            experience_max=ev.fresher.experience_max,
            experience_unclear=ev.fresher.experience_unclear,
            fresher_confidence=ev.fresher.confidence.value,
            fresher_reasons=ev.fresher.reasons,
            location=nj.location,
            locations=nj.locations,
            location_bucket=ev.location.bucket.value,
            location_key=ev.location_key,
            remote=ev.location.remote,
            salary=nj.salary.model_dump(exclude_none=True) if nj.salary else None,
            employment_type=nj.employment_type,
            role_family=ev.relevance.family,
            relevance_score=ev.relevance.score,
            relevance_matched=ev.relevance.matched,
            trust_level=ev.trust.level.value,
            trust=ev.trust.as_dict(),
            posted_at=nj.posted_at,
            posted_at_kind=nj.posted_at_kind,
            apply_url=nj.apply_url,
            canonical_apply_url=ev.canonical_apply_url,
            dedupe_key=dedupe_key(nj.company_name, nj.title, ev.location_key),
            duplicate_of=uuid.UUID(dup[0].id) if dup else None,
            duplicate_reason=dup[1] if dup else None,
            passed_filters=ev.keep,
            filter_reasons=ev.drop_reasons,
            first_seen_at=now,
            last_seen_at=now,
        ))

    inserted: set[uuid.UUID] = set()
    for i in range(0, len(rows), INSERT_BATCH):
        result = await session.execute(
            insert(Job).values(rows[i:i + INSERT_BATCH])
            .on_conflict_do_nothing(constraint="uq_jobs_source_id").returning(Job.id))
        inserted.update(result.scalars().all())

    for row, (_, ev, _) in zip(rows, fresh, strict=True):
        if row["id"] not in inserted:
            stats.seen_again += 1  # another worker inserted it first
            continue
        stats.new += 1
        if row["duplicate_of"]:
            stats.duplicates += 1
        elif ev.keep:
            stats.kept += 1
        else:
            stats.dropped += 1
            reason = ev.drop_reasons[0].split(":")[0]
            stats.drop_reasons[reason] = stats.drop_reasons.get(reason, 0) + 1
    return stats


async def mark_missing_closed(session: AsyncSession, *, source: str, board_token: str,
                              seen_ids: set[str], now: datetime) -> int:
    """Jobs that vanished from a board's full listing are closed (freshness, spec §13.4)."""
    rows = (await session.execute(
        select(Job.id, Job.source_job_id).where(
            Job.source == source, Job.board_token == board_token,
            or_(Job.is_open.is_(None), Job.is_open.is_(True))))).all()
    gone = [r.id for r in rows if r.source_job_id not in seen_ids]
    if gone:
        await session.execute(update(Job).where(Job.id.in_(gone)).values(
            is_open=False, closed_at=now, verify_status="closed",
            verify_reason="no longer listed on the company's job board", verified_at=now))
    return len(gone)


async def ingest_board(
    session: AsyncSession,
    client: PoliteClient,
    raw_store: RawStore,
    watch: CompanyWatchlist,
    company: Company,
    *,
    fresher_rules: FresherRules,
    relevance_rules: RelevanceRules,
    prefs: Preferences,
) -> IngestStats:
    source = SOURCES[watch.ats_type.value]
    now = datetime.now(UTC)
    try:
        raw = await source.fetch(client, watch.board_token)
    except BoardNotFound:
        watch.consecutive_failures += 1
        watch.last_error = "board not found (404)"
        if watch.consecutive_failures >= MAX_BOARD_FAILURES:
            watch.active = False
        await session.commit()
        raise

    parsed_ok = True
    try:
        jobs = source.parse(raw)
    except (KeyError, TypeError, ValueError):
        parsed_ok = False
        raise
    finally:
        await raw_store.save_job_payload(source=raw.source, board_token=raw.board_token, url=raw.url,
                                         fetched_at=raw.fetched_at, payload=raw.payload, parsed_ok=parsed_ok)

    for j in jobs:
        j.company_name = company.name  # the watchlist company is authoritative
    stats = await store_jobs(session, jobs, company=company, fresher_rules=fresher_rules,
                             relevance_rules=relevance_rules, prefs=prefs, now=now)
    stats.closed = await mark_missing_closed(session, source=source.name, board_token=watch.board_token,
                                             seen_ids={j.source_job_id for j in jobs}, now=now)
    # Being listed on the live board right now is itself a verification for ATS jobs.
    await session.execute(update(Job).where(
        Job.source == source.name, Job.board_token == watch.board_token,
        Job.source_job_id.in_([j.source_job_id for j in jobs])).values(
            is_open=True, verified_at=now, verify_status="open",
            verify_reason="listed on the company's live job board"))
    watch.last_fetched_at = now
    watch.consecutive_failures = 0
    watch.last_error = None
    await session.commit()
    return stats

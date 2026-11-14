"""Editable settings stored in the `settings` table, with spec defaults for anything not set yet."""

from dataclasses import asdict, fields
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from scout_core.db.models import Setting
from scout_core.filters.fresher import FresherRules
from scout_core.filters.relevance import RelevanceRules
from scout_core.pipeline.evaluate import Preferences

DEFAULTS: dict[str, Any] = {
    "schedule": {
        "timezone": "Asia/Kolkata",
        # Company boards change slowly and a full check of ~280 boards takes ~10 min on GitHub's runners,
        # so every 30 min; Gmail alerts (fast, where most fresher jobs arrive) every 10 min (2026-11-14).
        "ingest_interval_minutes": 30,   # how often company job boards are checked
        "gmail_interval_minutes": 10,    # how often job-alert emails are read
        "verify_interval_hours": 12,
        "discovery_weekday": 6,          # Sunday (0 = Monday)
        "discovery_time": "03:00",
        "daily_digest_time": "08:00",
        "evening_summary_time": "20:00",
    },
    "notifications": {
        "telegram_enabled": True,
        "digest_size": 8,
        "quiet_hours": {"start": "22:30", "end": "07:30"},
        "instant_alerts_enabled": True,  # one Telegram message per batch of new relevant jobs
        "instant_alert_max_jobs": 8,     # jobs listed in full per message; the rest are counted
        "instant_alert_min_score": 85,   # used once scoring exists (Phase 3)
        "follow_up_days": 7,             # reminder this many days after I mark a job applied
    },
    "sources": {"greenhouse": True, "lever": True, "ashby": True, "gmail_alerts": True, "adzuna": False},
    # Newest alert email already processed (Gmail internalDate, ms). Managed by the worker.
    "gmail_state": {"last_internal_ms": 0},
    "adzuna": {
        "queries": ["software engineer fresher", "backend developer fresher", "ai engineer",
                    "full stack developer fresher", "python developer fresher", "graduate engineer trainee"],
        "max_days_old": 3,
        "pages_per_query": 2,
    },
    "discovery": {"yc_enabled": True, "max_companies_per_run": 800},
    "fresher_rules": asdict(FresherRules()),
    "relevance_rules": asdict(RelevanceRules()),
    "preferences": asdict(Preferences()),
    "matching": {
        "jobs_per_run": 10,  # free-tier friendly: about 60 jobs/hour on the 10-minute schedule
        "weights": {"skills": 0.35, "embedding": 0.25, "rubric": 0.40},
        "bands": {"top": 85, "good": 70, "stretch": 55},
        "target_roles": ["Applied AI engineer", "Agentic AI / LLM engineer", "Forward Deployed Engineer",
                         "Backend engineer (Python, Node/TypeScript)", "Full-stack engineer",
                         "Software engineer (entry level)"],
    },
    "paused": False,
    "last_visit_at": None,
}


async def get_setting(session: AsyncSession, key: str) -> Any:
    row = await session.get(Setting, key)
    default = DEFAULTS.get(key)
    if row is None:
        return default
    if isinstance(default, dict) and isinstance(row.value, dict):
        return {**default, **row.value}
    return row.value


async def get_all(session: AsyncSession) -> dict[str, Any]:
    rows = {r.key: r.value for r in (await session.scalars(select(Setting))).all()}
    out = {}
    for key, default in DEFAULTS.items():
        if key in rows and isinstance(default, dict) and isinstance(rows[key], dict):
            out[key] = {**default, **rows[key]}
        else:
            out[key] = rows.get(key, default)
    return out


async def set_setting(session: AsyncSession, key: str, value: Any) -> None:
    if key not in DEFAULTS:
        raise KeyError(f"unknown setting '{key}'")
    stmt = insert(Setting).values(key=key, value=value)
    stmt = stmt.on_conflict_do_update(index_elements=[Setting.key], set_={"value": value})
    await session.execute(stmt)


def _build(cls, data: dict | None):
    data = data or {}
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


async def load_rules(session: AsyncSession) -> tuple[FresherRules, RelevanceRules, Preferences]:
    return (
        _build(FresherRules, await get_setting(session, "fresher_rules")),
        _build(RelevanceRules, await get_setting(session, "relevance_rules")),
        _build(Preferences, await get_setting(session, "preferences")),
    )

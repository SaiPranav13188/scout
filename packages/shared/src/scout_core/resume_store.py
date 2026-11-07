"""Master resume versions in Postgres.

A version is never edited after it is saved (spec §7). Corrections, uploads and re-parses all add a
new version and make it the active one; any earlier version can be made active again.
"""

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from scout_core.db.models import MasterResume
from scout_core.resume import ResumeData


async def active_resume(session: AsyncSession) -> MasterResume | None:
    return await session.scalar(select(MasterResume).where(MasterResume.is_active.is_(True)))


async def add_version(session: AsyncSession, *, file_url: str, parsed: dict[str, Any]) -> MasterResume:
    """Store a new version and make it the only active one. The caller commits."""
    latest = await session.scalar(select(func.max(MasterResume.version)))
    await session.execute(update(MasterResume).where(MasterResume.is_active.is_(True)).values(is_active=False))
    row = MasterResume(file_url=file_url, version=(latest or 0) + 1, is_active=True, parsed=parsed)
    session.add(row)
    await session.flush()
    return row


async def activate(session: AsyncSession, version: int) -> MasterResume | None:
    row = await session.scalar(select(MasterResume).where(MasterResume.version == version))
    if row is None:
        return None
    await session.execute(update(MasterResume).where(MasterResume.id != row.id).values(is_active=False))
    row.is_active = True
    await session.flush()
    return row


def resume_data(parsed: dict[str, Any]) -> ResumeData:
    return ResumeData.model_validate({k: v for k, v in parsed.items() if k in ResumeData.model_fields})

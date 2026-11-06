"""agent_runs bookkeeping and the audit log. No silent failures: every error leaves a row."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from scout_core.db.models import AgentRun, AuditLog

log = structlog.get_logger()


class RunHandle:
    def __init__(self, run: AgentRun) -> None:
        self.run = run
        self.counts: dict[str, int] = {}
        self.errors: list[str] = []

    def inc(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n

    def error(self, message: str) -> None:
        """Record a non-fatal error; the run finishes as 'partial'."""
        self.errors.append(message)
        self.inc("errors")


@asynccontextmanager
async def track_run(session: AsyncSession, worker: str, task_key: str | None = None,
                    retries: int = 0) -> AsyncIterator[RunHandle]:
    run = AgentRun(worker=worker, task_key=task_key, status="running", retries=retries)
    session.add(run)
    await session.commit()
    handle = RunHandle(run)
    try:
        yield handle
    except Exception as exc:
        await session.rollback()
        run.status = "error"
        run.error = f"{type(exc).__name__}: {exc}"[:4000]
        run.finished_at = datetime.now(UTC)
        run.counts = handle.counts
        session.add(run)
        await session.commit()
        log.error("run.failed", worker=worker, task_key=task_key, error=run.error)
        raise
    else:
        run.status = "partial" if handle.errors else "success"
        run.error = "\n".join(handle.errors[:50])[:4000] or None
        run.finished_at = datetime.now(UTC)
        run.counts = handle.counts
        await session.commit()
        log.info("run.finished", worker=worker, task_key=task_key, status=run.status, counts=handle.counts)


def audit(session: AsyncSession, *, actor: str, action: str, entity: str, entity_id: str | None,
          before: dict[str, Any] | None = None, after: dict[str, Any] | None = None) -> None:
    session.add(AuditLog(actor=actor, action=action, entity=entity, entity_id=entity_id,
                         before=before, after=after))

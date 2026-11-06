"""Celery wiring. Beat only fires `scheduler.tick` every minute; the tick decides what is due
from Postgres (see scout_core.schedule), so schedules are editable in Settings without a restart.
"""

import asyncio
from datetime import UTC, datetime

import redis
import structlog
from celery import Celery, signals

from scout_core.config import get_settings
from scout_core.db.session import worker_session
from scout_worker import work

log = structlog.get_logger()
settings = get_settings()

app = Celery("scout", broker=settings.redis_url, backend=None)
app.conf.update(
    task_acks_late=True,                 # a task is only removed from Redis after it finished
    task_reject_on_worker_lost=True,     # worker crash mid-task -> task is redelivered
    worker_prefetch_multiplier=1,
    broker_transport_options={"visibility_timeout": 3 * 3600},
    broker_connection_retry_on_startup=True,
    task_default_queue="scout",
    timezone="UTC",
    beat_schedule={"tick": {"task": "scheduler.tick", "schedule": 60.0}},
)

RUNNERS = work.RUNNERS


@app.task(name="scheduler.tick", ignore_result=True)
def tick() -> None:
    async def _tick():
        async with worker_session() as session:
            return await work.slots_to_run(session, datetime.now(UTC))

    r = redis.Redis.from_url(settings.redis_url)
    for task, slot_key in asyncio.run(_tick()):
        # The slot only shows up in agent_runs once a worker starts it, so a slot waiting in the
        # queue would be enqueued again by the next tick. A short Redis lock prevents that. If
        # Redis loses the lock, the worst case is one extra idempotent run (digests are guarded
        # separately by the notifications table).
        if not r.set(f"scout:slot:{slot_key}", "1", nx=True, ex=2 * 3600):
            continue
        run_slot.apply_async(args=[task, slot_key])
        log.info("tick.enqueued", task=task, slot=slot_key)


@app.task(name="scheduler.run_slot", bind=True, max_retries=2, default_retry_delay=300, ignore_result=True)
def run_slot(self, task: str, slot_key: str) -> None:
    runner = RUNNERS[task]
    try:
        asyncio.run(runner(slot_key))
        if task in ("ingest", "gmail"):
            asyncio.run(work.run_instant_alerts())
    except Exception as exc:
        asyncio.run(work.alert_failure(task, f"{type(exc).__name__}: {exc}"))
        raise self.retry(exc=exc) from exc


@signals.worker_ready.connect
def _recover_on_start(**_) -> None:
    """Restart safety: anything due but not done (according to Postgres) is queued again now."""
    tick.delay()

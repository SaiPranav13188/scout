"""Long-running scheduler for GitHub Actions (2026-11-14).

Why: the Render free API that the Postgres timer called every 10 minutes was suspended on 2026-11-13
("Monthly Quota Exceeded": all free services in the Render workspace share 750 hours, and the
10-minute ping kept Scout awake around the clock). GitHub's own `schedule:` trigger only fires every
few hours when busy, so one short run per trigger is not enough either.

So one workflow run stays up for most of GitHub's 6-hour job limit: a tick on every 10-minute
boundary, and between ticks it long-polls Telegram so /today, Skip, Applied and the label buttons keep
working without the API. The workflow is triggered hourly with a concurrency group, so the next run
is already queued when this one ends. Overlap is still safe: finished slots are skipped and
notifications are claimed once.
"""

import asyncio
import time
from datetime import UTC, datetime

import structlog

from scout_core.config import get_settings
from scout_core.notify.telegram import TelegramClient

log = structlog.get_logger()
TICK_EVERY_S = 600


async def _poll_telegram(tg: TelegramClient, offset: int, wait_s: int) -> int:
    """Handle updates for up to wait_s seconds; returns the next offset."""
    from scout_api.routes.telegram import handle_update
    from scout_core.db.session import worker_session

    owner = get_settings().telegram_chat_id or ""
    try:
        updates = await tg.call("getUpdates", {"offset": offset, "timeout": max(1, min(wait_s, 15)),  # client timeout is 20 s
                                               "allowed_updates": ["message", "callback_query"]})
    except Exception as exc:  # noqa: BLE001 - a Telegram hiccup must not stop the schedule
        log.warning("telegram.poll_failed", error=str(exc)[:200])
        await asyncio.sleep(min(wait_s, 15))
        return offset
    for update in updates or []:
        offset = max(offset, int(update["update_id"]) + 1)
        try:
            async with worker_session() as session:
                await handle_update(session, tg, update, owner)
        except Exception as exc:  # noqa: BLE001
            log.warning("telegram.update_failed", error=f"{type(exc).__name__}: {exc}"[:200])
    return offset


async def run_loop(minutes: float, *, telegram: bool = True) -> int:
    """Tick every 10 minutes for `minutes`; returns how many ticks failed."""
    from scout_worker.work import run_tick

    deadline = time.monotonic() + minutes * 60
    tg = TelegramClient.from_settings() if telegram else None
    offset = 0
    if tg is not None:
        try:
            # Polling and a webhook can't both be active; the webhook pointed at the suspended API.
            await tg.call("deleteWebhook", {"drop_pending_updates": False})
        except Exception as exc:  # noqa: BLE001
            log.warning("telegram.delete_webhook_failed", error=str(exc)[:200])
            tg = None
    failures = 0
    try:
        while time.monotonic() < deadline:
            started = datetime.now(UTC)
            try:
                results, failed = await run_tick(skip={"discovery"})
                failures += int(failed)
                log.info("tick.done", at=started.isoformat(timespec="seconds"), failed=failed,
                         results={k: (v if isinstance(v, str) else "ok") for k, v in results.items()})
            except Exception as exc:  # noqa: BLE001 - e.g. the database briefly unreachable; try again next slot
                failures += 1
                log.error("tick.crashed", error=f"{type(exc).__name__}: {exc}"[:300])
            # Wait for the next 10-minute boundary, answering Telegram meanwhile.
            next_at = (int(time.time()) // TICK_EVERY_S + 1) * TICK_EVERY_S
            while (left := next_at - time.time()) > 1 and time.monotonic() < deadline:
                if tg is not None:
                    offset = await _poll_telegram(tg, offset, int(left))
                else:
                    await asyncio.sleep(min(left, 60))
    finally:
        if tg is not None:
            await tg.aclose()
    return failures

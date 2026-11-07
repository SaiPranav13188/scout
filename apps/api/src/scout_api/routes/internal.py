"""Reliable schedule (2026-11-07). GitHub Actions only ran the "every 10 minutes" workflow every few hours,
so a Postgres timer (Supabase pg_cron + pg_net) calls this endpoint every 10 minutes instead. It starts one
tick in the background and returns at once. The same call keeps the free Render instance awake, so
Telegram buttons and the dashboard respond without a cold start.

Authenticated by a shared secret header (TICK_SECRET); disabled when it isn't set. Overlap is safe:
finished schedule slots are skipped, notifications are claimed once, ingestion is idempotent.
"""

import asyncio
import hmac
from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Header, HTTPException, Response

from scout_core.config import get_settings

router = APIRouter(tags=["internal"])
log = structlog.get_logger()
_state: dict = {"task": None, "started_at": None, "last": None}


async def _run_tick() -> None:
    from scout_worker import work

    try:
        results, failed = await work.run_tick()
        _state["last"] = {"finished_at": datetime.now(UTC).isoformat(), "failed": failed,
                          "slots": [k for k in results if k != "instant_alerts"]}
    except Exception as exc:  # noqa: BLE001 - logged; each worker already records and alerts its own failures
        log.error("tick.failed", error=f"{type(exc).__name__}: {exc}"[:300])
        _state["last"] = {"finished_at": datetime.now(UTC).isoformat(), "failed": True, "error": str(exc)[:200]}


@router.post("/internal/tick", status_code=202)
async def tick(response: Response, secret: Annotated[str | None, Header(alias="X-Tick-Secret")] = None):
    expected = get_settings().tick_secret
    if not expected:
        raise HTTPException(404, "not found")
    if not secret or not hmac.compare_digest(secret, expected.get_secret_value()):
        raise HTTPException(403, "bad secret")
    task = _state["task"]
    if task is not None and not task.done():
        response.status_code = 200
        return {"started": False, "running_since": _state["started_at"], "last": _state["last"]}
    _state["task"] = asyncio.create_task(_run_tick())
    _state["started_at"] = datetime.now(UTC).isoformat()
    return {"started": True, "last": _state["last"]}

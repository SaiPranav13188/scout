"""Telegram webhook (spec §8). Telegram authenticates itself with the secret_token header set in
setWebhook; only my chat id is obeyed. Job text is never interpreted as commands.
"""

import hmac
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from scout_api.deps import db, webhook_limit
from scout_core.config import get_settings
from scout_core.notify.telegram import TelegramClient, format_job
from scout_core.services import digest_items, set_status, today_stats
from scout_core.settings_store import set_setting
from scout_core.types import ApplicationStatus

router = APIRouter(tags=["telegram"])

HELP = ("Commands:\n/today – today's top new jobs\n/stats – counts\n"
        "/pause – stop digests and ingestion\n/resume – start again")


@router.post("/telegram/webhook", dependencies=[Depends(webhook_limit)])
async def webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(db)],
    secret: Annotated[str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")] = None,
):
    s = get_settings()
    expected = s.telegram_webhook_secret.get_secret_value() if s.telegram_webhook_secret else None
    if not expected or not secret or not hmac.compare_digest(secret, expected):
        raise HTTPException(403, "bad secret")
    update: dict[str, Any] = await request.json()
    tg = TelegramClient.from_settings()
    if tg is None:
        return {"ok": True}
    try:
        await handle_update(session, tg, update, s.telegram_chat_id or "")
    finally:
        await tg.aclose()
    return {"ok": True}


async def handle_update(session: AsyncSession, tg: TelegramClient, update: dict, owner_chat: str) -> None:
    if cb := update.get("callback_query"):
        chat_id = str(((cb.get("message") or {}).get("chat") or {}).get("id", ""))
        if chat_id != owner_chat:
            return  # not me: ignore silently
        action, _, raw_id = (cb.get("data") or "").partition(":")
        status = {"skip": ApplicationStatus.SKIPPED, "applied": ApplicationStatus.APPLIED}.get(action)
        try:
            job_id = uuid.UUID(raw_id)
        except ValueError:
            return
        if status is None:
            return
        try:
            await set_status(session, job_id, status, actor="telegram")
            await tg.answer_callback(cb["id"], "Marked applied ✅" if status == ApplicationStatus.APPLIED else "Skipped")
        except LookupError:
            await tg.answer_callback(cb["id"], "That job no longer exists.")
        return

    msg = update.get("message") or {}
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    if chat_id != owner_chat:
        return
    command = (msg.get("text") or "").strip().split()[0].split("@")[0].lower() if msg.get("text") else ""
    if command == "/today":
        items, total = await digest_items(session, since=datetime.now(UTC) - timedelta(hours=24), limit=5)
        if not items:
            await tg.send(owner_chat, "No new fresher jobs in the last 24 hours yet.")
        for item in items:
            text, buttons = format_job(item)
            await tg.send(owner_chat, text, buttons)
    elif command == "/stats":
        st = await today_stats(session)
        await tg.send(owner_chat, (f"New (24h): {st['new_24h']}\nApplied (24h): {st['applied_24h']}\n"
                                   f"Follow-ups due: {st['follow_ups_due']}\nIn feed: {st['open_in_feed']}"))
    elif command in ("/pause", "/resume"):
        await set_setting(session, "paused", command == "/pause")
        await session.commit()
        await tg.send(owner_chat, "Paused. Send /resume to start again." if command == "/pause" else "Resumed.")
    else:
        await tg.send(owner_chat, HELP)

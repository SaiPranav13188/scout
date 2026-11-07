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
        data = cb.get("data") or ""
        if data.startswith("label:"):
            await save_label(session, tg, cb["id"], data)
            return
        action, _, raw_id = data.partition(":")
        status = {"skip": ApplicationStatus.SKIPPED, "applied": ApplicationStatus.APPLIED}.get(action)
        try:
            job_id = uuid.UUID(raw_id)
        except ValueError:
            return
        if action == "resume":
            await send_resume(session, tg, cb["id"], job_id, owner_chat)
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


async def send_resume(session: AsyncSession, tg: TelegramClient, callback_id: str, job_id: uuid.UUID,
                      chat: str) -> None:
    """The "Resume" button: send this job's tailored resume (made now if there isn't one yet)."""
    import html

    from scout_core.db.models import Job
    from scout_core.llm import Gemini, LLMError, LLMNotConfigured
    from scout_core.render_resume import render_pdf
    from scout_core.tailor_service import NoMasterResume, file_name, generate, latest_for_job

    job = await session.get(Job, job_id)
    if job is None:
        await tg.answer_callback(callback_id, "That job no longer exists.")
        return
    row = await latest_for_job(session, job_id)
    await tg.answer_callback(callback_id, "Sending your resume…" if row else "Tailoring your resume (about 20 s)…")
    if row is None:
        try:
            row = await generate(session, Gemini.from_settings(), job, actor="telegram")
            await session.commit()
        except (NoMasterResume, LLMNotConfigured, LLMError) as exc:
            await tg.send(chat, f"Couldn't make a resume for {html.escape(job.title)}: {html.escape(str(exc)[:200])}")
            return
    v = row.validation_result or {}
    status = ("✅ tailored, every claim checked against your master resume" if (row.content or {}).get("tailored")
              else "↩️ untailored master resume (tailoring didn't pass the fact check)")
    caption = f"<b>{html.escape(job.title)}</b> · {html.escape(job.company_name)}\n{status} · {v.get('pages', '?')} page"
    await tg.send_document(chat, file_name(row, job, "pdf"), render_pdf((row.content or {}).get("document") or {}), caption)


async def save_label(session: AsyncSession, tg: TelegramClient, callback_id: str, data: str) -> None:
    """👍 / 🤔 / 👎 under a job: my apply / maybe / no label (same as the dashboard's "Your call")."""
    from sqlalchemy import func, select

    from scout_core.db.models import Job, JobLabel
    from scout_core.runs import audit

    _, label, raw_id = (data.split(":", 2) + ["", ""])[:3]
    if label not in ("apply", "maybe", "no"):
        return
    try:
        job_id = uuid.UUID(raw_id)
    except ValueError:
        return
    if await session.get(Job, job_id) is None:
        await tg.answer_callback(callback_id, "That job no longer exists.")
        return
    row = await session.scalar(select(JobLabel).where(JobLabel.job_id == job_id))
    if row:
        row.label = label
    else:
        session.add(JobLabel(job_id=job_id, label=label))
    audit(session, actor="telegram", action="label_job", entity="job", entity_id=str(job_id), after={"label": label})
    await session.commit()
    total = await session.scalar(select(func.count()).select_from(JobLabel))
    word = {"apply": "👍 I'd apply", "maybe": "🤔 Maybe", "no": "👎 Not for me"}[label]
    await tg.answer_callback(callback_id, f"Saved: {word} · {total} of 40 labels")

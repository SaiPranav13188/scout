"""Telegram Bot API notifier (spec §8). Only the allow-listed chat id is ever messaged or obeyed."""

import html
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from scout_core.config import get_settings

API = "https://api.telegram.org/bot{token}/{method}"
MAX_TEXT = 4096  # Bot API limit for sendMessage text


class TelegramError(Exception):
    pass


class TelegramClient:
    def __init__(self, token: str, client: httpx.AsyncClient | None = None) -> None:
        self._token = token
        self._client = client or httpx.AsyncClient(timeout=20)

    @classmethod
    def from_settings(cls) -> "TelegramClient | None":
        s = get_settings()
        if not s.telegram_bot_token or not s.telegram_chat_id:
            return None
        return cls(s.telegram_bot_token.get_secret_value())

    async def call(self, method: str, payload: dict[str, Any]) -> Any:
        r = await self._client.post(API.format(token=self._token, method=method), json=payload)
        try:
            data = r.json()
        except ValueError as exc:
            raise TelegramError(f"{method}: HTTP {r.status_code}, non-JSON response") from exc
        if not data.get("ok"):
            # Never echo the token: the URL is not included in the message.
            raise TelegramError(f"{method}: {data.get('error_code')} {data.get('description')}")
        return data["result"]

    async def send(self, chat_id: str, text: str, buttons: list[list[dict]] | None = None) -> Any:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:MAX_TEXT],
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        return await self.call("sendMessage", payload)

    async def send_document(self, chat_id: str, filename: str, data: bytes, caption: str = "") -> Any:
        r = await self._client.post(API.format(token=self._token, method="sendDocument"),
                                    data={"chat_id": chat_id, "caption": caption[:1000], "parse_mode": "HTML"},
                                    files={"document": (filename, data, "application/pdf")}, timeout=60)
        data_ = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if not data_.get("ok"):
            raise TelegramError(f"sendDocument: {data_.get('error_code')} {data_.get('description')}")
        return data_["result"]

    async def answer_callback(self, callback_id: str, text: str) -> None:
        await self.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]})

    async def aclose(self) -> None:
        await self._client.aclose()


@dataclass
class DigestItem:
    job_id: str
    title: str
    company: str
    location: str | None
    salary_text: str | None
    posted_at: datetime | None
    fresher_confidence: str | None
    trust_level: str
    why: str
    apply_url: str


def _ago(dt: datetime | None, now: datetime) -> str:
    if not dt:
        return "posted date unknown"
    hours = int((now - dt).total_seconds() // 3600)
    if hours < 1:
        return "just now"
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


TRUST_ICON = {"verified": "✅", "unverified": "▫️", "caution": "⚠️", "scam_risk": "🚩"}


def format_job(item: DigestItem, now: datetime | None = None) -> tuple[str, list[list[dict]]]:
    now = now or datetime.now(UTC)
    e = html.escape
    lines = [
        f"<b>{e(item.title)}</b>",
        f"{e(item.company)} · {e(item.location or 'location not stated')}",
        f"{TRUST_ICON.get(item.trust_level, '')} {e(item.trust_level.replace('_', ' '))} · "
        f"fresher fit: {e(item.fresher_confidence or 'unknown')} · {_ago(item.posted_at, now)}",
    ]
    if item.salary_text:
        lines.append(f"💰 {e(item.salary_text)}")
    if item.why:
        lines.append(f"<i>{e(item.why)}</i>")
    buttons = [[
        {"text": "Open job", "url": item.apply_url},
        {"text": "Resume", "callback_data": f"resume:{item.job_id}"},
    ], [
        {"text": "Skip", "callback_data": f"skip:{item.job_id}"},
        {"text": "Applied", "callback_data": f"applied:{item.job_id}"},
    ], [
        # One-tap labels for the matching eval (would I apply?).
        {"text": "👍 I'd apply", "callback_data": f"label:apply:{item.job_id}"},
        {"text": "🤔 Maybe", "callback_data": f"label:maybe:{item.job_id}"},
        {"text": "👎 Not for me", "callback_data": f"label:no:{item.job_id}"},
    ]]
    return "\n".join(lines), buttons


def digest_header(count: int, total_new: int, now: datetime | None = None) -> str:
    if count == 0:
        return "☀️ <b>Morning digest</b>\nNo new fresher jobs matched since the last digest."
    more = f" (showing top {count} of {total_new})" if total_new > count else ""
    return f"☀️ <b>Morning digest</b>\n{total_new} new fresher job{'s' if total_new != 1 else ''}{more}."

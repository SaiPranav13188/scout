"""Read-only Gmail access for job-alert emails (spec §5.1 item 5).

Scope: gmail.readonly only. The OAuth client config and refresh token are stored encrypted in
`secrets_encrypted`; nothing is written to disk. Email content is untrusted data: it is parsed by
plain code and never treated as instructions.
"""

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from scout_core.secrets import get_secret, put_secret

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
API = "https://gmail.googleapis.com/gmail/v1/users/me"
# Names of rows in secrets_encrypted (not secret values).
SECRET_CLIENT = "gmail_client"  # noqa: S105
SECRET_REFRESH = "gmail_refresh_token"  # noqa: S105


class GmailNotConnected(Exception):
    pass


class GmailAuthExpired(Exception):
    """Google rejected the stored refresh token (revoked, or expired while the app is in Testing)."""


@dataclass
class EmailMessage:
    id: str
    thread_id: str
    received_at: datetime
    sender: str
    subject: str
    html: str | None
    text: str | None


def connect_interactive(client_config: dict) -> tuple[str, str]:
    """Open the browser consent screen and return (refresh_token, gmail_address)."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True, prompt="consent", access_type="offline",
                                  authorization_prompt_message="Opening Google sign-in in your browser…",
                                  success_message="Scout is connected to Gmail (read-only). You can close this tab.")
    if not creds.refresh_token:
        raise GmailNotConnected("Google returned no refresh token")
    r = httpx.get(f"{API}/profile", headers={"Authorization": f"Bearer {creds.token}"}, timeout=20)
    r.raise_for_status()
    return creds.refresh_token, r.json()["emailAddress"]


async def save_connection(session: AsyncSession, client_config: dict, refresh_token: str) -> None:
    await put_secret(session, SECRET_CLIENT, json.dumps(client_config))
    await put_secret(session, SECRET_REFRESH, refresh_token)
    await session.commit()


class GmailClient:
    def __init__(self, access_token: str, http: httpx.AsyncClient | None = None) -> None:
        self._http = http or httpx.AsyncClient(timeout=30)
        self._headers = {"Authorization": f"Bearer {access_token}"}

    @classmethod
    async def from_db(cls, session: AsyncSession) -> "GmailClient":
        client_json = await get_secret(session, SECRET_CLIENT)
        refresh = await get_secret(session, SECRET_REFRESH)
        if not client_json or not refresh:
            raise GmailNotConnected("Gmail is not connected (run: python -m scout_worker.cli gmail-connect)")
        cfg = json.loads(client_json)
        cfg = cfg.get("installed") or cfg.get("web") or cfg
        async with httpx.AsyncClient(timeout=20) as http:
            r = await http.post(cfg.get("token_uri", "https://oauth2.googleapis.com/token"), data={
                "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
                "refresh_token": refresh, "grant_type": "refresh_token"})
        if r.status_code == 400 and r.json().get("error") == "invalid_grant":
            raise GmailAuthExpired("Google rejected the Gmail token; reconnect with gmail-connect")
        r.raise_for_status()
        return cls(r.json()["access_token"])

    async def search(self, query: str, max_results: int = 100) -> list[str]:
        ids: list[str] = []
        page: str | None = None
        while len(ids) < max_results:
            params = {"q": query, "maxResults": min(100, max_results - len(ids))}
            if page:
                params["pageToken"] = page
            r = await self._http.get(f"{API}/messages", headers=self._headers, params=params)
            r.raise_for_status()
            data = r.json()
            ids += [m["id"] for m in data.get("messages", [])]
            page = data.get("nextPageToken")
            if not page:
                break
        return ids

    async def get(self, message_id: str) -> EmailMessage:
        r = await self._http.get(f"{API}/messages/{message_id}", headers=self._headers, params={"format": "full"})
        r.raise_for_status()
        return parse_message(r.json())

    async def headers_only(self, message_id: str) -> dict[str, str]:
        r = await self._http.get(f"{API}/messages/{message_id}", headers=self._headers,
                                 params={"format": "metadata", "metadataHeaders": ["From", "Subject"]})
        r.raise_for_status()
        return {h["name"].lower(): h["value"] for h in r.json()["payload"].get("headers", [])}

    async def aclose(self) -> None:
        await self._http.aclose()


def _decode(data: str | None) -> str | None:
    if not data:
        return None
    text = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n")  # Gmail bodies use CRLF; parsers expect \n


def parse_message(raw: dict) -> EmailMessage:
    payload = raw.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    html = text = None
    stack = [payload]
    while stack:
        part = stack.pop()
        mime = part.get("mimeType", "")
        body = (part.get("body") or {}).get("data")
        if mime == "text/html" and html is None:
            html = _decode(body)
        elif mime == "text/plain" and text is None:
            text = _decode(body)
        stack.extend(part.get("parts", []) or [])
    received = datetime.fromtimestamp(int(raw.get("internalDate", "0")) / 1000, UTC)
    return EmailMessage(raw["id"], raw.get("threadId", ""), received, headers.get("from", ""),
                        headers.get("subject", ""), html, text)

"""Polite outbound HTTP: honest user agent, per-host pacing, retries with backoff, Retry-After."""

import asyncio
import random
import time
from collections import defaultdict
from urllib.parse import urlsplit

import httpx
import structlog

from scout_core.config import get_settings

log = structlog.get_logger()

RETRY_STATUSES = {429, 500, 502, 503, 504}


class PoliteClient:
    """Wraps httpx.AsyncClient. One request at a time per host, min interval between them."""

    def __init__(self, min_interval_per_host: float = 0.5, max_retries: int = 3,
                 client: httpx.AsyncClient | None = None) -> None:
        s = get_settings()
        self._client = client or httpx.AsyncClient(
            timeout=s.http_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": s.http_user_agent, "Accept": "application/json, text/html;q=0.9"},
        )
        self._min_interval = min_interval_per_host
        self._max_retries = max_retries
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last: dict[str, float] = {}

    @property
    def raw(self) -> httpx.AsyncClient:
        return self._client

    async def get(self, url: str, **kwargs) -> httpx.Response:
        host = urlsplit(url).netloc
        attempt = 0
        while True:
            async with self._locks[host]:
                wait = self._min_interval - (time.monotonic() - self._last.get(host, 0))
                if wait > 0:
                    await asyncio.sleep(wait)
                try:
                    response = await self._client.get(url, **kwargs)
                    error: Exception | None = None
                except httpx.TransportError as exc:
                    response, error = None, exc
                finally:
                    self._last[host] = time.monotonic()
            if isinstance(error, httpx.ConnectError):
                # DNS failures and refused connections rarely heal within seconds: retry once at most.
                retryable = attempt == 0
            else:
                retryable = error is not None or (response is not None and response.status_code in RETRY_STATUSES)
            if not retryable or attempt >= self._max_retries:
                if error is not None:
                    raise error
                assert response is not None
                return response
            attempt += 1
            delay = _retry_after(response) if response is not None else None
            delay = delay if delay is not None else min(30.0, 2 ** attempt + random.random())
            log.info("http.retry", url=url, attempt=attempt, delay=round(delay, 1),
                     status=response.status_code if response is not None else None, error=str(error or ""))
            await asyncio.sleep(delay)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "PoliteClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return min(120.0, max(0.0, float(value)))
    except ValueError:
        return None

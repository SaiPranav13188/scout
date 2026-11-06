import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator

from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from scout_core.db.session import get_sessionmaker


async def db() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session


class RateLimiter:
    """Sliding-window limit per client. In-memory is enough: there is one user and one API process."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit, self.window = limit, window_seconds
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def __call__(self, request: Request) -> None:
        key = request.client.host if request.client else "unknown"
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "slow down")
        q.append(now)


api_limit = RateLimiter(limit=240, window_seconds=60)
webhook_limit = RateLimiter(limit=60, window_seconds=60)

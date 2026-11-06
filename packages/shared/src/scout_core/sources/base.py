"""Source plug-in interface. Each source fetches raw payloads and parses them into NormalizedJob."""

from abc import ABC, abstractmethod
from datetime import UTC, datetime

from scout_core.http import PoliteClient
from scout_core.types import NormalizedJob, RawFetch


class SourceError(Exception):
    """A source could not be fetched or its response was not what the docs describe."""


class BoardNotFound(SourceError):
    """The board token does not exist (404). The watchlist entry should be deactivated."""


class JobSource(ABC):
    name: str

    @abstractmethod
    async def fetch(self, client: PoliteClient, board_token: str) -> RawFetch: ...

    @abstractmethod
    def parse(self, raw: RawFetch) -> list[NormalizedJob]: ...

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

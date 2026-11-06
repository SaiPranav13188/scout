"""Raw payload store (spec §10, MongoDB). Raw data only; nothing the UI shows is read from here.

MongoRawStore is used whenever MONGO_URL is set. Without it (local development without Docker),
FileRawStore writes gzip JSON lines under .devdata/raw so raw payloads are still kept.
"""

import gzip
import json
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scout_core.config import get_settings


class RawStore(ABC):
    @abstractmethod
    async def save_job_payload(self, *, source: str, board_token: str | None, url: str,
                               fetched_at: datetime, payload: Any, parsed_ok: bool) -> None: ...

    @abstractmethod
    async def save_trace(self, doc: dict[str, Any]) -> None: ...

    async def save_alert_email(self, doc: dict[str, Any]) -> None:
        """raw_alert_emails (spec §10): gmail id, received_at, sender, parsed items, parse errors."""
        await self.save_trace({"kind": "raw_alert_email", **doc})

    async def close(self) -> None:  # noqa: B027 - optional hook
        pass


class MongoRawStore(RawStore):
    def __init__(self, url: str, db: str) -> None:
        from motor.motor_asyncio import AsyncIOMotorClient

        self._client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=5000)
        self._db = self._client[db]

    async def ensure_indexes(self) -> None:
        await self._db.raw_jobs.create_index([("source", 1), ("fetched_at", -1)])
        await self._db.raw_alert_emails.create_index("gmail_message_id", unique=True)
        await self._db.agent_traces.create_index([("run_id", 1), ("node", 1)])
        await self._db.agent_traces.create_index("created_at", expireAfterSeconds=90 * 24 * 3600)

    async def save_job_payload(self, *, source, board_token, url, fetched_at, payload, parsed_ok) -> None:
        await self._db.raw_jobs.insert_one({"source": source, "board_token": board_token, "url": url,
                                            "fetched_at": fetched_at, "payload": payload,
                                            "parsed_ok": parsed_ok})

    async def save_trace(self, doc) -> None:
        await self._db.agent_traces.insert_one({**doc, "created_at": datetime.now(UTC)})

    async def save_alert_email(self, doc) -> None:
        await self._db.raw_alert_emails.update_one({"gmail_message_id": doc["gmail_message_id"]},
                                                   {"$set": doc}, upsert=True)

    async def close(self) -> None:
        self._client.close()


class FileRawStore(RawStore):
    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _append(self, name: str, doc: dict[str, Any]) -> None:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        with gzip.open(self._root / f"{name}-{day}.jsonl.gz", "at", encoding="utf-8") as f:
            f.write(json.dumps(doc, default=str) + "\n")

    async def save_job_payload(self, *, source, board_token, url, fetched_at, payload, parsed_ok) -> None:
        self._append("raw_jobs", {"source": source, "board_token": board_token, "url": url,
                                  "fetched_at": fetched_at, "payload": payload, "parsed_ok": parsed_ok})

    async def save_trace(self, doc) -> None:
        self._append("agent_traces", doc)


def make_raw_store() -> RawStore:
    s = get_settings()
    if s.mongo_url:
        return MongoRawStore(s.mongo_url, s.mongo_db)
    return FileRawStore(Path(".devdata") / "raw")

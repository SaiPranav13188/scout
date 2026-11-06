"""Run any job once from the command line (no Redis needed). Useful for setup and debugging.

  uv run python -m scout_worker.cli discovery
  uv run python -m scout_worker.cli ingest
  uv run python -m scout_worker.cli add-board lever fampay "FamPay" https://famapp.in
  uv run python -m scout_worker.cli digest
  uv run python -m scout_worker.cli tick                     # run everything that is due (cloud cron)
  uv run python -m scout_worker.cli tick --skip discovery
  uv run python -m scout_worker.cli tick --only discovery
  uv run python -m scout_worker.cli gmail-connect            # one-time Google sign-in (read-only)
  uv run python -m scout_worker.cli gmail-senders            # which job-alert senders are in the inbox
"""

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from scout_core.db.models import CompanyWatchlist
from scout_core.db.session import worker_session
from scout_core.http import PoliteClient
from scout_core.pipeline.ingest import get_or_create_company
from scout_core.sources.ats import SOURCES
from scout_core.types import AtsType
from scout_worker import work


async def add_board(ats: str, token: str, name: str, website: str | None) -> None:
    async with PoliteClient() as client:
        raw = await SOURCES[ats].fetch(client, token)  # confirm the board really exists
    count = len(raw.payload) if isinstance(raw.payload, list) else len(raw.payload.get("jobs", []))
    async with worker_session() as s:
        company = await get_or_create_company(s, name=name, website=website, source="manual")
        s.add(CompanyWatchlist(company_id=company.id, ats_type=AtsType(ats), board_token=token,
                               discovery_method="manual", evidence_url=raw.url))
        await s.commit()
    print(f"added {ats}/{token} ({count} jobs listed right now)")


async def gmail_connect(config: dict) -> None:
    from scout_core.gmail import connect_interactive, save_connection

    refresh, email = await asyncio.to_thread(connect_interactive, config)
    async with worker_session() as s:
        await save_connection(s, config, refresh)
    print(f"Connected to Gmail as {email} (read-only). Token stored encrypted in the database.")


ALERT_QUERY = ("newer_than:60d (from:linkedin.com OR from:wellfound.com OR from:angel.co OR "
               "from:internshala.com OR from:instahyre.com OR from:cutshort.io OR from:naukri.com OR "
               "from:ycombinator.com OR from:workatastartup.com)")


async def gmail_senders() -> None:
    """Counts of job-alert senders (From headers only) to decide which parsers to build."""
    from collections import Counter

    from scout_core.gmail import GmailClient

    async with worker_session() as s:
        gm = await GmailClient.from_db(s)
    try:
        ids = await gm.search(ALERT_QUERY, max_results=300)
        counts: Counter[str] = Counter()
        for mid in ids:
            h = await gm.headers_only(mid)
            counts[h.get("from", "?")] += 1
    finally:
        await gm.aclose()
    print(f"{len(ids)} messages from job sites in the last 60 days:")
    for sender, n in counts.most_common(30):
        print(f"  {n:4d}  {sender}")


def main() -> None:
    cmd, *args = sys.argv[1:] or ["help"]
    stamp = f"manual:{datetime.now(UTC).isoformat(timespec='seconds')}"
    if cmd == "ingest":
        print(json.dumps(asyncio.run(work.run_ingest(stamp)), indent=2))
    elif cmd == "verify":
        print(json.dumps(asyncio.run(work.run_verify(stamp)), indent=2))
    elif cmd == "discovery":
        print(json.dumps(asyncio.run(work.run_discovery(stamp)), indent=2))
    elif cmd == "digest":
        print(json.dumps(asyncio.run(work.run_daily_digest(stamp)), indent=2))
    elif cmd == "tick":
        only = {args[args.index("--only") + 1]} if "--only" in args else None
        skip = {args[args.index("--skip") + 1]} if "--skip" in args else None
        results, failed = asyncio.run(work.run_tick(only=only, skip=skip))
        print(json.dumps(results, indent=2, default=str))
        sys.exit(1 if failed else 0)
    elif cmd == "alerts":
        print(json.dumps(asyncio.run(work.run_instant_alerts()), indent=2, default=str))
    elif cmd == "gmail-connect":
        asyncio.run(gmail_connect(json.loads(Path(args[0] if args else "gmail_client.json").read_text())))
    elif cmd == "gmail-senders":
        asyncio.run(gmail_senders())
    elif cmd == "add-board" and len(args) >= 3:
        asyncio.run(add_board(args[0], args[1], args[2], args[3] if len(args) > 3 else None))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()

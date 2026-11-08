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
  uv run python -m scout_worker.cli resume-parse private/resume.pdf
  uv run python -m scout_worker.cli score 10                 # score up to 10 feed jobs now
  uv run python -m scout_worker.cli resume-backfill private/resume.pdf [--apply]  # one-time upgrade
  uv run python -m scout_worker.cli extension-build --last-name Bandaru [--pdf PATH] [--file-name NAME]  # personal extension copy in private/
  uv run python -m scout_worker.cli setup-timer                # Supabase timer: run Scout every 10 min
  uv run python -m scout_worker.cli add-india-boards          # India hiring companies' public boards
  uv run python -m scout_worker.cli seniority-check 40       # Gemini reads "experience unclear" jobs
  uv run python -m scout_worker.cli recheck-feed [--apply]  # re-run the fresher filter on feed jobs
  uv run python -m scout_worker.cli tailor-eval 6              # Phase 4 eval on real jobs (stores nothing)
  uv run python -m scout_worker.cli fresher-eval [--heldout] [--rules-only]  # labelled fresher/experienced postings
  uv run python -m scout_worker.cli match-eval [--maybe-is-apply]  # match score vs my labels (read-only)
"""

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from scout_core.config import get_settings
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


async def add_india_boards() -> None:
    """Add the checked-in India board list (scout_core.seeds_india); each board is confirmed live first."""
    from sqlalchemy import func, select

    from scout_core.seeds_india import INDIA_BOARDS

    added = skipped = dead = 0
    async with PoliteClient(max_retries=1) as client:
        for name, ats, token in INDIA_BOARDS:
            async with worker_session() as s:
                exists = await s.scalar(select(CompanyWatchlist).where(
                    CompanyWatchlist.ats_type == AtsType(ats), func.lower(CompanyWatchlist.board_token) == token.lower()))
                if exists:
                    skipped += 1
                    continue
                try:
                    raw = await SOURCES[ats].fetch(client, token)
                except Exception as exc:  # noqa: BLE001 - a dead board is skipped, not fatal
                    print(f"  skip {name} ({ats}/{token}): {exc}")
                    dead += 1
                    continue
                company = await get_or_create_company(s, name=name, website=None, source="india_list")
                s.add(CompanyWatchlist(company_id=company.id, ats_type=AtsType(ats), board_token=token,
                                       discovery_method="india_list", evidence_url=raw.url))
                await s.commit()
                added += 1
    print(f"added {added} boards, {skipped} already watched, {dead} not reachable")


async def _active_resume_row():
    from scout_core.resume_store import active_resume

    async with worker_session() as s:
        return await active_resume(s)


def upload_name(pdf: Path, full_name: str | None, explicit: str | None = None) -> str:
    """The file name employers see when the extension attaches my resume: --file-name if given, otherwise
    the PDF's own name (as I saved it), or "<Full_Name>_Resume.pdf" for the generic private/resume.pdf."""
    name = (explicit or "").strip() or (pdf.name if pdf.name.lower() != "resume.pdf" else
                                         f"{(full_name or 'Resume').replace(' ', '_')}_Resume.pdf")
    return name if name.lower().endswith(".pdf") else name + ".pdf"


def extension_build(out_dir: str, last_name: str | None, city: str | None, pdf: str | None,
                    file_name: str | None = None) -> None:
    """A personal copy of the Chrome extension with my profile and resume already inside (private/, never committed)."""
    import shutil

    from scout_core.autofill import build_profile

    row = asyncio.run(_active_resume_row())
    if row is None:
        sys.exit("No resume in Scout yet.")
    root = Path(__file__).resolve().parents[4]
    out = Path(out_dir).resolve()
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(root / "apps" / "extension", out, ignore=shutil.ignore_patterns("test", "README.md"))
    (out / "preset").mkdir()
    profile = build_profile(row, last_name=last_name, city=city)
    resume = Path(pdf) if pdf else root / "private" / "resume.pdf"
    if resume.exists():
        shutil.copy2(resume, out / "preset" / "resume.pdf")
        profile["resume_file_name"] = upload_name(resume, profile.get("full_name"), file_name)
    answers_file = root / "private" / "autofill_answers.json"
    if answers_file.exists():
        profile["answers"] = json.loads(answers_file.read_text())
        if profile.get("gpa") and profile["answers"].get("btech_cgpa"):
            profile["gpa"] = profile["answers"]["btech_cgpa"]
    from datetime import UTC
    from datetime import datetime as _dt
    profile["built_at"] = _dt.now(UTC).isoformat(timespec="seconds")
    (out / "preset" / "profile.json").write_text(json.dumps(profile, indent=2))
    print(f"Built {out}\n  profile: {profile['full_name']} · {profile['email']} · {profile['school']}"
          f"\n  resume: {('attached as ' + profile['resume_file_name']) if resume.exists() else 'none'}"
          f"\n  saved answers: {len(profile.get('answers') or {})}"
          "\nIn Chrome: chrome://extensions → Developer mode → Load unpacked → choose that folder.")


# The command the timer runs; format() quotes the URL and secret as SQL literals (%L).
TIMER_SQL = ("select format('select net.http_post(url := %L, headers := jsonb_build_object(''X-Tick-Secret'', %L, "
             "''Content-Type'', ''application/json''), body := cast(''{}'' as jsonb), timeout_milliseconds := 60000)', cast(:u as text), cast(:k as text))")


async def setup_timer(api_url: str) -> None:
    """Supabase pg_cron + pg_net: call the API's /internal/tick every 10 minutes (GitHub's schedule is too slow).

    Safe to run again: it replaces the existing 'scout-tick' timer. `--remove` deletes it.
    """
    from sqlalchemy import text

    secret = get_settings().tick_secret
    if not secret:
        sys.exit("TICK_SECRET is not set in .env")
    async with worker_session() as s:
        await s.execute(text("create extension if not exists pg_cron"))
        await s.execute(text("create extension if not exists pg_net"))
        command = (await s.execute(text(TIMER_SQL), {"u": api_url.rstrip("/") + "/internal/tick",
                                                    "k": secret.get_secret_value()})).scalar()
        await s.execute(text("select cron.schedule('scout-tick', '*/10 * * * *', :c)"), {"c": command})
        await s.commit()
        jobs = (await s.execute(text("select jobname, schedule, active from cron.job where jobname = 'scout-tick'"))).all()
    print(f"Timer set: {jobs}. Scout now runs every 10 minutes via {api_url}.")


async def remove_timer() -> None:
    from sqlalchemy import text

    async with worker_session() as s:
        await s.execute(text("select cron.unschedule('scout-tick') where exists (select 1 from cron.job where jobname = 'scout-tick')"))
        await s.commit()
    print("Timer removed.")


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
    elif cmd == "resume-parse":
        print(json.dumps(asyncio.run(work.save_master_resume(args[0] if args else "private/resume.pdf")),
                         indent=2, default=str))
    elif cmd == "score":
        print(json.dumps(asyncio.run(work.run_scoring(stamp, limit=int(args[0]) if args else 25)), indent=2))
    elif cmd == "resume-backfill":
        pdf = next((a for a in args if not a.startswith("--")), "private/resume.pdf")
        if not Path(pdf).is_file():
            sys.exit(f"No file at {Path(pdf).resolve()}.\nRun this from the scout folder (cd to it first), "
                     "or give the full path to your resume PDF.")
        print(json.dumps(asyncio.run(work.resume_backfill(pdf, apply="--apply" in args)), indent=2, default=str))
    elif cmd == "extension-build":
        opt = lambda flag: args[args.index(flag) + 1] if flag in args else None  # noqa: E731
        extension_build(opt("--out") or "private/scout-autofill", opt("--last-name"), opt("--city"), opt("--pdf"),
                        opt("--file-name"))
    elif cmd == "setup-timer":
        if "--remove" in args:
            asyncio.run(remove_timer())
        else:
            asyncio.run(setup_timer(next((a for a in args if a.startswith("http")), "https://scout-api-bxpx.onrender.com")))
    elif cmd == "add-india-boards":
        asyncio.run(add_india_boards())
    elif cmd == "seniority-check":
        from scout_core.llm import Gemini
        print(json.dumps(asyncio.run(work.check_unclear_experience(
            Gemini.from_settings(), limit=int(args[0]) if args else 15)), indent=2))
    elif cmd == "recheck-feed":
        print(json.dumps(asyncio.run(work.recheck_feed(apply="--apply" in args)), indent=2))
    elif cmd == "tailor-eval":
        print(json.dumps(asyncio.run(work.tailor_eval(int(args[0]) if args else 6)), indent=2))
    elif cmd == "fresher-eval":
        from scout_core.fresher_eval import DATA, HELDOUT, report, run
        llm = None
        if "--rules-only" not in args:
            from scout_core.llm import Gemini
            llm = Gemini.from_settings()
        path = HELDOUT if "--heldout" in args else DATA
        print(report(asyncio.run(run(llm, delay=1.0, path=path))))
    elif cmd == "match-eval":
        from scout_core.match_eval import to_markdown
        print(to_markdown(asyncio.run(work.match_eval("--maybe-is-apply" in args))))
    elif cmd == "add-board" and len(args) >= 3:
        asyncio.run(add_board(args[0], args[1], args[2], args[3] if len(args) > 3 else None))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()

# ruff: noqa: S603, S607, S106, S608 - a local test stack: fixed commands (uv, npx, cp) and fake test data only
"""The isolated stack behind the dashboard end-to-end tests (Playwright Test: apps/web/e2e/*.spec.ts).

Never touches real data. Builds and serves:
  Postgres database "scout_e2e" (embedded pgserver, or E2E_DATABASE_URL in CI) -> migrations -> seed data
  -> API on :8021 (dev token) -> dashboard dev server on :3121 (from a temporary copy of apps/web,
     so it can run next to your own `next dev`)
and keeps running until stopped. Playwright starts it as its webServer (playwright.config.ts):

  cd apps/web && npx playwright test
"""

import asyncio
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "apps" / "web"
API_PORT, WEB_PORT, TOKEN = 8021, 3121, "e2e-dev-token"


def wait_http(url: str, headers: dict | None = None, timeout: float = 120) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if httpx.get(url, headers=headers, timeout=5).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError(f"{url} did not come up")


async def seed(db_url: str) -> None:
    os.environ["DATABASE_URL"] = db_url
    from sqlalchemy import select

    from scout_core.db.models import AgentRun, Application, Job, MasterResume, ResumeGenerated
    from scout_core.db.session import worker_session
    from scout_core.filters.fresher import FresherRules
    from scout_core.filters.relevance import RelevanceRules
    from scout_core.pipeline.evaluate import Preferences
    from scout_core.pipeline.ingest import get_or_create_company, store_jobs
    from scout_core.render_resume import ats_text_check, fit_one_page, page_count
    from scout_core.resume import Contact, Project, ResumeData, SkillGroup
    from scout_core.services import set_status
    from scout_core.tailor import untailored, validate
    from scout_core.types import ApplicationStatus, NormalizedJob

    source = ("Jane Example\nSkills: Python, FastAPI, Qdrant, LangGraph\nNagrik: built a 6-agent pipeline with a 40-case eval.\n"
              "Example Institute of Technology, B.Tech, 2022 - 2026, CGPA 8.3")
    master = ResumeData(skills=[SkillGroup(category="AI", items=["Python", "FastAPI", "Qdrant", "LangGraph"])],
                        projects=[Project(name="Nagrik", tech=["Python"], bullets=["Built a 6-agent pipeline with a 40-case eval."])])
    contact = Contact(name="Jane Example", emails=["jane@example.com"], links=["https://linkedin.com/in/jane"])
    async with worker_session() as s:
        s.add(MasterResume(file_url="local:resume.pdf", version=1, is_active=True, parsed={
            **master.model_dump(), "contact": contact.model_dump(), "fact_check": {"ok": True, "unsupported": []},
            "source_text": source, "reviewed": False}))
        co = await get_or_create_company(s, name="Acme AI", website="https://acme.ai", source="test")
        desc = "Open to freshers (2026 batch). Build LLM agents and RAG pipelines in Python and FastAPI. " * 4
        titles = ["AI Engineer", "GenAI Engineer - New Grad", "Machine Learning Engineer (Fresher)"]
        await store_jobs(s, [NormalizedJob(source="lever", source_job_id=str(i), company_name="Acme AI", title=t,
                                           description_text=desc, locations=["Bengaluru"], apply_url=f"https://jobs.lever.co/acme/{i}",
                                           board_token="acme", posted_at=datetime.now(UTC) - timedelta(hours=2))
                             for i, t in enumerate(titles)],
                         company=co, fresher_rules=FresherRules(second_reading=False), relevance_rules=RelevanceRules(), prefs=Preferences())
        await s.commit()
        jobs = {j.title: j for j in (await s.scalars(select(Job))).all()}
        await set_status(s, jobs["GenAI Engineer - New Grad"].id, ApplicationStatus.APPLIED, actor="me")
        app = await s.scalar(select(Application))
        app.follow_up_at = datetime.now(UTC) - timedelta(hours=1)
        doc, pdf, dropped = fit_one_page(untailored(master, contact))
        mr = await s.scalar(select(MasterResume))
        s.add(ResumeGenerated(job_id=jobs["AI Engineer"].id, master_resume_id=mr.id, file_url_pdf="render:pdf",
                              content={"document": doc, "tailored": True, "emphasis": ["Agents first"], "master_version": 1},
                              validation_result={**validate(doc, master, source), "pages": page_count(pdf),
                                                 "ats": ats_text_check(pdf, doc), "dropped_to_fit": dropped, "passed": True}))
        now = datetime.now(UTC)
        s.add(AgentRun(worker="ingest", task_key="ingest:e2e", status="success", started_at=now, finished_at=now, counts={"fetched": 3}))
        await s.commit()


def _db_url() -> str:
    if url := os.environ.get("E2E_DATABASE_URL"):
        return url
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "devdb.py"), "--db", "scout_e2e"],
                          capture_output=True, text=True, check=True).stdout.strip().splitlines()[-1]


def _reset(db_url: str) -> None:
    """Start from empty tables, so every run sees exactly the seed data."""
    from sqlalchemy import text

    from scout_core.db.session import make_engine

    async def go():
        engine = make_engine(db_url, pooled=False)
        async with engine.begin() as conn:
            tables = (await conn.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'"))).scalars().all()
            await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
        await engine.dispose()
    asyncio.run(go())


def main() -> int:
    db_url = _db_url()
    env = {**os.environ, "DATABASE_URL": db_url}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT / "packages" / "shared", env=env,
                   check=True, capture_output=True)
    _reset(db_url)
    asyncio.run(seed(db_url))
    tmp = Path(tempfile.mkdtemp(prefix="scout-e2e-"))
    procs: list[subprocess.Popen] = []
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(now=True))
    try:
        api_env = {**env, "ENV": "development", "DEV_AUTH_TOKEN": TOKEN, "CORS_ORIGINS": f"http://localhost:{WEB_PORT}",
                   "GEMINI_API_KEY": "", "TELEGRAM_BOT_TOKEN": "", "LANGFUSE_PUBLIC_KEY": ""}
        procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "scout_api.main:app", "--port", str(API_PORT)],
                                      cwd=ROOT / "apps" / "api", env=api_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        web_dir = WEB if os.environ.get("CI") else tmp  # CI: nothing else runs, so serve apps/web in place
        if web_dir is tmp:
            for item in ("src", "public", "package.json", "tsconfig.json", "next.config.ts", "components.json",
                         "next-env.d.ts", "node_modules"):
                src = WEB / item
                if not src.exists():
                    continue
                if subprocess.run(["cp", "-Rc", str(src), str(tmp)], capture_output=True).returncode != 0:  # APFS clone
                    (shutil.copytree if src.is_dir() else shutil.copy2)(src, tmp / item)
        web_env = {**os.environ, "NEXT_PUBLIC_API_URL": f"http://localhost:{API_PORT}", "NEXT_PUBLIC_DEV_AUTH_TOKEN": TOKEN,
                   "NEXT_PUBLIC_SUPABASE_URL": ""}
        procs.append(subprocess.Popen(["npx", "next", "dev", "-p", str(WEB_PORT)], cwd=web_dir, env=web_env,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        wait_http(f"http://localhost:{API_PORT}/healthz")
        print(f"e2e stack ready: dashboard http://localhost:{WEB_PORT}, API http://localhost:{API_PORT}", flush=True)
        while not stop["now"] and all(p.poll() is None for p in procs):
            time.sleep(0.5)
        return 0 if stop["now"] else 1  # a server died on its own: fail loudly
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

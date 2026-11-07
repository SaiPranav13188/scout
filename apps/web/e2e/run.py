"""Dashboard end-to-end test (spec §4 "Playwright Test for the dashboard").

Starts everything it needs on its own, never touching real data:
  embedded Postgres (database "scout_e2e") -> migrations -> seed data
  -> API on :8021 (dev token) -> dashboard dev server on :3121 (from a temporary copy of apps/web,
     so it can run next to your own `next dev`)
then drives a real Chromium through Today, the job panel, Pipeline, Resume, Agent health and Settings.

  uv run --with playwright python apps/web/e2e/run.py [path-to-chromium]
"""

import asyncio
import json
import os
import shutil
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
RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, what: str) -> None:
    RESULTS.append((bool(ok), what))
    print(("PASS " if ok else "FAIL ") + what, flush=True)


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
                         company=co, fresher_rules=FresherRules(), relevance_rules=RelevanceRules(), prefs=Preferences())
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


def drive(chromium: str | None) -> None:
    from playwright.sync_api import sync_playwright

    base = f"http://localhost:{WEB_PORT}"
    errors: list[str] = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=chromium) if chromium else p.chromium.launch()
        pg = b.new_context(viewport={"width": 1400, "height": 1000}, accept_downloads=True).new_page()
        pg.on("pageerror", lambda e: errors.append(str(e)))

        pg.goto(base + "/")
        pg.get_by_text("AI Engineer", exact=True).first.wait_for(timeout=60000)
        check(pg.get_by_text("Machine Learning Engineer (Fresher)").count() > 0, "Today lists confirmed fresher AI jobs")
        pg.get_by_text("AI Engineer", exact=True).first.click()
        pg.get_by_text("Tailored resume", exact=True).wait_for(timeout=15000)
        with pg.expect_download() as dl:
            pg.get_by_role("button", name="PDF").first.click()
        check(dl.value.suggested_filename.endswith(".pdf"), f"tailored resume downloads ({dl.value.suggested_filename})")
        check(pg.get_by_role("button", name="Draft messages").count() == 1, "message drafts offered in the job panel")
        pg.keyboard.press("Escape")

        pg.goto(base + "/pipeline")
        pg.get_by_text("follow-up is due").wait_for(timeout=30000)
        pg.get_by_label("Status of GenAI Engineer - New Grad").select_option("interview")
        pg.wait_for_timeout(1500)
        check("GenAI Engineer" in pg.get_by_role("region", name="Interview").inner_text(), "Pipeline: move to Interview")
        apps = httpx.get(f"http://localhost:{API_PORT}/api/applications", headers={"Authorization": f"Bearer {TOKEN}"}).json()
        check(apps and apps[0]["status"] == "interview", "the move is saved in the API")

        pg.goto(base + "/resume")
        pg.get_by_text("not reviewed yet").wait_for(timeout=30000)
        check(pg.get_by_role("button", name="Autofill profile").count() == 1, "Resume page offers the autofill profile")
        pg.get_by_role("button", name="Looks right, mark reviewed").click()
        pg.get_by_text("Saved as version 2").wait_for(timeout=15000)
        check(True, "review saves a new resume version")

        pg.goto(base + "/health")
        pg.get_by_text("Gemini usage, last 24 h").wait_for(timeout=30000)
        check(pg.get_by_text("Job ingestion").count() > 0 and pg.get_by_text("Next run").count() > 0, "Agent health shows workers and next runs")

        pg.goto(base + "/settings")
        pg.get_by_text("Match score", exact=True).wait_for(timeout=30000)
        check(pg.get_by_text("Top match from").count() == 1, "Settings has editable score bands")

        pg.set_viewport_size({"width": 390, "height": 844})
        for path in ("/", "/pipeline", "/resume", "/health", "/settings"):
            pg.goto(base + path)
            pg.wait_for_timeout(1200)
            check(pg.evaluate("document.documentElement.scrollWidth") <= 390, f"no sideways scroll on a phone: {path}")
        b.close()
    check(not errors, f"no page errors ({errors[:2]})")


def main() -> int:
    chromium = sys.argv[1] if len(sys.argv) > 1 else None
    db_url = subprocess.run([sys.executable, str(ROOT / "scripts" / "devdb.py"), "--db", "scout_e2e"],
                            capture_output=True, text=True, check=True).stdout.strip().splitlines()[-1]
    env = {**os.environ, "DATABASE_URL": db_url}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT / "packages" / "shared", env=env,
                   check=True, capture_output=True)
    asyncio.run(seed(db_url))
    tmp = Path(tempfile.mkdtemp(prefix="scout-e2e-"))
    procs: list[subprocess.Popen] = []
    try:
        api_env = {**env, "ENV": "development", "DEV_AUTH_TOKEN": TOKEN, "CORS_ORIGINS": f"http://localhost:{WEB_PORT}",
                   "GEMINI_API_KEY": "", "TELEGRAM_BOT_TOKEN": "", "LANGFUSE_PUBLIC_KEY": ""}
        procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "scout_api.main:app", "--port", str(API_PORT)],
                                      cwd=ROOT / "apps" / "api", env=api_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        for item in ("src", "public", "package.json", "tsconfig.json", "next.config.ts", "components.json", "next-env.d.ts", "node_modules"):
            src = WEB / item
            if not src.exists():
                continue
            if subprocess.run(["cp", "-Rc", str(src), str(tmp)], capture_output=True).returncode != 0:  # APFS clone; fall back to copy
                (shutil.copytree if src.is_dir() else shutil.copy2)(src, tmp / item)
        web_env = {**os.environ, "NEXT_PUBLIC_API_URL": f"http://localhost:{API_PORT}", "NEXT_PUBLIC_DEV_AUTH_TOKEN": TOKEN,
                   "NEXT_PUBLIC_SUPABASE_URL": ""}
        procs.append(subprocess.Popen(["npx", "next", "dev", "-p", str(WEB_PORT)], cwd=tmp, env=web_env,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        wait_http(f"http://localhost:{API_PORT}/healthz")
        wait_http(f"http://localhost:{WEB_PORT}/")
        drive(chromium)
    finally:
        for proc in procs:
            proc.terminate()
        shutil.rmtree(tmp, ignore_errors=True)
    failed = [w for ok, w in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed" + (f"; FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

"""API tests against a real Postgres, with real ES256 JWTs signed by a test key."""

import json
import time
from datetime import UTC, datetime

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from scout_api import auth
from scout_api.deps import db
from scout_api.main import app
from scout_core.config import get_settings
from scout_core.db.models import Application, AuditLog, Job
from scout_core.db.session import make_engine
from scout_core.filters.fresher import FresherRules
from scout_core.filters.relevance import RelevanceRules
from scout_core.pipeline.evaluate import Preferences
from scout_core.pipeline.ingest import get_or_create_company, store_jobs
from scout_core.types import NormalizedJob

pytestmark = pytest.mark.db
SUPABASE = "https://testproj.supabase.co"
OWNER = "owner@example.com"
KEY = ec.generate_private_key(ec.SECP256R1())


class _FakeJwks:
    def get_signing_key_from_jwt(self, token):
        return jwt.PyJWK.from_dict(json.loads(jwt.algorithms.ECAlgorithm.to_jwk(KEY.public_key())), "ES256")


def _token(email=OWNER, **overrides):
    claims = {"sub": "u1", "email": email, "aud": "authenticated", "iss": f"{SUPABASE}/auth/v1",
              "exp": int(time.time()) + 3600, "role": "authenticated", **overrides}
    return jwt.encode(claims, KEY, algorithm="ES256", headers={"kid": "k1"})


@pytest.fixture
async def client(session, db_url, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "supabase_url", SUPABASE)
    monkeypatch.setattr(s, "owner_email", OWNER)
    monkeypatch.setattr(s, "telegram_chat_id", "42")
    monkeypatch.setattr(s, "telegram_bot_token", __import__("pydantic").SecretStr("123:abc"))
    monkeypatch.setattr(s, "telegram_webhook_secret", __import__("pydantic").SecretStr("hook-secret"))
    monkeypatch.setattr(auth, "_jwks_client", lambda url: _FakeJwks())
    engine = make_engine(db_url, pooled=False)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _db():
        async with maker() as s2:
            yield s2

    app.dependency_overrides[db] = _db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


async def _seed(session):
    company = await get_or_create_company(session, name="Acme AI", website="https://acme.ai", source="test")
    jobs = [
        NormalizedJob(source="lever", source_job_id="1", company_name="Acme AI", title="AI Engineer",
                      description_text="Freshers welcome. Python, LLM, RAG.", locations=["Bengaluru"],
                      apply_url="https://jobs.lever.co/acme/1", board_token="acme",
                      posted_at=datetime(2026, 11, 5, tzinfo=UTC)),
        NormalizedJob(source="lever", source_job_id="2", company_name="Acme AI", title="Backend Engineer",
                      description_text="0-1 years of experience. FastAPI services for LLM agents and RAG.",
                      locations=["Remote"],
                      apply_url="https://jobs.lever.co/acme/2", board_token="acme",
                      posted_at=datetime(2026, 11, 6, tzinfo=UTC)),
        NormalizedJob(source="lever", source_job_id="3", company_name="Acme AI", title="Sales Executive",
                      description_text="Freshers welcome.", locations=["Mumbai"],
                      apply_url="https://jobs.lever.co/acme/3", board_token="acme"),
    ]
    await store_jobs(session, jobs, company=company, fresher_rules=FresherRules(second_reading=False),
                     relevance_rules=RelevanceRules(), prefs=Preferences())
    await session.commit()


def _auth(token=None):
    return {"Authorization": f"Bearer {token or _token()}"}


async def test_requires_token(client):
    assert (await client.get("/api/jobs")).status_code == 401


async def test_rejects_other_users(client):
    r = await client.get("/api/jobs", headers=_auth(_token(email="someone@else.com")))
    assert r.status_code == 403


async def test_rejects_expired_and_wrong_issuer(client):
    assert (await client.get("/api/jobs", headers=_auth(_token(exp=1)))).status_code == 401
    assert (await client.get("/api/jobs", headers=_auth(_token(iss="https://evil/auth/v1")))).status_code == 401


async def test_feed_newest_first_and_hides_unrelated(client, session):
    await _seed(session)
    r = await client.get("/api/jobs", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert [j["title"] for j in body["items"]] == ["Backend Engineer", "AI Engineer"]
    assert body["total"] == 2
    assert body["facets"]["family"] == {"backend": 1, "applied_ai": 1}
    r = await client.get("/api/jobs", params={"all_roles": True}, headers=_auth())
    assert r.json()["total"] == 3


async def test_filters_and_search(client, session):
    await _seed(session)
    r = await client.get("/api/jobs", params={"location": "remote_global"}, headers=_auth())
    assert [j["title"] for j in r.json()["items"]] == ["Backend Engineer"]
    r = await client.get("/api/jobs", params={"q": "ai eng"}, headers=_auth())
    assert [j["title"] for j in r.json()["items"]] == ["AI Engineer"]


async def test_mark_applied_and_skip_leave_feed_and_audit(client, session):
    await _seed(session)
    items = (await client.get("/api/jobs", headers=_auth())).json()["items"]
    r = await client.post(f"/api/jobs/{items[0]['id']}/status", json={"status": "applied"}, headers=_auth())
    assert r.status_code == 200 and r.json()["status"] == "applied"
    r = await client.post(f"/api/jobs/{items[1]['id']}/status", json={"status": "skipped"}, headers=_auth())
    assert (await client.get("/api/jobs", headers=_auth())).json()["total"] == 0
    app_row = await session.scalar(select(Application).where(Application.status == "applied"))
    assert app_row is not None and app_row.applied_at is not None
    assert len((await session.scalars(select(AuditLog))).all()) == 2
    # Undo puts it back.
    await client.post(f"/api/jobs/{items[1]['id']}/status", json={"status": "discovered"}, headers=_auth())
    assert (await client.get("/api/jobs", headers=_auth())).json()["total"] == 1


async def test_experience_correction(client, session):
    await _seed(session)
    items = (await client.get("/api/jobs", headers=_auth())).json()["items"]
    r = await client.post(f"/api/jobs/{items[0]['id']}/experience", json={"entry_level": False}, headers=_auth())
    assert r.status_code == 200 and "marked not entry-level by me" in r.json()["filter_reasons"][0]
    assert (await client.get("/api/jobs", headers=_auth())).json()["total"] == 1


async def test_settings_validation(client):
    r = await client.put("/api/settings/schedule", json={"nope": 1}, headers=_auth())
    assert r.status_code == 422
    r = await client.put("/api/settings/schedule", json={"ingest_interval_minutes": 30}, headers=_auth())
    assert r.status_code == 200 and r.json()["ingest_interval_minutes"] == 30
    assert r.json()["daily_digest_time"] == "08:00"  # defaults preserved


async def test_saved_searches_crud(client):
    r = await client.post("/api/saved-searches", json={"site": "linkedin", "label": "AI eng BLR",
                                                       "url": "https://www.linkedin.com/jobs/search/?keywords=ai"},
                          headers=_auth())
    assert r.status_code == 201
    assert len((await client.get("/api/saved-searches", headers=_auth())).json()) == 1
    await client.delete(f"/api/saved-searches/{r.json()['id']}", headers=_auth())
    assert (await client.get("/api/saved-searches", headers=_auth())).json() == []
    bad = await client.post("/api/saved-searches", json={"site": "linkedin", "label": "x", "url": "javascript:alert(1)"},
                            headers=_auth())
    assert bad.status_code == 422


@respx.mock
async def test_telegram_webhook_secret_and_allowlist(client, session):
    await _seed(session)
    sent = respx.post(url__regex=r"https://api\.telegram\.org/bot123:abc/.*").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}}))
    update = {"message": {"chat": {"id": 42}, "text": "/stats"}}
    assert (await client.post("/telegram/webhook", json=update)).status_code == 403
    assert (await client.post("/telegram/webhook", json=update,
                              headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})).status_code == 403
    ok = {"X-Telegram-Bot-Api-Secret-Token": "hook-secret"}
    assert (await client.post("/telegram/webhook", json=update, headers=ok)).status_code == 200
    assert sent.call_count == 1
    # A stranger's chat is ignored.
    await client.post("/telegram/webhook", json={"message": {"chat": {"id": 7}, "text": "/pause"}}, headers=ok)
    assert sent.call_count == 1
    # Applied button from my chat marks the job applied.
    job = await session.scalar(select(Job).where(Job.title == "AI Engineer"))
    cb = {"callback_query": {"id": "c1", "data": f"applied:{job.id}", "message": {"chat": {"id": 42}}}}
    assert (await client.post("/telegram/webhook", json=cb, headers=ok)).status_code == 200
    await session.refresh(job)
    assert job.status.value == "applied"


async def test_dev_token_only_outside_production(client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "dev_auth_token", __import__("pydantic").SecretStr("local-dev-token"))
    assert (await client.get("/api/stats", headers=_auth("local-dev-token"))).status_code == 200
    monkeypatch.setattr(s, "env", "production")
    # In production the dev token is just an invalid JWT.
    assert (await client.get("/api/stats", headers=_auth("local-dev-token"))).status_code == 401


async def test_saved_search_edit(client):
    r = await client.post("/api/saved-searches", json={"site": "linkedin", "label": " old ", "url": "https://www.linkedin.com/jobs/"},
                          headers=_auth())
    sid = r.json()["id"]
    r = await client.patch(f"/api/saved-searches/{sid}",
                           json={"label": "LinkedIn – AI – India", "url": "https://www.linkedin.com/jobs/search/?keywords=ai"},
                           headers=_auth())
    assert r.status_code == 200
    assert r.json()["label"] == "LinkedIn – AI – India" and "keywords=ai" in r.json()["url"]
    assert (await client.patch(f"/api/saved-searches/{sid}", json={"url": "notaurl"}, headers=_auth())).status_code == 422
    assert (await client.patch("/api/saved-searches/00000000-0000-0000-0000-000000000000", json={"label": "x"},
                               headers=_auth())).status_code == 404


async def test_job_labels(client, session):
    await _seed(session)
    items = (await client.get("/api/jobs", headers=_auth())).json()["items"]
    a, b = items[0]["id"], items[1]["id"]
    r = await client.put(f"/api/jobs/{a}/label", json={"label": "apply"}, headers=_auth())
    assert r.status_code == 200 and r.json()["apply"] == 1 and r.json()["target"] == 40
    await client.put(f"/api/jobs/{b}/label", json={"label": "no", "note": "too senior"}, headers=_auth())
    await client.put(f"/api/jobs/{a}/label", json={"label": "maybe"}, headers=_auth())  # change my mind
    summary = (await client.get("/api/labels/summary", headers=_auth())).json()
    assert (summary["apply"], summary["maybe"], summary["no"], summary["total"]) == (0, 1, 1, 2)
    labelled = {j["id"]: j["my_label"] for j in (await client.get("/api/jobs", headers=_auth())).json()["items"]}
    assert labelled[a] == "maybe" and labelled[b] == "no"
    assert (await client.get(f"/api/jobs/{a}", headers=_auth())).json()["my_label"] == "maybe"
    await client.put(f"/api/jobs/{a}/label", json={"label": None}, headers=_auth())  # clear
    assert (await client.get("/api/labels/summary", headers=_auth())).json()["total"] == 1
    assert (await client.put(f"/api/jobs/{a}/label", json={"label": "yes!"}, headers=_auth())).status_code == 422


async def test_match_score_in_feed_and_best_fit_sort(client, session):
    from scout_core.db.models import JobScore
    from scout_core.matching import PROMPT_VERSION
    await _seed(session)
    items = (await client.get("/api/jobs", headers=_auth())).json()["items"]
    assert all(j["match"] is None for j in items)
    backend = next(j for j in items if j["title"] == "Backend Engineer")
    import uuid as _uuid
    session.add(JobScore(job_id=_uuid.UUID(backend["id"]), score=91, breakdown={"info": "full"}, reasons=["fits"],
                         matched_skills=["fastapi"], missing_skills=["go"], red_flags=[], model="m",
                         prompt_version=PROMPT_VERSION))
    await session.commit()
    best = (await client.get("/api/jobs", params={"sort": "relevance"}, headers=_auth())).json()["items"]
    assert best[0]["title"] == "Backend Engineer"
    assert best[0]["match"]["score"] == 91 and best[0]["match"]["band"] == "top"
    assert best[0]["match"]["missing_skills"] == ["go"]


async def test_unclear_experience_hidden_unless_asked(client, session):
    from scout_core.filters.fresher import FresherRules
    from scout_core.filters.relevance import RelevanceRules
    from scout_core.pipeline.evaluate import Preferences
    await _seed(session)
    company = await get_or_create_company(session, name="Acme AI", website=None, source="test")
    # A full posting that never states the experience level (title-only alert jobs are left out entirely).
    await store_jobs(session, [NormalizedJob(
        source="lever", source_job_id="9", company_name="Acme AI", title="Machine Learning Engineer",
        description_text="Build LLM agents and RAG pipelines in Python. You will ship features with the team.",
        locations=["Bengaluru"], apply_url="https://jobs.lever.co/acme/9", board_token="acme")],
        company=company, fresher_rules=FresherRules(second_reading=False), relevance_rules=RelevanceRules(), prefs=Preferences())
    await session.commit()
    default = (await client.get("/api/jobs", headers=_auth())).json()
    assert "Machine Learning Engineer" not in [j["title"] for j in default["items"]]
    assert default["facets"]["experience"]["unclear"] == 1
    shown = (await client.get("/api/jobs", params={"include_unclear": True}, headers=_auth())).json()
    assert "Machine Learning Engineer" in [j["title"] for j in shown["items"]]
    stats = (await client.get("/api/stats", headers=_auth())).json()
    assert stats["open_in_feed"] == default["total"]  # Telegram/stats follow the same rule


# ------------------------------------------------------------------ resume manager

def _pdf(lines: list[str]) -> bytes:
    """A minimal real PDF with one text page (enough for pypdf to extract the lines)."""
    content = "BT /F1 11 Tf 50 780 Td 14 TL " + " ".join(
        "(" + ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") '" for ln in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


SOURCE = ("[NAME]\n[EMAIL] | [PHONE]\nB.Tech Computer Science, Example Institute of Technology, 2022 - 2026, CGPA 8.3\n"
          "Skills: Python, FastAPI, Redis, React\nNagrik - civic complaints app (2025)\n"
          "Built 6 agents with a BullMQ queue; cut triage time 40%\n")


def _parsed(**over):
    base = {
        "headline": None, "summary": None,
        "education": [{"institution": "Example Institute of Technology", "degree": "B.Tech",
                       "field": "Computer Science", "start": "2022", "end": "2026", "grade": "CGPA 8.3"}],
        "skills": [{"category": "Languages", "items": ["Python"]},
                   {"category": "Tools", "items": ["FastAPI", "Redis", "React"]}],
        "projects": [{"name": "Nagrik", "tech": ["Redis"], "dates": "2025", "link_label": None,
                      "bullets": ["Built 6 agents with a BullMQ queue; cut triage time 40%"]}],
        "experience": [], "certifications": [], "achievements": [],
        "contact": {"name": "Jane Example", "emails": ["jane@example.com"], "phones": [], "links": []},
        "fact_check": {"ok": True, "unsupported": []}, "source_text": SOURCE,
        "parsed_with": "gemini-test", "reviewed": False,
    }
    return {**base, **over}


async def _seed_resume(session, **over):
    from scout_core.db.models import MasterResume
    session.add(MasterResume(file_url="local:resume.pdf", version=1, is_active=True, parsed=_parsed(**over)))
    await session.commit()


async def test_resume_empty_state(client):
    body = (await client.get("/api/resume", headers=_auth())).json()
    assert body["active"] is None and body["versions"] == []
    assert (await client.put("/api/resume", json={"base_version": 1, "data": {}, "contact": {}},
                             headers=_auth())).status_code == 409


async def test_resume_review_creates_new_version_and_keeps_the_original(client, session):
    from scout_core.db.models import MasterResume
    await _seed_resume(session)
    body = (await client.get("/api/resume", headers=_auth())).json()
    active = body["active"]
    assert active["version"] == 1 and active["reviewed"] is False and active["can_fact_check"]
    assert active["source"] == "Parsed from resume.pdf"
    assert "source_text" not in active  # the stored file text isn't sent to the browser

    # Correct a skill group and tidy messy input: blanks dropped, repeats removed, spaces trimmed.
    data = active["data"]
    data["skills"][0]["items"] = ["Python", "  python ", "", "FastAPI"]
    data["projects"][0]["bullets"].append("   ")
    r = await client.put("/api/resume", json={"base_version": 1, "data": data, "contact": active["contact"]},
                         headers=_auth())
    assert r.status_code == 200 and r.json()["saved"] is True
    new = r.json()["active"]
    assert new["version"] == 2 and new["reviewed"] and new["edited_from"] == 1
    assert new["source"] == "Your corrections to version 1"
    assert new["data"]["skills"][0]["items"] == ["Python", "FastAPI"]
    assert new["data"]["skills"][1]["items"] == ["Redis", "React"]  # FastAPI listed once, in the first group
    assert new["data"]["projects"][0]["bullets"] == ["Built 6 agents with a BullMQ queue; cut triage time 40%"]
    assert [v["version"] for v in r.json()["versions"]] == [2, 1]

    v1 = await session.scalar(select(MasterResume).where(MasterResume.version == 1))
    await session.refresh(v1)
    assert v1.is_active is False and v1.parsed == _parsed()  # untouched

    # Saving again with no changes doesn't pile up identical versions.
    again = await client.put("/api/resume", json={"base_version": 2, "data": new["data"], "contact": new["contact"]},
                             headers=_auth())
    assert again.json()["saved"] is False and again.json()["active"]["version"] == 2

    # An edit started from an older version is refused instead of overwriting newer work.
    stale = await client.put("/api/resume", json={"base_version": 1, "data": data, "contact": active["contact"]},
                             headers=_auth())
    assert stale.status_code == 409


async def test_resume_review_without_changes_marks_reviewed(client, session):
    await _seed_resume(session)
    a = (await client.get("/api/resume", headers=_auth())).json()["active"]
    r = (await client.put("/api/resume", json={"base_version": 1, "data": a["data"], "contact": a["contact"]},
                          headers=_auth())).json()
    assert r["saved"] and r["active"]["version"] == 2 and r["active"]["reviewed"]


async def test_resume_fact_check_flags_what_is_not_in_the_file(client, session):
    await _seed_resume(session)
    a = (await client.get("/api/resume", headers=_auth())).json()["active"]
    data = a["data"]
    data["skills"][0]["items"].append("Kubernetes")
    data["projects"][0]["bullets"][0] = "Built 9 agents with a BullMQ queue; cut triage time 40%"
    data["summary"] = "Shipped 3 apps"
    check = (await client.post("/api/resume/check", json={"data": data}, headers=_auth())).json()
    assert check["available"] and not check["ok"]
    joined = " | ".join(check["unsupported"])
    assert "Kubernetes" in joined and "'9'" in joined and "'3'" in joined
    assert len(check["unsupported"]) == 3  # 40%, Python etc. are in the file
    # Saving is allowed (I'm the source of truth), and the warnings stay on the version.
    r = (await client.put("/api/resume", json={"base_version": 1, "data": data, "contact": a["contact"]},
                          headers=_auth())).json()
    assert r["active"]["fact_check"]["ok"] is False and r["versions"][0]["unsupported"] == 3


async def test_resume_activate_older_version(client, session):
    await _seed_resume(session)
    a = (await client.get("/api/resume", headers=_auth())).json()["active"]
    a["data"]["headline"] = "AI engineer"
    await client.put("/api/resume", json={"base_version": 1, "data": a["data"], "contact": a["contact"]},
                     headers=_auth())
    r = (await client.post("/api/resume/versions/1/activate", headers=_auth())).json()
    assert r["active"]["version"] == 1 and [v["is_active"] for v in r["versions"]] == [False, True]
    assert (await client.post("/api/resume/versions/9/activate", headers=_auth())).status_code == 404
    old = (await client.get("/api/resume/versions/2", headers=_auth())).json()
    assert old["data"]["headline"] == "AI engineer" and old["is_active"] is False


async def test_resume_upload(client, session, monkeypatch):
    from scout_api.routes import resume as resume_routes
    seen = {}

    class FakeLLM:
        pass

    async def fake_parse(llm, raw):
        seen["raw"] = raw
        return _parsed(source_text="redacted")

    monkeypatch.setattr(resume_routes.Gemini, "from_settings", classmethod(lambda cls: FakeLLM()))
    monkeypatch.setattr(resume_routes, "parse_resume_text", fake_parse)
    pdf = _pdf(["Jane Example", "Skills: Python, FastAPI, Redis, React, PostgreSQL, Docker, LangGraph",
                "Nagrik - civic complaints app with six agents, a BullMQ queue and an MCP server",
                "Haathi - multilingual retrieval augmented generation for government schemes",
                "B.Tech Computer Science, Example Institute of Technology, 2022 - 2026"])
    r = await client.post("/api/resume/upload", files={"file": ("my resume.pdf", pdf, "application/pdf")},
                          headers=_auth())
    assert r.status_code == 200, r.text
    assert r.json()["active"]["version"] == 1 and r.json()["active"]["source"] == "Uploaded my resume.pdf"
    assert "Nagrik - civic complaints app" in seen["raw"]

    not_pdf = await client.post("/api/resume/upload", files={"file": ("cv.pdf", b"hello", "application/pdf")},
                                headers=_auth())
    assert not_pdf.status_code == 422 and "PDF or a Word" in not_pdf.json()["detail"]
    empty = await client.post("/api/resume/upload", files={"file": ("cv.pdf", _pdf(["Hi"]), "application/pdf")},
                              headers=_auth())
    assert empty.status_code == 422 and "scanned" in empty.json()["detail"]
    huge = await client.post("/api/resume/upload", files={"file": ("cv.pdf", b"%PDF" + b"0" * (5 * 1024 * 1024),
                                                                   "application/pdf")}, headers=_auth())
    assert huge.status_code == 413
    assert (await client.get("/api/resume", headers=_auth())).json()["versions"][0]["version"] == 1


async def test_resume_upload_without_gemini(client, monkeypatch):
    from scout_api.routes import resume as resume_routes
    from scout_core.llm import LLMNotConfigured

    def boom(cls):
        raise LLMNotConfigured("no key")

    monkeypatch.setattr(resume_routes.Gemini, "from_settings", classmethod(boom))
    pdf = _pdf(["Skills: Python, FastAPI, Redis, React, PostgreSQL, Docker, LangGraph, LangChain, Pandas",
                "Nagrik - civic complaints app with six agents, a BullMQ queue and an MCP server, evals",
                "Haathi - multilingual retrieval augmented generation for government schemes"])
    r = await client.post("/api/resume/upload", files={"file": ("cv.pdf", pdf, "application/pdf")}, headers=_auth())
    assert r.status_code == 503 and "GEMINI_API_KEY" in r.json()["detail"]


async def test_scores_follow_the_active_resume(client, session):
    """After a resume edit, old scores count as outdated; the feed shows each job once (newest score)."""
    import uuid as _uuid

    from scout_core.db.models import JobScore
    from scout_core.matching import PROMPT_VERSION, profile_text
    from scout_core.resume import profile_fingerprint
    from scout_core.resume_store import resume_data
    from scout_core.settings_store import get_setting
    await _seed(session)
    await _seed_resume(session)
    targets = (await get_setting(session, "matching")).get("target_roles", [])
    fp1 = profile_fingerprint(profile_text(resume_data(_parsed()), targets), PROMPT_VERSION)
    items = (await client.get("/api/jobs", headers=_auth())).json()["items"]
    ai = next(j for j in items if j["title"] == "AI Engineer")
    session.add(JobScore(job_id=_uuid.UUID(ai["id"]), score=60, breakdown={}, model="m",
                         prompt_version=PROMPT_VERSION, profile_hash=fp1))
    await session.commit()
    assert (await client.get("/api/resume", headers=_auth())).json()["scoring"] == {
        "current": 1, "outdated": 0, "unscored": 1}

    a = (await client.get("/api/resume", headers=_auth())).json()["active"]
    a["data"]["skills"][0]["items"].append("React Native")
    r = (await client.put("/api/resume", json={"base_version": 1, "data": a["data"], "contact": a["contact"]},
                          headers=_auth())).json()
    assert r["scoring"] == {"current": 0, "outdated": 1, "unscored": 1}

    # Re-scored against the new resume: the job appears once, with the new score.
    fp2 = profile_fingerprint(profile_text(resume_data(r["active"]["data"]), targets), PROMPT_VERSION)
    session.add(JobScore(job_id=_uuid.UUID(ai["id"]), score=88, breakdown={}, model="m",
                         prompt_version=PROMPT_VERSION, profile_hash=fp2))
    await session.commit()
    feed = (await client.get("/api/jobs", params={"sort": "relevance"}, headers=_auth())).json()
    assert [j["title"] for j in feed["items"]].count("AI Engineer") == 1 and feed["total"] == 2
    assert feed["items"][0]["title"] == "AI Engineer" and feed["items"][0]["match"]["score"] == 88
    assert (await client.get("/api/resume", headers=_auth())).json()["scoring"]["current"] == 1



async def test_feed_shows_ai_field_roles_only(client, session):
    """My field is AI: a plain backend role stays out even when it is entry level."""
    await _seed(session)
    company = await get_or_create_company(session, name="Acme AI", website=None, source="test")
    await store_jobs(session, [NormalizedJob(
        source="lever", source_job_id="7", company_name="Acme AI", title="Backend Developer",
        description_text="Freshers welcome. Java and Spring payments APIs.", locations=["Pune"],
        apply_url="https://jobs.lever.co/acme/7", board_token="acme")],
        company=company, fresher_rules=FresherRules(second_reading=False), relevance_rules=RelevanceRules(), prefs=Preferences())
    await session.commit()
    titles = [j["title"] for j in (await client.get("/api/jobs", headers=_auth())).json()["items"]]
    assert "Backend Developer" not in titles and "AI Engineer" in titles
    stats = (await client.get("/api/stats", headers=_auth())).json()
    assert stats["open_in_feed"] == len(titles)


# ------------------------------------------------------------------ tailored resumes

class _TailorLLM:
    def __init__(self):
        from scout_core.llm import Usage
        self.usage = Usage()

    async def json(self, schema, prompt, *, purpose, temperature=0.1):
        from scout_core.tailor import BulletOut, ProjectOut, SkillGroupOut
        self.usage.by_call.append({"model": "fake"})
        return schema(skill_groups=[SkillGroupOut(category="Core", items=["Python", "FastAPI"])],
                      projects=[ProjectOut(name="Nagrik", bullets=[BulletOut(
                          from_bullet=0, text="Built 6 agents with a BullMQ queue; cut triage time 40%")])],
                      emphasis=["agents first"])


async def test_tailored_resume_generate_list_download(client, session, monkeypatch):
    from scout_api.routes import tailored
    await _seed(session)
    job_id = (await client.get("/api/jobs", headers=_auth())).json()["items"][0]["id"]
    assert (await client.post(f"/api/jobs/{job_id}/resume", headers=_auth())).status_code == 409  # no master yet
    await _seed_resume(session)
    monkeypatch.setattr(tailored.Gemini, "from_settings", classmethod(lambda cls: _TailorLLM()))
    r = await client.post(f"/api/jobs/{job_id}/resume", headers=_auth())
    assert r.status_code == 200, r.text
    made = r.json()
    assert made["tailored"] and made["passed"] and made["pages"] == 1 and made["unsupported"] == []
    assert made["emphasis"] == ["agents first"] and made["master_version"] == 1
    again = (await client.post(f"/api/jobs/{job_id}/resume", headers=_auth())).json()
    versions = (await client.get(f"/api/jobs/{job_id}/resumes", headers=_auth())).json()
    assert [v["id"] for v in versions] == [again["id"], made["id"]]  # every generation is a new version
    assert (await client.get("/api/resumes", headers=_auth())).json()[0]["job_title"]
    pdf = await client.get(f"/api/resumes/{made['id']}/file", headers=_auth())
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert "Jane-Example" in pdf.headers["content-disposition"]
    docx = await client.get(f"/api/resumes/{made['id']}/file", params={"format": "docx"}, headers=_auth())
    assert docx.status_code == 200 and docx.content[:2] == b"PK"
    assert (await client.get(f"/api/resumes/{made['id']}/file")).status_code == 401  # owner only


@respx.mock
async def test_telegram_resume_button_sends_pdf(client, session, monkeypatch):
    from scout_core import llm as llm_mod
    await _seed(session)
    await _seed_resume(session)
    monkeypatch.setattr(llm_mod.Gemini, "from_settings", classmethod(lambda cls: _TailorLLM()))
    answered = respx.post(url__regex=r".*/answerCallbackQuery").mock(return_value=httpx.Response(200, json={"ok": True, "result": True}))
    doc = respx.post(url__regex=r".*/sendDocument").mock(return_value=httpx.Response(200, json={"ok": True, "result": {}}))
    job = await session.scalar(select(Job).where(Job.title == "AI Engineer"))
    ok = {"X-Telegram-Bot-Api-Secret-Token": "hook-secret"}
    cb = {"callback_query": {"id": "c9", "data": f"resume:{job.id}", "message": {"chat": {"id": 42}}}}
    assert (await client.post("/telegram/webhook", json=cb, headers=ok)).status_code == 200
    assert answered.call_count == 1 and doc.call_count == 1
    body = doc.calls[0].request.content
    assert b"%PDF" in body and b"tailored" in body
    # Second tap reuses the stored version instead of calling Gemini again.
    monkeypatch.setattr(llm_mod.Gemini, "from_settings", classmethod(lambda cls: (_ for _ in ()).throw(AssertionError("no"))))
    await client.post("/telegram/webhook", json=cb, headers=ok)
    assert doc.call_count == 2
    # A stranger's chat can't request my resume.
    stranger = {"callback_query": {"id": "c10", "data": f"resume:{job.id}", "message": {"chat": {"id": 7}}}}
    await client.post("/telegram/webhook", json=stranger, headers=ok)
    assert doc.call_count == 2


# ------------------------------------------------------------------ pipeline + health (Phase 5)

async def test_pipeline_follow_ups_notes_and_moves(client, session):
    await _seed(session)
    items = (await client.get("/api/jobs", headers=_auth())).json()["items"]
    a, b = items[0]["id"], items[1]["id"]
    await client.post(f"/api/jobs/{a}/status", json={"status": "applied"}, headers=_auth())
    await client.post(f"/api/jobs/{b}/status", json={"status": "skipped"}, headers=_auth())
    apps = (await client.get("/api/applications", headers=_auth())).json()
    assert [x["job_id"] for x in apps] == [a]  # skipped jobs aren't in the pipeline
    first = apps[0]
    gap = datetime.fromisoformat(first["follow_up_at"]) - datetime.fromisoformat(first["applied_at"])
    assert round(gap.total_seconds() / 86400) == 7 and first["follow_up_due"] is False

    r = (await client.patch(f"/api/applications/{a}", json={"status": "interview", "notes": " HR call Tue "},
                            headers=_auth())).json()
    assert r["status"] == "interview" and r["notes"] == "HR call Tue"
    assert r["follow_up_at"] == first["follow_up_at"]  # moving columns keeps my reminder
    soon = (datetime.now(UTC)).isoformat()
    assert (await client.patch(f"/api/applications/{a}", json={"follow_up_at": soon}, headers=_auth())).json()["follow_up_due"]
    assert (await client.patch(f"/api/applications/{a}", json={"clear_follow_up": True}, headers=_auth())).json()["follow_up_at"] is None
    assert (await client.patch(f"/api/applications/{b}", json={"notes": "x"}, headers=_auth())).status_code == 200  # skipped row exists
    assert (await client.patch(f"/api/applications/{a}", json={"status": "hired!"}, headers=_auth())).status_code == 422
    # Back to "discovered" (undo) leaves the pipeline.
    await client.patch(f"/api/applications/{a}", json={"status": "discovered"}, headers=_auth())
    assert (await client.get("/api/applications", headers=_auth())).json() == []


async def test_health_summary(client, session):
    from scout_core.db.models import AgentRun
    now = datetime.now(UTC)
    session.add_all([
        AgentRun(worker="ingest", task_key="ingest:a", status="success", started_at=now, finished_at=now,
                 counts={"fetched": 5}),
        AgentRun(worker="scoring", task_key="scoring:a", status="error", started_at=now, finished_at=now,
                 error="429 quota", counts={"llm_calls": 4, "llm_input_tokens": 1000, "llm_output_tokens": 200}, retries=2),
    ])
    await session.commit()
    body = (await client.get("/api/health/summary", headers=_auth())).json()
    w = {x["worker"]: x for x in body["workers"]}
    assert w["ingest"]["last_status"] == "success" and w["ingest"]["runs_24h"] == 1
    assert w["scoring"]["failures_24h"] == 1 and w["scoring"]["retries_24h"] == 2 and "429" in w["scoring"]["last_error"]
    assert datetime.fromisoformat(w["ingest"]["next_due_at"]) > now
    assert body["llm_24h"] == {"llm_calls": 4, "llm_input_tokens": 1000, "llm_output_tokens": 200}


async def test_autofill_profile_export(client, session):
    assert (await client.get("/api/resume/autofill-profile", headers=_auth())).status_code == 409
    await _seed_resume(session, contact={"name": "B.Jane Example", "emails": ["jane@example.com"], "phones": ["+91 98765 43210"],
                                         "links": ["https://github.com/jane/nagrik", "https://www.linkedin.com/in/jane",
                                                   "https://github.com/jane", "https://jane.dev"]})
    r = await client.get("/api/resume/autofill-profile", headers=_auth())
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    p = r.json()
    assert (p["first_name"], p["last_name"], p["full_name"]) == ("Jane Example", "B", "B. Jane Example")
    assert p["linkedin"] == "https://www.linkedin.com/in/jane" and p["github"] == "https://github.com/jane"
    assert p["portfolio"] == "https://jane.dev" and p["graduation_year"] == "2026" and p["school"].startswith("Example")
    assert not {"work_authorization", "salary", "gender", "notice_period"} & set(p)  # my call, never exported



async def test_resume_upload_docx_and_macro_refusal(client, session, monkeypatch):
    import io
    import zipfile

    from docx import Document

    from scout_api.routes import resume as resume_routes
    seen = {}

    async def fake_parse(llm, raw):
        seen["raw"] = raw
        return _parsed(source_text="redacted")

    monkeypatch.setattr(resume_routes.Gemini, "from_settings", classmethod(lambda cls: object()))
    monkeypatch.setattr(resume_routes, "parse_resume_text", fake_parse)
    d = Document()
    d.add_paragraph("Jane Example")
    d.add_paragraph("Skills: Python, FastAPI, Redis, React, PostgreSQL, Docker, LangGraph, LangChain, Qdrant")
    d.add_paragraph("Nagrik - civic complaints app with six agents, a BullMQ queue and an MCP server, with evals")
    t = d.add_table(rows=1, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "Example Institute of Technology", "B.Tech CSE, 2022 - 2026, CGPA 8.3"
    buf = io.BytesIO()
    d.save(buf)
    docx = buf.getvalue()
    r = await client.post("/api/resume/upload", files={"file": ("cv.docx", docx, "application/octet-stream")}, headers=_auth())
    assert r.status_code == 200, r.text
    assert "Nagrik - civic complaints app" in seen["raw"] and "CGPA 8.3" in seen["raw"]  # tables are read too
    assert r.json()["active"]["source"] == "Uploaded cv.docx"

    # The same file with a VBA project inside is refused before anything is parsed.
    bad = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(docx)) as src, zipfile.ZipFile(bad, "w") as out:
        for item in src.infolist():
            out.writestr(item, src.read(item.filename))
        out.writestr("word/vbaProject.bin", b"\x00macro")
    r = await client.post("/api/resume/upload", files={"file": ("cv.docx", bad.getvalue(), "application/octet-stream")}, headers=_auth())
    assert r.status_code == 422 and "macros" in r.json()["detail"]
    r = await client.post("/api/resume/upload", files={"file": ("cv.docm", docx, "application/octet-stream")}, headers=_auth())
    assert r.status_code == 422 and ".docm" in r.json()["detail"]
    other_zip = io.BytesIO()
    with zipfile.ZipFile(other_zip, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("notes.txt", "hello")
    r = await client.post("/api/resume/upload", files={"file": ("cv.docx", other_zip.getvalue(), "application/zip")}, headers=_auth())
    assert r.status_code == 422 and "isn't a Word document" in r.json()["detail"]



async def test_score_bands_editable(client, session):
    import uuid as _uuid

    from scout_core.db.models import JobScore
    from scout_core.matching import PROMPT_VERSION
    await _seed(session)
    job_id = (await client.get("/api/jobs", headers=_auth())).json()["items"][0]["id"]
    session.add(JobScore(job_id=_uuid.UUID(job_id), score=80, breakdown={}, model="m", prompt_version=PROMPT_VERSION))
    await session.commit()
    assert (await client.get(f"/api/jobs/{job_id}", headers=_auth())).json()["match"]["band"] == "good"
    matching = (await client.get("/api/settings", headers=_auth())).json()["matching"]
    bad = {**matching, "bands": {"top": 60, "good": 70, "stretch": 55}}
    assert (await client.put("/api/settings/matching", json=bad, headers=_auth())).status_code == 422
    ok = {**matching, "bands": {"top": 78, "good": 65, "stretch": 50}}
    assert (await client.put("/api/settings/matching", json=ok, headers=_auth())).status_code == 200
    assert (await client.get(f"/api/jobs/{job_id}", headers=_auth())).json()["match"]["band"] == "top"
    assert (await client.get("/api/jobs", headers=_auth())).json()["items"][0]["match"]["band"] in ("top", None)



@respx.mock
async def test_telegram_label_buttons(client, session):
    from scout_core.db.models import JobLabel
    await _seed(session)
    answered = respx.post(url__regex=r".*/answerCallbackQuery").mock(return_value=httpx.Response(200, json={"ok": True, "result": True}))
    job_id = (await session.scalar(select(Job).where(Job.title == "AI Engineer"))).id
    ok = {"X-Telegram-Bot-Api-Secret-Token": "hook-secret"}
    tap = lambda label, chat=42: {"callback_query": {"id": "c", "data": f"label:{label}:{job_id}", "message": {"chat": {"id": chat}}}}  # noqa: E731
    await client.post("/telegram/webhook", json=tap("apply"), headers=ok)
    assert (await session.scalar(select(JobLabel.label))) == "apply"
    assert "1 of 40" in json.loads(answered.calls[0].request.content)["text"]
    await client.post("/telegram/webhook", json=tap("no"), headers=ok)  # changing my mind updates, not duplicates
    session.expire_all()
    assert [r for r in (await session.scalars(select(JobLabel.label))).all()] == ["no"]
    await client.post("/telegram/webhook", json=tap("maybe", chat=7), headers=ok)  # a stranger is ignored
    session.expire_all()
    assert (await session.scalar(select(JobLabel.label))) == "no"
    await client.post("/telegram/webhook", json=tap("hire-me"), headers=ok)  # junk is ignored
    assert (await client.get("/api/labels/summary", headers=_auth())).json()["no"] == 1


def test_job_message_has_label_buttons():
    from datetime import UTC, datetime

    from scout_core.notify.telegram import DigestItem, format_job
    item = DigestItem(job_id="00000000-0000-0000-0000-000000000001", title="AI Engineer", company="Acme", location="Bengaluru",
                      salary_text=None, apply_url="https://jobs.lever.co/acme/1", posted_at=datetime.now(UTC),
                      trust_level="verified", fresher_confidence="high", why="")
    _, buttons = format_job(item)
    datas = [b.get("callback_data", "") for row in buttons for b in row]
    assert {"label:apply:00000000-0000-0000-0000-000000000001", "label:no:00000000-0000-0000-0000-000000000001"} <= set(datas)
    assert all(len(d.encode()) <= 64 for d in datas)  # Telegram's limit for callback data


async def test_internal_tick_secret_and_single_run(client, monkeypatch):
    import asyncio as _asyncio

    from scout_api.routes import internal
    from scout_worker import work
    s = get_settings()
    monkeypatch.setattr(s, "tick_secret", None)
    assert (await client.post("/internal/tick")).status_code == 404  # off unless configured
    monkeypatch.setattr(s, "tick_secret", __import__("pydantic").SecretStr("tick-s3cret"))
    assert (await client.post("/internal/tick")).status_code == 403
    assert (await client.post("/internal/tick", headers={"X-Tick-Secret": "nope"})).status_code == 403
    gate, calls = _asyncio.Event(), []

    async def fake_tick(**kw):
        calls.append(1)
        await gate.wait()
        return {"ingest:x": {}, "instant_alerts": {}}, False

    monkeypatch.setattr(work, "run_tick", fake_tick)
    monkeypatch.setattr(internal, "_state", {"task": None, "started_at": None, "last": None})
    ok = {"X-Tick-Secret": "tick-s3cret"}
    first = await client.post("/internal/tick", headers=ok)
    assert first.status_code == 202 and first.json()["started"] is True
    await _asyncio.sleep(0)
    second = await client.post("/internal/tick", headers=ok)
    assert second.status_code == 200 and second.json()["started"] is False  # never two ticks at once
    gate.set()
    await internal._state["task"]
    assert calls == [1] and internal._state["last"]["slots"] == ["ingest:x"]
    third = await client.post("/internal/tick", headers=ok)
    assert third.status_code == 202 and third.json()["last"]["failed"] is False
    gate.set()
    await internal._state["task"]

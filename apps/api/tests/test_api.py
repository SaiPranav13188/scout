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
                      description_text="0-1 years of experience. FastAPI.", locations=["Remote"],
                      apply_url="https://jobs.lever.co/acme/2", board_token="acme",
                      posted_at=datetime(2026, 11, 6, tzinfo=UTC)),
        NormalizedJob(source="lever", source_job_id="3", company_name="Acme AI", title="Sales Executive",
                      description_text="Freshers welcome.", locations=["Mumbai"],
                      apply_url="https://jobs.lever.co/acme/3", board_token="acme"),
    ]
    await store_jobs(session, jobs, company=company, fresher_rules=FresherRules(),
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

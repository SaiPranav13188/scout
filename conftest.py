"""DB fixtures: a real Postgres (embedded pgserver locally, or DATABASE_URL in CI), migrated with Alembic."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from scout_core.db.session import make_engine

ROOT = Path(__file__).resolve().parent
SHARED = ROOT / "packages" / "shared"


def _test_db_url() -> str | None:
    if url := os.environ.get("TEST_DATABASE_URL"):
        return url
    try:
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / "devdb.py"), "--db", "scout_test"],
                             capture_output=True, text=True, check=True, timeout=120)
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    return out.stdout.strip().splitlines()[-1]


@pytest.fixture(scope="session")
def db_url():
    url = _test_db_url()
    if not url:
        pytest.skip("no Postgres available (set TEST_DATABASE_URL or install dev deps)")
    env = {**os.environ, "DATABASE_URL": url}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=SHARED, env=env,
                   check=True, capture_output=True, timeout=120)
    return url


@pytest.fixture
async def session(db_url):
    engine = make_engine(db_url, pooled=False)
    async with engine.begin() as conn:
        tables = (await conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'"))).scalars().all()
        await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()

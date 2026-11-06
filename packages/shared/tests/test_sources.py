"""Parsers tested on real payloads recorded from public boards on 2026-11-06 (see fixtures/README.md)."""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from scout_core.http import PoliteClient
from scout_core.sources.ats import AshbySource, GreenhouseSource, LeverSource, ats_from_url
from scout_core.sources.base import BoardNotFound, SourceError
from scout_core.types import RawFetch

FIX = Path(__file__).parent / "fixtures"


def _raw(source, token, name):
    return RawFetch(source=source, board_token=token, url="recorded", status_code=200,
                    fetched_at=datetime.now(UTC), payload=json.loads((FIX / name).read_text()))


def test_greenhouse_real_payload():
    raw = _raw("greenhouse", "razorpaysoftwareprivatelimited", "gh_full.json")
    jobs = GreenhouseSource().parse(raw)
    assert len(jobs) == len(raw.payload["jobs"]) > 0
    j = jobs[0]
    assert j.source_job_id == str(raw.payload["jobs"][0]["id"])
    assert j.company_name == "Razorpay Software Private Limited"
    assert j.apply_url.startswith("https://job-boards.greenhouse.io/")
    assert j.posted_at is not None and j.posted_at.tzinfo is not None
    assert j.posted_at_kind == "published"
    # Content arrives entity-escaped; the parser must produce clean text.
    assert "&lt;" not in (j.description_text or "") and "<div" not in (j.description_text or "")
    assert len(j.description_text or "") > 200


def test_lever_real_payload():
    raw = _raw("lever", "fampay", "lever_full.json")
    jobs = LeverSource().parse(raw)
    assert len(jobs) == len(raw.payload) > 0
    j = jobs[0]
    assert j.apply_url.startswith("https://jobs.lever.co/fampay/")
    assert j.posted_at is not None and j.posted_at.year >= 2020  # ms timestamp converted
    assert j.locations and j.extra["country"] == "IN"
    assert j.description_text and "<" not in j.description_text[:200]


def test_ashby_real_payload():
    raw = _raw("ashby", "bolna", "ashby_full.json")
    listed = [x for x in raw.payload["jobs"] if x.get("isListed") is not False]
    jobs = AshbySource().parse(raw)
    assert len(jobs) == len(listed) > 0
    j = jobs[0]
    assert j.apply_url.startswith("https://jobs.ashbyhq.com/bolna/")
    assert j.posted_at is not None
    assert j.employment_type in {"Full Time", "Part Time", "Intern", "Contract", "Temporary"}
    assert any("India" in loc or "Bengaluru" in loc for loc in j.locations)


def test_ashby_skips_unlisted():
    raw = _raw("ashby", "bolna", "ashby_full.json")
    raw.payload["jobs"][0]["isListed"] = False
    assert len(AshbySource().parse(raw)) == len(raw.payload["jobs"]) - 1


@respx.mock
async def test_fetch_404_raises_board_not_found():
    respx.get("https://boards-api.greenhouse.io/v1/boards/nope/jobs?content=true").mock(
        return_value=httpx.Response(404))
    async with PoliteClient(min_interval_per_host=0) as c:
        with pytest.raises(BoardNotFound):
            await GreenhouseSource().fetch(c, "nope")


@respx.mock
async def test_fetch_retries_then_succeeds_on_429():
    route = respx.get("https://api.lever.co/v0/postings/acme?mode=json").mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "0"}), httpx.Response(200, json=[])])
    async with PoliteClient(min_interval_per_host=0) as c:
        raw = await LeverSource().fetch(c, "acme")
    assert raw.payload == [] and route.call_count == 2


@respx.mock
async def test_fetch_rejects_unexpected_shape():
    respx.get("https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true").mock(
        return_value=httpx.Response(200, json={"unexpected": True}))
    async with PoliteClient(min_interval_per_host=0) as c:
        with pytest.raises(SourceError):
            await AshbySource().fetch(c, "acme")


@pytest.mark.parametrize(("url", "expected"), [
    ("https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited", ("greenhouse", "razorpaysoftwareprivatelimited")),
    ("https://boards.greenhouse.io/embed/job_board?for=acme", ("greenhouse", "acme")),
    ("https://jobs.lever.co/fampay/123", ("lever", "fampay")),
    ("https://jobs.ashbyhq.com/bolna", ("ashby", "bolna")),
    ("https://example.com/careers", None),
])
def test_ats_from_url(url, expected):
    assert ats_from_url(url) == expected


@respx.mock
async def test_connect_errors_retried_once_only():
    route = respx.get("https://dead.example/").mock(side_effect=httpx.ConnectError("dns"))
    async with PoliteClient(min_interval_per_host=0, max_retries=3) as c:
        with pytest.raises(httpx.ConnectError):
            await c.get("https://dead.example/")
    assert route.call_count == 2

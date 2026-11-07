from datetime import UTC, datetime, timedelta

import pytest

from scout_core.discovery import find_board_links, yc_seeds
from scout_core.filters.relevance import classify_role
from scout_core.pipeline.evaluate import evaluate_job
from scout_core.trust import TrustLevel, evaluate
from scout_core.types import NormalizedJob

NOW = datetime(2026, 11, 6, tzinfo=UTC)


def test_ats_board_with_clean_text_is_verified():
    r = evaluate(source="greenhouse", apply_url="https://job-boards.greenhouse.io/acme/jobs/1",
                 company_website="https://acme.com", description="Build APIs.", now=NOW)
    assert r.level == TrustLevel.VERIFIED


@pytest.mark.parametrize("text", [
    "Selected candidates must pay a registration fee of Rs 2000.",
    "A refundable deposit is required before onboarding.",
    "100% job guarantee after training!",
])
def test_fee_requests_are_scam_risk(text):
    r = evaluate(source="adzuna", apply_url="https://example.com/j", company_website=None, description=text, now=NOW)
    assert r.level == TrustLevel.SCAM_RISK


def test_personal_email_and_whatsapp_are_caution():
    r = evaluate(source="adzuna", apply_url="https://example.com/j", company_website=None,
                 description="Send your resume on WhatsApp to 98xxxxxx or hr.jobs@gmail.com", now=NOW)
    assert r.level == TrustLevel.CAUTION


def test_young_domain_lowers_trust():
    r = evaluate(source="adzuna", apply_url="https://newco.com/j", company_website="https://newco.com",
                 description="Build APIs.", domain_created_at=NOW - timedelta(days=30), now=NOW)
    assert r.level == TrustLevel.CAUTION


def test_unknown_company_is_unverified_not_verified():
    r = evaluate(source="adzuna", apply_url="https://example.com/j", company_website=None,
                 description="Build APIs.", now=NOW)
    assert r.level == TrustLevel.UNVERIFIED


@pytest.mark.parametrize(("title", "family"), [
    ("Applied AI Engineer", "applied_ai"),
    ("Artificial Intelligence Engineer - AWS", "applied_ai"),
    ("AIML Engineer Digital & AI", "applied_ai"),
    ("Forward Deployed Engineer", "fde"),
    ("Backend Developer (Python)", "backend"),
    ("Full Stack Engineer", "fullstack"),
    ("Software Development Engineer 1", "software"),
    ("Inside Sales Executive", "unrelated"),
    ("SAP ABAP Consultant", "unrelated"),
    ("Product Designer", "other"),
    ("Application Engineer, Bengaluru", "software"),
    ("Mechanical Engineer", "unrelated"),
])
def test_role_families(title, family):
    assert classify_role(title, "").family == family


def test_target_skills_raise_relevance():
    low = classify_role("Software Engineer", "Java and Spring.")
    high = classify_role("Software Engineer", "Python, FastAPI, LLM agents, RAG, Postgres, Redis.")
    assert high.score > low.score


def test_find_board_links_on_careers_page():
    html = """<html><body>
      <a href="https://job-boards.greenhouse.io/acme">Open roles</a>
      <a href="/careers/life">Life at Acme</a>
      <iframe src="https://jobs.ashbyhq.com/acme-ai/embed"></iframe>
      <a href="https://twitter.com/acme">Twitter</a></body></html>"""
    boards, follow = find_board_links(html, "https://acme.com/")
    assert ("greenhouse", "acme") in boards and ("ashby", "acme-ai") in boards
    assert follow == ["https://acme.com/careers/life"]


def test_yc_seeds_keep_unknowns_null():
    data = [{"name": "Acme", "status": "Active", "isHiring": True, "website": "https://acme.com",
             "one_liner": "Rockets", "team_size": 12, "batch": "Winter 2024", "stage": "Early"},
            {"name": "Dead", "status": "Inactive", "isHiring": True},
            {"name": "Quiet", "status": "Active", "isHiring": False}]
    seeds = yc_seeds(data)
    assert [s.name for s in seeds] == ["Acme"]
    assert seeds[0].founded_year is None and seeds[0].linkedin_url is None


def _job(**kw):
    base = dict(source="lever", source_job_id="1", company_name="acme", title="Backend Engineer",
                description_text="0-1 years of experience with Python.", locations=["Bengaluru"],
                apply_url="https://jobs.lever.co/acme/1")
    base.update(kw)
    return NormalizedJob(**base)


def test_evaluate_keeps_good_job():
    e = evaluate_job(_job())
    assert e.keep and not e.drop_reasons and e.location_key == "bengaluru"


def test_evaluate_drops_us_only_remote_with_reason():
    e = evaluate_job(_job(locations=["Remote - US"]))
    assert not e.keep and e.drop_reasons[0].startswith("location: remote restricted")


def test_evaluate_drops_senior_with_reason():
    e = evaluate_job(_job(title="Senior Backend Engineer"))
    assert not e.keep and "senior" in e.drop_reasons[0]


@pytest.mark.parametrize(("text", "restricted"), [
    ("You must be based in the US to be considered.", True),
    ("Candidates must be authorized to work in the United States.", True),
    ("This role requires US work authorization.", True),
    ("Remote (US only) role.", True),
    ("Fully remote. We hire anywhere; must be based in the US or India.", False),
    ("Fully remote team across many time zones.", False),
])
def test_remote_restricted_by_description(text, restricted):
    e = evaluate_job(_job(locations=["Remote"], description_text=text + " 0-1 years of experience."))
    assert (e.location.bucket.value == "remote_restricted") is restricted


@pytest.mark.parametrize("text", [
    # Real snippets from YC company boards (2026-11-06) that were wrongly flagged.
    "establish post-training feedback channels to gather participant insights",
    "paid drive time and training; mileage reimbursement; covered rbt exam and application fees; a $1,500 bonus",
    "our agents answer calls, reply to emails and whatsapp messages, make bookings, take payments",
    "We never charge a registration fee at any stage of hiring.",
])
def test_benign_text_is_not_flagged(text):
    r = evaluate(source="greenhouse", apply_url="https://job-boards.greenhouse.io/a/jobs/1",
                 company_website=None, description=text, now=NOW)
    assert r.level == TrustLevel.VERIFIED, r.signals


@pytest.mark.parametrize("text", [
    "Interested candidates send your CV on WhatsApp to 98xxxxxx01",
    "Contact HR on WhatsApp for joining details.",
    "Apply via Telegram: @jobs_now",
])
def test_whatsapp_recruiting_is_flagged(text):
    r = evaluate(source="adzuna", apply_url="https://x.com/j", company_website=None, description=text, now=NOW)
    assert r.level == TrustLevel.CAUTION


@pytest.mark.parametrize(("title", "restricted"), [
    # Real titles from 2026-11-06 that were wrongly shown as worldwide remote.
    ("Mobile Developer I - US Remote", True),
    ("Data Engineer - US Remote", True),
    ("Partner Engineer, Spain", True),
    ("Backend Engineer (Remote, India)", False),
    ("Software Engineer, Distributed Systems", False),
])
def test_remote_restricted_by_title(title, restricted):
    e = evaluate_job(_job(title=title, locations=["Remote"], description_text="0-1 years of experience."))
    assert (e.location.bucket.value == "remote_restricted") is restricted


def test_board_owner_must_match_company():
    from scout_core.discovery import board_matches_company
    payload = {"jobs": [{"company_name": "Razorpay Software Private Limited"}]}
    assert board_matches_company(payload, "Razorpay")
    assert not board_matches_company({"jobs": [{"company_name": "Cloudflare"}]}, "Vector")
    assert board_matches_company([], "Bolna AI")  # Lever/Ashby carry no owner name


async def test_site_linking_many_boards_is_not_a_careers_page(httpserver):
    from scout_core.discovery import RobotsCache, discover_boards
    from scout_core.http import PoliteClient
    links = "".join(f'<a href="https://jobs.ashbyhq.com/company{i}">x</a>' for i in range(5))
    httpserver.expect_request("/").respond_with_data(f"<html><body>{links}</body></html>", content_type="text/html")
    async with PoliteClient(min_interval_per_host=0, max_retries=0) as c:
        boards = await discover_boards(c, RobotsCache(c, "test"), httpserver.url_for("/"), "Vector")
    assert boards == []


def test_next_runs():
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    from scout_core.schedule import next_runs
    sched = {"timezone": "Asia/Kolkata", "ingest_interval_minutes": 10, "verify_interval_hours": 12,
             "discovery_weekday": 6, "discovery_time": "03:00", "daily_digest_time": "08:00",
             "evening_summary_time": "20:00"}
    now = datetime(2026, 11, 7, 9, 3, tzinfo=ZoneInfo("Asia/Kolkata"))  # Saturday 09:03 IST
    n = {k: v.astimezone(ZoneInfo("Asia/Kolkata")) for k, v in next_runs(now.astimezone(UTC), sched).items()}
    assert (n["ingest"].hour, n["ingest"].minute) == (9, 10)
    assert n["daily_digest"].date().isoformat() == "2026-11-08" and n["daily_digest"].hour == 8
    assert n["evening_summary"].date().isoformat() == "2026-11-07" and n["evening_summary"].hour == 20
    assert n["discovery"].date().isoformat() == "2026-11-08" and n["discovery"].hour == 3  # Sunday

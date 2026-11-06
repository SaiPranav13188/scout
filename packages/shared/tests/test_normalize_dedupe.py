from datetime import UTC, datetime

import pytest

from scout_core.dedupe import Candidate, dedupe_key, find_duplicate, titles_match
from scout_core.normalize import (
    LocationBucket,
    canonical_url,
    classify_location,
    normalize_company,
    normalize_title,
    registered_domain,
)


@pytest.mark.parametrize(("raw", "expected"), [
    ("Razorpay Software Private Limited", "razorpay software"),
    ("Acme, Inc.", "acme"),
    ("Foo Technologies Pvt. Ltd.", "foo technologies"),
    ("  Bolna  AI ", "bolna ai"),
])
def test_normalize_company(raw, expected):
    assert normalize_company(raw) == expected


def test_normalize_title_expands_and_strips_location_noise():
    assert normalize_title("Sr. SWE (Remote, India)") == "senior software engineer"
    assert normalize_title("Back-end Engg") == "backend engineer"


@pytest.mark.parametrize(("a", "b"), [
    ("https://boards.greenhouse.io/acme/jobs/123?gh_src=abc", "https://job-boards.greenhouse.io/acme/jobs/123"),
    ("https://jobs.lever.co/acme/uuid-1/apply?lever-source=LinkedIn", "https://jobs.lever.co/acme/uuid-1"),
    ("https://jobs.ashbyhq.com/acme/uuid-2/application?utm_source=x", "https://jobs.ashbyhq.com/acme/uuid-2"),
    ("https://WWW.Acme.com/careers/42/?utm_campaign=y#top", "https://acme.com/careers/42"),
])
def test_canonical_url_collapses_variants(a, b):
    assert canonical_url(a) == canonical_url(b)


def test_canonical_url_keeps_meaningful_params():
    assert canonical_url("https://acme.com/careers?gh_jid=55") != canonical_url("https://acme.com/careers?gh_jid=56")


def test_registered_domain_handles_indian_suffixes():
    assert registered_domain("https://careers.acme.co.in/jobs/1") == "acme.co.in"


@pytest.mark.parametrize(("locs", "remote", "codes", "bucket"), [
    (["Bengaluru"], None, None, LocationBucket.INDIA),
    (["Bangalore, Karnataka"], None, None, LocationBucket.INDIA),
    (["San Francisco, CA", "Gurgaon"], None, None, LocationBucket.INDIA),
    (["Remote"], None, None, LocationBucket.REMOTE_GLOBAL),
    (["Remote - Worldwide"], None, None, LocationBucket.REMOTE_GLOBAL),
    (["Remote (US)"], None, None, LocationBucket.REMOTE_RESTRICTED),
    (["Remote - Europe"], True, None, LocationBucket.REMOTE_RESTRICTED),
    (["Remote, APAC"], None, None, LocationBucket.REMOTE_GLOBAL),
    # Regression (real Ashby job, 2026-11-06): remote job whose locations are Chinese cities.
    (["China", "Shenzhen, China", "Haiyan"], True, None, LocationBucket.REMOTE_RESTRICTED),
    (["Remote-first"], None, None, LocationBucket.REMOTE_GLOBAL),
    (["Remote"], True, None, LocationBucket.REMOTE_GLOBAL),
    (["New York, NY"], None, None, LocationBucket.ABROAD),
    (["London"], False, ["GB"], LocationBucket.ABROAD),
    (["HQ"], None, ["IN"], LocationBucket.INDIA),
    ([], None, None, LocationBucket.UNKNOWN),
])
def test_classify_location(locs, remote, codes, bucket):
    assert classify_location(locs, remote, codes).bucket == bucket


def test_dedupe_key_stable_across_cosmetic_differences():
    assert dedupe_key("Acme Inc.", "Sr. Backend Engineer", "bengaluru") == \
        dedupe_key("ACME", "Senior Back-end Engineer", "bengaluru")


def test_titles_match_respects_seniority():
    assert titles_match("backend software engineer", "software engineer backend") is True
    assert titles_match("software engineer backend", "software engineer - backend") is True
    assert titles_match("software engineer", "senior software engineer") is False
    assert titles_match("software engineer i", "software engineer ii") is False


def _cand(i, company="acme", title="backend engineer", loc="bengaluru", url=None, day=1):
    return Candidate(id=str(i), company_norm=company, title_norm=title, location_key=loc,
                     canonical_url=url, first_seen_at=datetime(2026, 11, day, tzinfo=UTC))


def test_same_apply_url_from_different_source_is_duplicate():
    url = canonical_url("https://jobs.lever.co/acme/abc")
    dup = find_duplicate(company="Something Else", title="Different", location_key="pune",
                         canonical_apply_url=url, candidates=[_cand(1, url=url)])
    assert dup and dup[1] == "same_apply_url"


def test_repost_with_new_id_is_duplicate_and_original_wins():
    cands = [_cand(2, day=5), _cand(1, day=1)]
    dup = find_duplicate(company="Acme Pvt Ltd", title="Backend Engineer", location_key="bengaluru",
                         canonical_apply_url="https://jobs.lever.co/acme/new-id", candidates=cands)
    assert dup and dup[0].id == "1"


def test_different_city_not_duplicate():
    dup = find_duplicate(company="Acme", title="Backend Engineer", location_key="pune",
                         canonical_apply_url=None, candidates=[_cand(1)])
    assert dup is None


def test_different_company_not_duplicate():
    dup = find_duplicate(company="Other", title="Backend Engineer", location_key="bengaluru",
                         canonical_apply_url=None, candidates=[_cand(1)])
    assert dup is None


def test_contained_title_is_not_same_role():
    # Regression: real FamPay jobs on 2026-11-06 were wrongly merged.
    from scout_core.normalize import normalize_title
    assert not titles_match(normalize_title("SDE 2 Infra"), normalize_title("Data Engineer (SDE 2)"))
    assert not titles_match("software engineer", "software engineer backend")

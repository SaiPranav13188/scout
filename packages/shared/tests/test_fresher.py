"""Fresher filter, including the tricky cases named in spec §13.2."""

import pytest

from scout_core.filters.fresher import (
    Confidence,
    Decision,
    FresherRules,
    classify,
    extract_requirements,
)


@pytest.mark.parametrize(
    ("title", "description", "expected"),
    [
        # --- spec keep cases
        ("Software Engineer", "We are looking for 0-1 years of experience in Python.", Decision.KEEP),
        ("Backend Engineer", "Experience: 0 to 1 year", Decision.KEEP),
        ("AI Engineer", "0-12 months of experience building LLM apps", Decision.KEEP),
        ("Graduate Engineer Trainee", "Open to 2026 batch graduates.", Decision.KEEP),
        ("SDE 1", "Freshers are welcome to apply.", Decision.KEEP),
        ("Associate Software Engineer", "Build APIs with FastAPI.", Decision.KEEP),
        ("Junior Full Stack Developer", "", Decision.KEEP),
        ("Software Engineer Intern (PPO)", "Internship with pre-placement offer for strong performers.", Decision.KEEP),
        ("Software Engineer", "Fresher preferred. Strong DSA.", Decision.KEEP),
        ("Software Engineer", "Up to 1 year of experience.", Decision.KEEP),
        # --- spec drop cases
        ("Senior Software Engineer", "0-1 years", Decision.DROP),
        ("Engineering Manager", "", Decision.DROP),
        ("Staff Engineer", "", Decision.DROP),
        ("Principal Architect", "", Decision.DROP),
        ("Tech Lead - Backend", "", Decision.DROP),
        ("Software Engineer", "Requires 3+ years of experience with Go.", Decision.DROP),
        ("Software Engineer", "5+ years building distributed systems.", Decision.DROP),
        ("Backend Engineer", "1+ years of experience with Node.js", Decision.DROP),
        ("Backend Engineer", "2+ years of professional experience", Decision.DROP),
        ("Software Engineer", "Experience: 0-2 years", Decision.DROP),
        ("Software Engineer", "1-3 years of experience in backend development", Decision.DROP),
        ("Data Engineer", "2-4 years experience with Spark", Decision.DROP),
        ("ML Engineer", "Minimum 2 years of experience in ML.", Decision.DROP),
        ("Developer", "At least 3 years of hands-on experience.", Decision.DROP),
        ("Software Engineer III", "", Decision.DROP),
        ("SDE 2 Infra", "", Decision.DROP),
        ("Security Engineer 2", "", Decision.DROP),
        ("Software Engineer II", "", Decision.DROP),
        ("Software Engineer I", "", Decision.KEEP),
        ("Sr. Backend Developer", "", Decision.DROP),
        ("Software Engineering Intern", "Summer internship, 10 weeks.", Decision.DROP),
        # --- "Associate" only when not senior
        ("Senior Associate, Engineering", "", Decision.DROP),
        ("Associate Director, Platform", "", Decision.DROP),
    ],
)
def test_keep_drop(title, description, expected):
    v = classify(title, description)
    assert v.decision == expected, (title, description, v.reasons)


def test_unclear_experience_is_kept_and_flagged():
    v = classify("Software Engineer", "Build things with Python and React.")
    assert v.decision == Decision.KEEP
    assert v.experience_unclear is True
    assert v.confidence == Confidence.LOW


def test_soft_requirement_does_not_drop_but_flags():
    v = classify("Backend Engineer", "3+ years of experience is a plus, freshers can apply.")
    assert v.decision == Decision.KEEP
    assert v.experience_unclear is True


def test_mixed_levels_kept_as_unclear():
    v = classify("Software Engineer", "SDE-1: 0-1 years of experience. SDE-2: 2-4 years of experience.")
    assert v.decision == Decision.KEEP
    assert v.experience_unclear is True


def test_non_experience_numbers_ignored():
    v = classify("Software Engineer", "We were founded 8 years ago and have 5 years of runway. Freshers welcome.")
    assert v.decision == Decision.KEEP
    assert not v.experience_unclear


def test_lead_generation_is_not_seniority():
    v = classify("Lead Generation Associate", "")
    assert v.decision == Decision.KEEP


def test_max_experience_setting():
    rules = FresherRules(max_experience_years=2)
    assert classify("Software Engineer", "0-2 years of experience", rules).decision == Decision.KEEP
    assert classify("Software Engineer", "2-4 years of experience", rules).decision == Decision.DROP


def test_internships_setting():
    rules = FresherRules(include_internships=True)
    assert classify("Software Engineering Intern", "Summer internship.", rules).decision == Decision.KEEP


def test_drop_reason_quotes_evidence():
    v = classify("Software Engineer", "You have 4+ years of experience shipping production code.")
    assert v.decision == Decision.DROP
    assert "4+ years" in v.reasons[0]
    assert "shipping" in v.reasons[0]


def test_word_numbers():
    reqs = extract_requirements("Two or more years of experience required.")
    assert reqs and reqs[0].min == 2 and reqs[0].max is None


def test_high_confidence_on_explicit_range():
    v = classify("Software Engineer", "0-1 years experience")
    assert v.confidence == Confidence.HIGH
    assert v.experience_min == 0 and v.experience_max == 1


def test_company_history_is_not_experience():
    # Regression from a real posting on 2026-11-06.
    v = classify("Sales Development Representative",
                 "We have been growing in exponential growth over the last 10+ years. Freshers welcome.")
    assert v.decision == Decision.KEEP
    v = classify("Software Engineer", "Trusted by banks for the past 15 years.")
    assert v.decision == Decision.KEEP


def test_internshala_job_offer_after_internship_counts_as_conversion():
    # Real Internshala card text (Nov 2026).
    v = classify("Marketing Analytics", "Job offer upto ₹ 3.6LPA post internship", employment_type="Intern")
    assert v.decision == Decision.KEEP

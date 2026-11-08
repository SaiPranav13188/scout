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


def test_soft_requirement_kept_only_when_freshers_are_invited():
    v = classify("Backend Engineer", "3+ years of experience is a plus, freshers can apply.")
    assert v.decision == Decision.KEEP and v.experience_unclear is False
    assert "welcomes freshers" in v.reasons[0]


@pytest.mark.parametrize("text", [
    "3+ years of experience preferred.",
    "Ideally you have 2+ years building backend systems.",
    "Nice to have: 4 years of experience with Kubernetes.",
])
def test_soft_requirement_above_limit_drops(text):
    v = classify("Application Engineer", text)
    assert v.decision == Decision.DROP and "prefers" in v.reasons[0]


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


@pytest.mark.parametrize("title", [
    "SDE3, Infrastructure Security Engineer (DevSecOps)",  # Sarvam AI, 2026-11-07
    "Intermediate Backend Engineer, Security Factory",  # GitLab grade = mid-level
    "Mid-Level Software Engineer", "Experienced Data Engineer", "Software Engineer Level 2",
])
def test_more_senior_titles_dropped(title):
    assert classify(title, "Build things.").decision == Decision.DROP


@pytest.mark.parametrize("title", ["Technical Account Engineer I", "Software Developer I", "Data Analyst I"])
def test_level_one_titles_are_entry_level(title):
    v = classify(title, "Work with customers.")
    assert v.decision == Decision.KEEP and not v.experience_unclear


def test_level_two_roman_still_dropped():
    assert classify("Technical Account Engineer II", "x").decision == Decision.DROP


def test_intern_to_full_time_is_confirmed_not_unclear():
    v = classify("Software Development Engineer Intern", "Strong performers receive a pre-placement offer (PPO).")
    assert v.decision == Decision.KEEP and not v.experience_unclear and v.reasons == ["intern-to-full-time role"]


@pytest.mark.parametrize("text", [  # exact wording from real postings that slipped through (2026-11-07)
    "3–6 years in frontend engineering with shipped, production-grade products",
    "2–5 years in DevOps, SRE, infrastructure or backend engineering",
    "Minimum 8-year experience working as Support Engineer/ Sr. Support Engineer",
    "1-5 years in conversational AI, solution engineering, system integration",
    "4–6 years of building and operating production backend systems.",
    "You bring 2+ years building backend systems.",
])
def test_years_in_a_field_is_a_requirement(text):
    v = classify("Software Engineer", text)
    assert v.decision == Decision.DROP, v.reasons


@pytest.mark.parametrize("text", [
    "We have grown 3x in 2 years in business.", "Our company was founded 10 years ago.",
    "A 2-year fixed-term contract.", "Over the last 5 years in India we've served 1M users.",
    "0-1 years in software engineering or internships.",
])
def test_non_requirements_and_fresher_ranges_still_kept(text):
    assert classify("Software Engineer", text).decision == Decision.KEEP



@pytest.mark.parametrize("title", ["Associate Data Scientist", "Junior AI Engineer"])
def test_weak_title_words_alone_are_not_proof(title):
    v = classify(title, None)
    assert v.decision == Decision.KEEP and v.experience_unclear  # hidden until something confirms 0-1 years


def test_weak_title_word_with_fresher_description_is_confirmed():
    v = classify("Associate Data Scientist", "Open to freshers from the 2026 batch.")
    assert not v.experience_unclear



def test_senior_ai_engineer_posting_from_linkedin_is_dropped():
    """The algoleap "AI Engineer (GIS)" posting (2026-11-08): a plain title, a senior description."""
    desc = ("Top 5 Skills\n- Production Experience with atleast 2 Agentic deployments\n"
            "- Mentor and provide technical leadership to junior and mid-level Data Scientists and ML Engineers.\n"
            "Required Qualifications\n- Experience: 6+ years of progressive experience in Data Science, Machine "
            "Learning, or Applied AI, with a minimum of 3 years in a leadership or principal role.\n"
            "- Education: Master's or Ph.D. in Computer Science.")
    v = classify("AI Engineer (GIS)", desc)
    assert v.decision == Decision.DROP and "6+ years" in v.reasons[0]

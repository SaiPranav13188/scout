"""Alert-email parsers.

Two layers:
1. Format tests (run everywhere, including CI): hand-written emails that reproduce each sender's
   layout with invented companies and jobs. Clearly test data, never stored as jobs.
2. Real-email tests (local only): every sample in private/gmail_samples/ (my own alert emails,
   git-ignored and never published). Skipped when that folder is absent, e.g. on GitHub.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scout_core.gmail import EmailMessage
from scout_core.pipeline.evaluate import evaluate_job
from scout_core.sources.email_alerts import parse_alert

NOW = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)


def _msg(sender, subject, *, text=None, html=None):
    return EmailMessage("m1", "t1", NOW, sender, subject, html, text)


# ------------------------------------------------------------------ format tests (invented content)

LINKEDIN_TEXT = """Your job alert for ai engineer in Hyderabad
Example AI Engineer
Examplecorp Labs
Hyderabad
Apply with resume & profile
View job: https://www.linkedin.com/comm/jobs/view/1000000001/?trackingId=x

---------------------------------------------------------

Example Backend Developer
Sample Systems
Banjara Hills
View job: https://www.linkedin.com/comm/jobs/view/1000000002/?trackingId=y

---------------------------------------------------------
"""
LINKEDIN_HTML = ('<a href="https://www.linkedin.com/comm/jobs/view/1000000001/?x">Example AI Engineer '
                 'Examplecorp Labs · Hyderabad (Hybrid) Easy Apply</a>')


def test_linkedin_format():
    parser, jobs = parse_alert(_msg("LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", "Example AI Engineer",
                                    text=LINKEDIN_TEXT, html=LINKEDIN_HTML))
    assert parser == "parse_linkedin" and len(jobs) == 2
    a, b = jobs
    assert (a.title, a.company_name, a.location) == ("Example AI Engineer", "Examplecorp Labs", "Hyderabad")
    assert a.apply_url == "https://www.linkedin.com/jobs/view/1000000001/"  # tracking removed
    assert a.extra["workplace"] == "Hybrid" and a.source_job_id == "1000000001"
    # The alert email doesn't say when the job was posted, so no posting time is invented.
    assert a.posted_at is None and a.posted_at_kind == "not_given"
    # "Banjara Hills" isn't a known city, but the alert searched Hyderabad, so it's an India job.
    assert evaluate_job(b).location.bucket.value == "india"


def test_linkedin_non_alert_mail_ignored():
    _, jobs = parse_alert(_msg("LinkedIn <security-noreply@linkedin.com>", "New sign-in", text=LINKEDIN_TEXT))
    assert jobs == []


WELLFOUND_TEXT = """<https://wellfound.com/company/examplecorp/jobs?email_uid=1>
 Example AI Engineer Intern  Examplecorp  / 11-50 Employees  ₹4L – ₹5L • No equity  |  Bengaluru  |  0
years of exp
 Actively Hiring
Learn more
<https://wellfound.com/jobs/9000001-example-ai-engineer-intern?utm=1>

<https://wellfound.com/company/samplelabs/jobs?email_uid=1>
 Example Senior Engineer  Sample Labs  / 51-200 Employees  ₹40L – ₹50L  |  Mumbai, Remote  |  5 years of exp
 Actively Hiring
Learn more
<https://wellfound.com/jobs/9000002-example-senior-engineer?utm=1>
"""


def test_wellfound_format():
    _, jobs = parse_alert(_msg("Wellfound <team@hi.wellfound.com>", "New job: Example AI Engineer Intern at Examplecorp",
                               text=WELLFOUND_TEXT))
    assert len(jobs) == 2
    a = jobs[0]
    assert (a.title, a.company_name, a.locations) == ("Example AI Engineer Intern", "Examplecorp", ["Bengaluru"])
    assert a.apply_url == "https://wellfound.com/jobs/9000001-example-ai-engineer-intern"
    assert a.salary.text == "₹4L – ₹5L" and a.extra["company_size"] == "11-50 Employees"
    assert evaluate_job(jobs[1]).keep is False  # "5 years of exp" is not entry level


def test_wellfound_application_confirmation_ignored():
    _, jobs = parse_alert(_msg("Wellfound <team@hi.wellfound.com>", "Application to Examplecorp successfully submitted",
                               text=WELLFOUND_TEXT))
    assert jobs == []


INTERNSHALA_HTML = """<html><body>
<a href="https://et.internshala.com/CL0/https:%2F%2Finternshala.com%2Fjob%2Fdetail%2Ffresher-remote-example-developer-job-at-examplecorp1700000001%3Futm_source=x/1/abc">
 <span>Actively hiring</span><span>Example Developer</span><span>Examplecorp</span><span>Remote</span>
 <span>Immediately</span><span>₹3,00,000 - 4,00,000 /year</span><span>3 days ago</span><span>Job</span><span>Apply now</span></a>
<a href="https://et.internshala.com/CL0/https:%2F%2Finternshala.com%2Finternship%2Fdetail%2Fexample-analytics-internship-at-samplelabs1700000002/1/abc">
 <span>Example Analytics</span><span>Sample Labs</span><span>Pune</span><span>3 months</span>
 <span>₹ 10,000 /month</span><span>Job offer upto ₹ 4LPA post internship</span><span>Apply now</span></a>
<a href="https://et.internshala.com/CL0/https:%2F%2Ftrainings.internshala.com%2Fpython-course/1/abc">Enroll now</a>
</body></html>"""


def test_internshala_format():
    _, jobs = parse_alert(_msg("Internshala <student@mail.internshala.com>",
                               "12+ new opportunities for 'Information Technology' role", html=INTERNSHALA_HTML))
    assert len(jobs) == 2  # the training ad is not a job
    job, intern = jobs
    assert (job.title, job.company_name, job.source_job_id) == ("Example Developer", "Examplecorp", "1700000001")
    assert job.apply_url == "https://internshala.com/job/detail/fresher-remote-example-developer-job-at-examplecorp1700000001"
    assert intern.employment_type == "Intern"
    # "3 days ago" on the card becomes an estimated posting time 3 days before the email.
    assert job.posted_at_kind == "relative_estimate" and (NOW - job.posted_at).days == 3
    assert intern.posted_at is None  # that card states no date
    # A card is only a snippet: since 2026-11-08 snippet-only jobs are left out (full posting not read).
    assert not evaluate_job(intern).keep


def test_internshala_interview_invite_ignored():
    _, jobs = parse_alert(_msg("Internshala <student@internshala.com>", "Interview Invite from Examplecorp",
                               html=INTERNSHALA_HTML))
    assert jobs == []


INSTAHYRE_HTML = """<html><body>
<a href="https://click4.instahyre.com/CL0/https:%2F%2Fwww.instahyre.com%2Fjob-500001-example-ml-engineer-at-examplecorp%2F%3Futm=1/1/x">
 <span>Example ML Engineer</span><span>Examplecorp</span><span>Work From Home</span><span>Apply here</span>
 <span>Examplecorp builds example software.</span><span>Python</span><span>PyTorch</span></a>
<a href="https://click4.instahyre.com/CL0/https:%2F%2Fwww.instahyre.com%2Fjob-500001-example-ml-engineer-at-examplecorp%2F/1/x">Apply here</a>
<a href="https://click4.instahyre.com/CL0/https:%2F%2Fwww.instahyre.com%2Fjob-500002-example-lead-at-samplelabs%2F/1/x">
 <span>Example Research Lead</span><span>Sample Labs</span><span>10</span><span>Apply here</span><span>About.</span></a>
</body></html>"""


def test_instahyre_format():
    _, jobs = parse_alert(_msg("Instahyre <noreply@instahyre.com>", "Job | Example ML Engineer at Examplecorp",
                               html=INSTAHYRE_HTML))
    assert len(jobs) == 2  # the separate "Apply here" button is not a second job
    a, b = jobs
    assert (a.title, a.company_name, a.location, a.remote) == ("Example ML Engineer", "Examplecorp", "Work From Home", True)
    assert "Skills: Python, PyTorch" in a.description_text
    assert b.location is None  # an unexplained number is ignored, never read as a location


def test_unknown_sender_ignored():
    assert parse_alert(_msg("Someone <hi@example.com>", "Jobs!", text=LINKEDIN_TEXT)) == (None, [])


# ------------------------------------------------------------------ real emails (local only)

PRIVATE = Path(__file__).resolve().parents[3] / "private" / "gmail_samples"
REAL = sorted(PRIVATE.glob("*.json")) if PRIVATE.exists() else []


@pytest.mark.skipif(not REAL, reason="private real alert samples not present (they are never published)")
@pytest.mark.parametrize("path", REAL, ids=lambda p: p.stem)
def test_real_alert_emails(path):
    d = json.loads(path.read_text())
    msg = EmailMessage(d["id"], "", datetime.fromisoformat(d["received_at"]), d["sender"], d["subject"],
                       d["html"], (d["text"] or "").replace("\r\n", "\n"))
    parser, jobs = parse_alert(msg)
    text = (msg.text or "") + (msg.html or "")
    if parser == "parse_linkedin" and "View job:" in (msg.text or ""):
        assert len(jobs) == msg.text.count("View job:")
    if parser == "parse_wellfound" and msg.subject.startswith("New job"):
        assert len(jobs) == len(set(__import__("re").findall(r"wellfound\.com/jobs/(\d+)", msg.text or "")))
    for j in jobs:
        assert j.title and j.company_name and j.apply_url.startswith("https://")
        assert "?" not in j.apply_url  # no tracking parameters stored
        assert j.title not in ("Apply now", "Apply here")
    assert isinstance(text, str)


def test_linkedin_entry_level_filter_is_never_proof():
    """2026-11-08: an "Entry level" LinkedIn alert delivered a 6+ years role. The alert's filter (f_E code
    or search words) is recorded but never makes a title-only job count as entry-level."""
    from scout_core.pipeline.evaluate import evaluate_job

    def alert(query):
        text = LINKEDIN_TEXT + f"See all jobs on LinkedIn:  https://www.linkedin.com/comm/jobs/search-results/?geoId=102713980&{query}\n"
        _, jobs = parse_alert(_msg("LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", "x", text=text))
        return jobs[0]

    for q in ("keywords=ai%20engineer&f_E=1%2C2", "keywords=AI%20Engineer%20Internship%20or%20Entry%20level%20On-site",
              "keywords=AI%20Engineer", "keywords=x&f_E=4"):
        job = alert(q)
        assert job.description_text is None, q  # nothing invented
        ev = evaluate_job(job)
        assert not ev.keep and "couldn't be read" in ev.fresher.reasons[0], q  # title only: left out
    assert alert("keywords=ai%20engineer&f_E=1%2C2").extra["entry_level_alert"] is True  # kept for information


def test_snippets_never_confirm_entry_level_but_can_reject():
    """2026-11-08 rule: entry-level only when the whole posting was read. Email alerts carry snippets."""
    from scout_core.filters.fresher import FresherRules
    from scout_core.pipeline.evaluate import evaluate_job
    from scout_core.types import NormalizedJob

    def job(source, desc, title="AI Engineer", employment_type=None):
        return NormalizedJob(source=source, source_job_id="1", company_name="Acme", title=title,
                             description_text=desc, locations=["Bengaluru"], employment_type=employment_type,
                             apply_url="https://example.com/job/1")

    rules = FresherRules()
    # A snippet that sounds fresher-friendly is not enough: snippet-only jobs are left out entirely.
    for src, desc, kind in (("internshala_alert", "Job offer upto ₹6LPA post internship", "Intern"),
                            ("instahyre_alert", "Freshers welcome. Python, LLM.", None),
                            ("wellfound_alert", "0 years of experience required.", None)):
        ev = evaluate_job(job(src, desc, employment_type=kind), fresher_rules=rules)
        assert not ev.keep and "couldn't be read" in ev.fresher.reasons[0], src
    # A snippet can still reject.
    assert evaluate_job(job("wellfound_alert", "4 years of experience required.")).keep is False
    # A full company-board posting that says so passes the rules, then waits for the second reader
    # (Gemini, whole posting); without the second reading it would be confirmed directly.
    v = evaluate_job(job("greenhouse", "We hire freshers from the 2026 batch. " * 5)).fresher
    assert v.experience_unclear is True and "waiting for the full-text check" in v.reasons[0]
    v = evaluate_job(job("greenhouse", "We hire freshers from the 2026 batch. " * 5),
                     fresher_rules=FresherRules(second_reading=False)).fresher
    assert v.experience_unclear is False and v.confidence.value == "high"


def test_linkedin_section_footer_is_not_a_job():
    """Real 2026-11 layout: the 'other alerts' section starts right after the first section's footer."""
    text = ("Your job alert for GenAI Engineer in India\n\nAI Engineer\nAcme AI\nBengaluru\n"
            "View job: https://www.linkedin.com/comm/jobs/view/111/?trk=x\n\n"
            "---------------------------------------------------------\n  \n"
            "See all jobs on LinkedIn:  https://www.linkedin.com/comm/jobs/search-results/?keywords=GenAI+Engineer\n\n"
            "New jobs from your other alerts\n            \nJobs in Hyderabad\n        \n"
            "Customer Support Executive (Voice and Chat) – (Exp: 0-1 year)\nNovetum\nHyderabad\n"
            "Apply with resume & profile\n"
            "View job: https://www.linkedin.com/comm/jobs/view/4487318631/?trackingId=x\n\n"
            "---------------------------------------------------------\n"
            "See all jobs https://www.linkedin.com/jobs/search-results/?k=1\nEdit alert https://www.linkedin.com/jobs/jam/manage/1\n\n"
            "ML Engineer\nBeta Labs\nPune\nView job: https://www.linkedin.com/comm/jobs/view/222/\n")
    _, jobs = parse_alert(_msg("LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", "x", text=text))
    got = [(j.title, j.company_name, j.location) for j in jobs]
    assert got == [("AI Engineer", "Acme AI", "Bengaluru"),
                   ("Customer Support Executive (Voice and Chat) – (Exp: 0-1 year)", "Novetum", "Hyderabad"),
                   ("ML Engineer", "Beta Labs", "Pune")]


def test_board_job_without_description_is_left_out():
    from scout_core.pipeline.evaluate import evaluate_job
    from scout_core.types import NormalizedJob
    job = NormalizedJob(source="lever", source_job_id="1", company_name="100ms", title="Security Engineer - AI Agents",
                        description_text=None, locations=["Bengaluru"], apply_url="https://jobs.lever.co/x/1")
    ev = evaluate_job(job)
    assert not ev.keep and "no description" in ev.fresher.reasons[0]

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


def test_snippets_confirm_only_with_explicit_employer_text_and_can_reject():
    """2026-11-08 rule: entry-level only when the whole posting was read. Refined 2026-11-14: a snippet
    quoting the employer's own fresher wording ("Freshers welcome", "0 years") counts; vaguer text doesn't."""
    from scout_core.filters.fresher import FresherRules
    from scout_core.pipeline.evaluate import evaluate_job
    from scout_core.types import NormalizedJob

    def job(source, desc, title="AI Engineer", employment_type=None):
        return NormalizedJob(source=source, source_job_id="1", company_name="Acme", title=title,
                             description_text=desc, locations=["Bengaluru"], employment_type=employment_type,
                             apply_url="https://example.com/job/1")

    rules = FresherRules()
    # Vague or weak snippets are not enough: those jobs are left out entirely.
    for src, desc, kind in (("internshala_alert", "Job offer upto ₹6LPA post internship", "Intern"),
                            ("instahyre_alert", "Junior role. Python, LLM.", None),
                            ("wellfound_alert", "Build agents with our team.", None)):
        ev = evaluate_job(job(src, desc, employment_type=kind), fresher_rules=rules)
        assert not ev.keep and "couldn't be read" in ev.fresher.reasons[0], src
    # The employer's explicit words do count.
    for src, desc in (("instahyre_alert", "Freshers welcome. Python, LLM."),
                      ("wellfound_alert", "0 years of experience required.")):
        ev = evaluate_job(job(src, desc), fresher_rules=rules)
        assert ev.keep and not ev.fresher.experience_unclear and "quoted from the posting" in ev.fresher.reasons[0], src
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


def test_linkedin_search_header_with_html_does_not_shift_fields():
    """2026-11-12 layout: the plain text repeats the search as raw HTML above the first job. It became the
    title, the real title became the company and the company the location (e.g. 'AI Engineer' @ 'Com Olho')."""
    text = ('Your job alert for ai engineer in Hyderabad\n'
            '<strong class="font-bold" style="font-weight: 600;">ai engineer</strong> jobs in Hyderabad\n'
            'Jr. Machine Learning Engineer\nDigital Roundabout Pte Ltd\nHyderabad\n'
            'View job: https://www.linkedin.com/comm/jobs/view/333/?trk=x\n\n'
            '---------------------------------------------------------\n')
    _, jobs = parse_alert(_msg("LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", "x", text=text))
    assert [(j.title, j.company_name, j.location) for j in jobs] == [
        ("Jr. Machine Learning Engineer", "Digital Roundabout Pte Ltd", "Hyderabad")]


def _alert_job(title):
    from scout_core.types import NormalizedJob
    return NormalizedJob(source="linkedin_alert", source_job_id=title, company_name="Acme", title=title,
                         description_text=None, locations=["Hyderabad"],
                         apply_url="https://www.linkedin.com/jobs/view/1/")


def test_alert_title_written_by_employer_can_confirm_fresher():
    """2026-11-14: the employer's own title ('Freshers', 'Trainee', '0-1 year') is evidence; it is shown
    without a second reading (there is no fuller text to read)."""
    for title in ("AI Engineer - Freshers", "Graduate Engineer Trainee - ML", "Machine Learning Engineer (0-1 years)",
                  "AI Engineer Campus Hire 2026"):
        ev = evaluate_job(_alert_job(title))
        assert ev.keep, title
        assert not ev.fresher.experience_unclear and "title written by the employer" in ev.fresher.reasons[0], title


def test_alert_title_without_proof_is_still_left_out():
    """A bare AI title, weak words ('Associate', 'Junior', 'Engineer I'), or a fresher word next to a
    seniority word never get through from a title-only alert."""
    for title in ("AI Engineer", "Associate AI/ML Engineer", "Junior Data Scientist", "AI Developer 1",
                  "Applied Scientist I", "Senior AI Engineer (Freshers can apply)", "AI Engineer (2-4 years)",
                  "Graduate Research Assistant - NLP"):
        ev = evaluate_job(_alert_job(title))
        assert not ev.keep, title


# ------------------------------------------------------------------ Indeed / hirist (2026-11-14, invented content)

INDEED_DIGEST = """Indeed Job Alert
3 new machine learning fresher vacancies
Jobs 1-3 of 3 new jobs
See matching results on Indeed: https://in.indeed.com/jobs?q=machine+learning+fresher&from=ja
ML Engineer
Example Tech - Coimbatore, Tamil Nadu
₹15,000 - ₹18,000 a month
Easily apply
Experience:* Fresher to 1 Year. Good understanding of Python and machine learning.
Just posted
https://in.indeed.com/rc/clk/dl?jk=3b954430d4016425&from=ja&qd=x
AI Engineer
Sample Labs - Hyderabad, Telangana
Build LLM features for enterprise customers.
2 days ago
https://in.indeed.com/rc/clk/dl?jk=4db3dc6738c57e9f&from=ja&qd=y
Machine Learning Engineer
Demo Corp - Bengaluru, Karnataka
3+ years of experience building machine learning models in production.
30+ days ago
https://in.indeed.com/rc/clk/dl?jk=0b0b7c9b66fdd38f&from=ja
Do not share this email
Manage job alerts: https://subscriptions.indeed.com?token=x
"""


def test_indeed_digest_format_and_snippet_evidence():
    _, jobs = parse_alert(_msg("Indeed <donotreply@jobalert.indeed.com>", "ML Engineer at Example Tech", text=INDEED_DIGEST))
    assert [(j.title, j.company_name, j.location) for j in jobs] == [
        ("ML Engineer", "Example Tech", "Coimbatore, Tamil Nadu"),
        ("AI Engineer", "Sample Labs", "Hyderabad, Telangana"),
        ("Machine Learning Engineer", "Demo Corp", "Bengaluru, Karnataka")]
    a, b, c = jobs
    assert a.apply_url == "https://in.indeed.com/viewjob?jk=3b954430d4016425"  # no tracking link stored
    assert a.posted_at == NOW and b.posted_at.day == NOW.day - 2
    # The employer's own words in the snippet confirm a fresher role; no proof = left out; 3+ years = dropped.
    ea, eb, ec = (evaluate_job(j) for j in jobs)
    assert ea.keep and not ea.fresher.experience_unclear and "quoted from the posting" in ea.fresher.reasons[0]
    assert not eb.keep and "only a title or snippet" in eb.drop_reasons[0]
    assert not ec.keep and "3+ years" in ec.drop_reasons[0]


HIRIST_SINGLE = """<html><body><div>Get App</div><div>Hi Test User,</div>
<div>Opportunity at Acme AI looks like a strong fit.</div>
<div>AI Engineer</div><div>Acme AI</div><div>Python</div><div>Location: Bangalore/Pune</div>
<div>Experience: 0 - 1 Years</div>
<div><a href="https://postoffice.hirist.tech/CL0/https:%2F%2Fwww.hirist.tech%2Fone-click-apply%3FjobId=1626000/1/x">Apply Now</a></div>
<div>Early applicants are 3x more likely to get noticed</div>
<div>We are hiring freshers from the 2026 batch to build LLM agents.</div>
<div>Best regards, hirist</div></body></html>"""

HIRIST_DIGEST = """<html><body>Hello Test,<br>
06/11 - <a href="https://postoffice.hirist.tech/CL0/https:%2F%2Fwww.hirist.tech%2Fj%2Facme-ai-engineer-1626001.html/1/x">Acme - AI Engineer (0-1 yrs)</a> - Bangalore<br>
05/11 - <a href="https://postoffice.hirist.tech/CL0/https:%2F%2Fwww.hirist.tech%2Fj%2Fdata-scientist-1626002.html/1/x">Data Scientist - NLP (2-5 yrs)</a> - Chennai<br>
</body></html>"""


def test_hirist_single_job_is_a_full_posting():
    _, jobs = parse_alert(_msg('"hirist.tech" <info@hirist.tech>', "Hiring | AI Engineer at Acme AI", html=HIRIST_SINGLE))
    (j,) = jobs
    assert (j.title, j.company_name, j.locations, j.source_job_id) == ("AI Engineer", "Acme AI", ["Bangalore", "Pune"], "1626000")
    assert j.source == "hirist_email" and "2026 batch" in j.description_text
    ev = evaluate_job(j)  # judged like a board job: kept, then waits for the second reader
    assert ev.keep and ev.fresher.experience_unclear


def test_hirist_digest_reads_the_listed_experience_range():
    _, jobs = parse_alert(_msg('"hirist.tech" <info@hirist.tech>', "10+ Matching Jobs", html=HIRIST_DIGEST))
    assert [(j.title, j.location, j.source_job_id) for j in jobs] == [
        ("Acme - AI Engineer", "Bangalore", "1626001"), ("Data Scientist - NLP", "Chennai", "1626002")]
    keep, drop = (evaluate_job(j) for j in jobs)
    assert keep.keep and not keep.fresher.experience_unclear
    assert not drop.keep and "2-5 years" in drop.drop_reasons[0]

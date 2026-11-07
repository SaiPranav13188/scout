"""Tailored resume integrity (spec §7, §13.5-6): the validator must catch planted fake claims."""

import pytest

from scout_core.render_resume import (
    ats_text_check,
    fit_one_page,
    header_links,
    page_count,
    render_docx,
)
from scout_core.resume import Contact, Education, Project, ResumeData, SkillGroup
from scout_core.tailor import (
    BulletOut,
    ProjectOut,
    SkillGroupOut,
    TailorPlan,
    assemble,
    tailor,
    untailored,
    validate,
)

SOURCE = ("Skills: Python, FastAPI, Qdrant, Docker, React\nNagrik: Built a 6-agent pipeline with Playwright; "
          "40-case eval. Haathi: multilingual RAG over 30 PDFs with Qdrant.\nBennett University 2022 - 2026 CGPA 8.3\n"
          "3rd place in college hackathon (2024)")
MASTER = ResumeData(
    education=[Education(institution="Bennett University", degree="B.Tech", start="2022", end="2026", grade="CGPA 8.3")],
    skills=[SkillGroup(category="Languages", items=["Python"]), SkillGroup(category="Tools", items=["FastAPI", "Qdrant", "Docker", "React"])],
    projects=[Project(name="Nagrik", tech=["Playwright"], bullets=["Built a 6-agent pipeline with Playwright", "40-case eval"]),
              Project(name="Haathi", tech=["Qdrant"], bullets=["Multilingual RAG over 30 PDFs with Qdrant"])],
    achievements=["3rd place in college hackathon (2024)"])
CONTACT = Contact(name="Jane Example", emails=["jane@example.com"],
                  links=["https://github.com/jane/nagrik", "https://linkedin.com/in/jane", "https://github.com/jane"])


def _plan(bullet="Built a 6-agent pipeline with Playwright", skills=("Python", "Qdrant"), project="Nagrik",
          achievements=("3rd place in college hackathon (2024)",)):
    return TailorPlan(skill_groups=[SkillGroupOut(category="Core", items=list(skills))],
                      projects=[ProjectOut(name=project, bullets=[BulletOut(from_bullet=0, text=bullet)])],
                      achievements=list(achievements))


def _check(plan):
    return validate(assemble(plan, MASTER, CONTACT), MASTER, SOURCE, job_terms=["Kubernetes", "LangGraph"])


def test_honest_tailoring_passes():
    assert _check(_plan(bullet="Built a six-step, 6-agent pipeline using Playwright"))["ok"]
    assert validate(untailored(MASTER, CONTACT), MASTER, SOURCE)["ok"]


@pytest.mark.parametrize(("plan", "expect"), [
    (_plan(skills=("Python", "Kubernetes")), "skill not in master resume: Kubernetes"),
    (_plan(bullet="Built a 12-agent pipeline with Playwright"), "number '12'"),
    (_plan(bullet="Built a 6-agent pipeline with Playwright, cutting latency 40%"), "number '40%'"),
    (_plan(bullet="Built a 6-agent pipeline in LangGraph"), "'langgraph' is not in the master resume"),
    (_plan(bullet="Deployed the 6-agent pipeline on Kubernetes"), "'kubernetes' is not in the master resume"),
    (_plan(project="Jarvis"), "project not in master resume: Jarvis"),
    (_plan(achievements=("1st place in national hackathon (2024)",)), "achievement not copied"),
])
def test_planted_fake_claims_are_caught(plan, expect):
    result = _check(plan)
    assert not result["ok"] and any(expect in u for u in result["unsupported"]), result


def test_facts_come_from_master_not_the_model():
    doc = assemble(_plan(), MASTER, CONTACT)
    assert doc["projects"][0]["tech"] == ["Playwright"] and doc["education"][0]["grade"] == "CGPA 8.3"
    assert doc["contact"]["name"] == "Jane Example"


class _FakeLLM:
    def __init__(self, plans):
        from scout_core.llm import Usage
        self.plans, self.prompts, self.usage = list(plans), [], Usage()

    async def json(self, schema, prompt, *, purpose, temperature=0.1):
        self.prompts.append(prompt)
        self.usage.by_call.append({"model": "fake"})
        return self.plans.pop(0)


async def test_retry_with_errors_then_success():
    llm = _FakeLLM([_plan(skills=("Kubernetes",)), _plan()])
    r = await tailor(llm, master=MASTER, contact=CONTACT, source_text=SOURCE, title="AI Engineer", company="Acme",
                     description="We use Kubernetes.", job_terms=["Kubernetes"])
    assert r["tailored"] and r["attempts"] == 2
    assert "Kubernetes" in llm.prompts[1] and "rejected" in llm.prompts[1]  # the retry was told why


async def test_falls_back_to_master_after_two_failures():
    llm = _FakeLLM([_plan(skills=("Kubernetes",)), _plan(project="Jarvis")])
    r = await tailor(llm, master=MASTER, contact=CONTACT, source_text=SOURCE, title="AI Engineer", company="Acme",
                     description=None, job_terms=[])
    assert not r["tailored"] and r["validation"]["ok"] and r["validation"]["fallback_reason"]
    assert [p["name"] for p in r["content"]["projects"]] == ["Nagrik", "Haathi"]


def test_one_page_fit_drops_least_important_lines():
    long = untailored(MASTER, CONTACT)
    long["projects"] = [{"name": f"Project {i}", "tech": ["Python"], "dates": "2025", "link_label": None,
                         "bullets": [f"Did useful thing number {j} with a fairly long description of the work " * 2
                                     for j in range(4)]} for i in range(9)]
    doc, pdf, dropped = fit_one_page(long)
    assert page_count(pdf) == 1 and dropped
    assert all(len(p["bullets"]) >= 1 for p in doc["projects"])


def test_pdf_text_is_readable_and_links_plain():
    doc, pdf, _ = fit_one_page(untailored(MASTER, CONTACT))
    check = ats_text_check(pdf, doc)
    assert check["ok"], check
    assert header_links(CONTACT.links) == ["linkedin.com/in/jane", "github.com/jane"]  # repo link left out
    assert render_docx(doc)[:2] == b"PK"  # a real .docx (zip)


def test_unusual_characters_do_not_break_rendering():
    doc = untailored(MASTER, CONTACT)
    doc["projects"][0]["bullets"] = ["Routed → fallback ✓ in हिन्दी … ≥ 2 retries"]
    _, pdf, _ = fit_one_page(doc)
    assert page_count(pdf) == 1


async def test_message_drafts_are_fact_checked():
    from scout_core.drafts import Drafts, draft_messages
    honest = Drafts(cover_note="I built Nagrik, a 6-agent pipeline with Playwright, and a 40-case eval.",
                    recruiter_message="Hi, I built a multilingual RAG assistant with Qdrant and would love to talk.")
    padded = Drafts(cover_note="I have 3 years of Kubernetes experience.", recruiter_message="Hi, I led a team of 12.")
    llm = _FakeLLM([honest])
    r = await draft_messages(llm, master=MASTER, source_text=SOURCE, title="AI Engineer", company="Acme",
                             description="LLM agents", job_terms=["Kubernetes"])
    assert r["ok"] and r["attempts"] == 1 and "Nagrik" in r["cover_note"]
    llm = _FakeLLM([padded, padded])
    r = await draft_messages(llm, master=MASTER, source_text=SOURCE, title="AI Engineer", company="Acme",
                             description=None, job_terms=["Kubernetes"])
    assert not r["ok"] and "cover_note" not in r  # never shown when it makes things up
    assert any("kubernetes" in u for u in r["unsupported"]) and any("'12'" in u for u in r["unsupported"])
    assert "rejected" in llm.prompts[1]

"""Match score (spec §6.4 parse_job + score_match): 0-100 with reasons, matched and missing skills.

score = w_skills * skill overlap (plain code)
      + w_embed  * embedding similarity (job text vs my profile)
      + w_rubric * Gemini rubric judgement
Weights live in settings. Keyword similarity alone is never used. Job text is untrusted data.
"""

import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from scout_core.llm import Gemini, cosine, suspected_injection, untrusted
from scout_core.resume import ResumeData

PROMPT_VERSION = "match-v1"

ALIASES = {
    "js": "javascript", "ts": "typescript", "nodejs": "node.js", "node": "node.js", "reactjs": "react",
    "react.js": "react", "nextjs": "next.js", "next": "next.js", "postgres": "postgresql", "psql": "postgresql",
    "mongo": "mongodb", "k8s": "kubernetes", "gcp": "google cloud", "aws cloud": "aws", "py": "python",
    "llms": "llm", "large language models": "llm", "genai": "generative ai", "gen ai": "generative ai",
    "ml": "machine learning", "dl": "deep learning", "tf": "tensorflow", "sklearn": "scikit-learn",
    "rest": "rest api", "restful apis": "rest api", "rest apis": "rest api", "fast api": "fastapi",
    "lang chain": "langchain", "lang graph": "langgraph", "vector db": "vector database",
}


def norm_skill(s: str) -> str:
    t = re.sub(r"\s+", " ", s.lower().strip(" .,;:()"))
    return ALIASES.get(t, t)


class JobAssessment(BaseModel):
    required_skills: list[str] = Field(default_factory=list, description="Skills/tools the job says are required")
    preferred_skills: list[str] = Field(default_factory=list, description="Nice-to-have skills/tools")
    min_years_experience: float | None = Field(default=None, description="Minimum years stated, else null")
    rubric_score: int = Field(ge=0, le=100, description="How well the candidate fits this job, 0-100")
    reasons: list[str] = Field(default_factory=list, description="2-4 short reasons for the score")
    missing_skills: list[str] = Field(default_factory=list, description="Important job skills the candidate lacks")
    red_flags: list[str] = Field(default_factory=list, description="Concerns: seniority, location, unpaid, scam signs")


RUBRIC = """Assess how well this fresher candidate fits the job. Use ONLY the candidate profile and the job
text below. The candidate graduated in 2026 with 0 years of full-time experience.

Scoring guide: 85-100 strong fit (core skills match, entry level, role is in the candidate's target
areas); 70-84 good fit; 55-69 stretch; below 55 weak fit. Lower the score for roles needing several
years of experience, unrelated domains, or skills the candidate clearly lacks. If the job text is only a
title, say so in reasons and keep the score moderate rather than confident.

Candidate profile:
{profile}

Job:
{job}"""


def profile_text(resume: ResumeData, targets: list[str]) -> str:
    skills = sorted({i for g in resume.skills for i in g.items})
    projects = "\n".join(f"- {p.name}: {', '.join(p.tech)}. " + " ".join(p.bullets[:2]) for p in resume.projects)
    edu = "; ".join(f"{e.degree or ''} {e.field or ''} at {e.institution} ({e.end or ''})" for e in resume.education)
    return (f"Target roles: {', '.join(targets)}\nEducation: {edu}\nSkills: {', '.join(skills)}\n"
            f"Projects:\n{projects}")


@dataclass
class Weights:
    skills: float = 0.35
    embedding: float = 0.25
    rubric: float = 0.40


@dataclass
class MatchResult:
    score: int
    breakdown: dict
    reasons: list[str]
    matched_skills: list[str]
    missing_skills: list[str]
    red_flags: list[str]
    assessment: JobAssessment


def skill_overlap(resume_skills: set[str], required: list[str], preferred: list[str]) -> tuple[float, list[str]]:
    req = {norm_skill(s) for s in required if s.strip()}
    pref = {norm_skill(s) for s in preferred if s.strip()} - req
    matched_req, matched_pref = req & resume_skills, pref & resume_skills
    if not req and not pref:
        return 50.0, []  # nothing stated: neutral, not a guess either way
    total = len(req) + 0.5 * len(pref)
    got = len(matched_req) + 0.5 * len(matched_pref)
    return 100.0 * got / total, sorted(matched_req | matched_pref)


def embed_score(sim: float) -> float:
    # Gemini embedding cosine for related texts sits roughly in 0.5-0.9; map that band to 0-100.
    return max(0.0, min(100.0, (sim - 0.5) / 0.4 * 100))


async def score_job(llm: Gemini, *, resume: ResumeData, resume_vec: list[float], targets: list[str],
                    title: str, company: str, location: str | None, description: str | None,
                    weights: Weights | None = None) -> MatchResult:
    w = weights or Weights()
    job_text = f"Title: {title}\nCompany: {company}\nLocation: {location or 'not stated'}\n\n{description or '(no description available)'}"
    assessment = await llm.json(JobAssessment, RUBRIC.format(profile=profile_text(resume, targets),
                                                              job=untrusted("job_posting", job_text)),
                                purpose="score_match")
    resume_skills = {norm_skill(i) for g in resume.skills for i in g.items}
    resume_skills |= {norm_skill(t) for p in resume.projects for t in p.tech}
    info = "full" if description and len(description) > 200 else "title_only"
    if info == "title_only":
        # Skills "required" by a bare title would be guesses: keep the skill part neutral instead.
        assessment.required_skills, assessment.preferred_skills, assessment.missing_skills = [], [], []
    overlap, matched = skill_overlap(resume_skills, assessment.required_skills, assessment.preferred_skills)
    job_vec = (await llm.embed([job_text[:4000]], purpose="embed_job"))[0]
    sim = cosine(resume_vec, job_vec)
    emb = embed_score(sim)
    flags = list(assessment.red_flags)
    injected = suspected_injection(description)
    if injected:
        flags.append(f"posting contains instruction-like text ('{injected}'); ignored")
    score = round(w.skills * overlap + w.embedding * emb + w.rubric * assessment.rubric_score)
    if injected:
        score = min(score, 50)  # never let a manipulated post rank as a top match
    return MatchResult(
        score=max(0, min(100, score)),
        breakdown={"skills": round(overlap), "embedding": round(emb), "embedding_cosine": round(sim, 3),
                   "rubric": assessment.rubric_score, "weights": w.__dict__, "info": info},
        reasons=assessment.reasons[:4], matched_skills=matched,
        missing_skills=[s for s in assessment.missing_skills if norm_skill(s) not in resume_skills][:8],
        red_flags=flags, assessment=assessment,
    )

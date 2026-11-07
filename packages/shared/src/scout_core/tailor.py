"""Tailored resume per job (spec §6.4 tailor_resume + validate_resume, §7 integrity rules).

Gemini may only choose, reorder and lightly reword what is already in my master resume. Everything
it returns is checked in plain code before use:
  - every skill listed is one of my skills (alias match);
  - every project / role named is one of mine; its tech, dates and links come from the master data;
  - every bullet points at one of my original bullets, and any tool, skill or number in it must
    appear in my master data (job keywords that aren't true for me fail the resume);
  - achievements and certifications are copied, never written.
Education and contact details are taken from the master data, never from the model.
On failure: one retry with the errors, then the untailored master resume (logged as a fallback).
"""

import re
from typing import Any

from pydantic import BaseModel, Field

from scout_core.llm import Gemini, untrusted
from scout_core.matching import norm_skill
from scout_core.resume import Contact, ResumeData, _numbers

PROMPT_VERSION = "tailor-v1"
MAX_BULLETS_PER_ITEM = 4

# Tools and skills a job ad might mention. Any of these in a reworded bullet must be in my master data.
TECH_TERMS = {
    "python", "java", "javascript", "typescript", "golang", "rust", "c++", "c#", "kotlin", "swift", "scala",
    "ruby", "php", "sql", "nosql", "bash", "react", "react native", "next.js", "nextjs", "vue", "angular",
    "svelte", "node.js", "nodejs", "express.js", "fastapi", "django", "flask", "spring boot", ".net",
    "graphql", "grpc", "websocket", "postgresql", "postgres", "mysql", "mongodb", "redis", "cassandra",
    "dynamodb", "elasticsearch", "opensearch", "kafka", "rabbitmq", "bullmq", "celery", "airflow", "spark",
    "hadoop", "flink", "snowflake", "bigquery", "redshift", "dbt", "pandas", "numpy", "scikit-learn", "sklearn",
    "pytorch", "tensorflow", "keras", "jax", "hugging face", "huggingface", "transformers", "langchain",
    "langgraph", "llamaindex", "crewai", "autogen", "openai", "anthropic", "claude", "gemini", "gpt", "llama",
    "mistral", "vertex ai", "bedrock", "sagemaker", "mlflow", "kubeflow", "vllm", "onnx", "triton",
    "qdrant", "pinecone", "weaviate", "milvus", "chromadb", "faiss", "pgvector", "rag", "mcp", "aws", "gcp",
    "azure", "docker", "kubernetes", "k8s", "terraform", "ansible", "helm", "jenkins", "github actions",
    "gitlab ci", "circleci", "linux", "nginx", "supabase", "firebase", "vercel", "netlify", "heroku",
    "playwright", "selenium", "cypress", "jest", "pytest", "tableau", "power bi", "figma", "opencv",
    "yolo", "spacy", "nltk", "computer vision", "nlp", "reinforcement learning", "fine-tuning", "lora",
    "prometheus", "grafana", "datadog", "sentry", "langfuse", "zod", "prisma", "tailwind", "flutter",
}


class BulletOut(BaseModel):
    from_bullet: int = Field(description="Index (0-based) of the original bullet this is based on")
    text: str


class SkillGroupOut(BaseModel):
    category: str
    items: list[str]


class ProjectOut(BaseModel):
    name: str
    bullets: list[BulletOut]


class ExperienceOut(BaseModel):
    organization: str
    role: str
    bullets: list[BulletOut]


class TailorPlan(BaseModel):
    skill_groups: list[SkillGroupOut]
    projects: list[ProjectOut]
    experience: list[ExperienceOut] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    emphasis: list[str] = Field(default_factory=list, description="2-4 short notes: what was emphasised and why")


PROMPT = """Tailor this fresher's resume for the job below. You may ONLY:
- reorder skill groups and the skills inside them, putting what the job needs first (drop at most a
  few clearly irrelevant skills; never add a skill that is not in the resume);
- reorder projects and roles, and choose up to {max_b} bullets for each, most relevant first;
- lightly reword a bullet for clarity or to use the job's wording, but ONLY for things the original
  bullet already says. Keep every number exactly as written. Never add tools, skills, metrics,
  dates, employers or claims. Each bullet must say which original bullet it came from (from_bullet).
- pick achievements by copying them exactly.
Use the exact project names, role names and organisation names from the resume.

{errors}MASTER RESUME (JSON):
{master}

{job}"""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9+#.]+", " ", s.lower()).strip()


def _has_term(text: str, term: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9+#])", text) is not None


def master_text(master: ResumeData, source_text: str | None) -> str:
    """Everything that counts as 'true about me': the uploaded file text plus the reviewed data."""
    parts = [source_text or "", master.model_dump_json()]
    return _norm(" ".join(parts))


def _bullets_of(master: ResumeData, kind: str, name: str, role: str | None = None) -> list[str] | None:
    if kind == "project":
        item = next((p for p in master.projects if _norm(p.name) == _norm(name)), None)
    else:
        item = next((e for e in master.experience if _norm(e.organization) == _norm(name)
                     and (role is None or _norm(e.role) == _norm(role))), None)
    return None if item is None else item.bullets


def validate(content: dict[str, Any], master: ResumeData, source_text: str | None,
             job_terms: list[str] | None = None) -> dict[str, Any]:
    """Plain-code integrity check of a tailored resume. Any unsupported claim fails it."""
    truth = master_text(master, source_text)
    my_skills = {norm_skill(s) for g in master.skills for s in g.items} | {
        norm_skill(t) for p in master.projects for t in p.tech}
    known_numbers = _numbers(" ".join([source_text or "", master.model_dump_json()]))
    vocab = TECH_TERMS | {_norm(t) for t in (job_terms or []) if 2 <= len(_norm(t)) <= 30}
    problems: list[str] = []

    for g in content.get("skills", []):
        for s in g["items"]:
            if norm_skill(s) not in my_skills and _norm(s) not in truth:
                problems.append(f"skill not in master resume: {s}")

    def check_text(where: str, text: str) -> None:
        t = _norm(text)
        for term in vocab:
            if term and _has_term(t, term) and not _has_term(truth, term):
                problems.append(f"{where}: '{term}' is not in the master resume")
        for num in re.findall(r"(?<![\d.,])\d+(?:[.,]\d+)*%?", text):
            if num.replace(",", "") not in known_numbers:
                problems.append(f"{where}: number '{num}' is not in the master resume")

    for p in content.get("projects", []):
        if _bullets_of(master, "project", p["name"]) is None:
            problems.append(f"project not in master resume: {p['name']}")
            continue
        for b in p["bullets"]:
            check_text(f"project {p['name']}", b)
    for e in content.get("experience", []):
        if _bullets_of(master, "experience", e["organization"], e["role"]) is None:
            problems.append(f"role not in master resume: {e['role']} at {e['organization']}")
            continue
        for b in e["bullets"]:
            check_text(f"{e['role']} at {e['organization']}", b)
    mine = {_norm(a) for a in master.achievements}
    problems += [f"achievement not copied from master: {a[:60]}" for a in content.get("achievements", [])
                 if _norm(a) not in mine]
    certs = {_norm(c) for c in master.certifications}
    problems += [f"certification not in master: {c[:60]}" for c in content.get("certifications", [])
                 if _norm(c) not in certs]
    if content.get("headline") and _norm(content["headline"]) != _norm(master.headline or ""):
        problems.append("headline was changed")
    if content.get("summary") and _norm(content["summary"]) != _norm(master.summary or ""):
        problems.append("summary was changed")
    return {"ok": not problems, "unsupported": sorted(set(problems))}


def untailored(master: ResumeData, contact: Contact) -> dict[str, Any]:
    """The master resume as a renderable document (the safe fallback)."""
    return {
        "contact": contact.model_dump(), "headline": master.headline, "summary": master.summary,
        "education": [e.model_dump() for e in master.education],
        "skills": [g.model_dump() for g in master.skills],
        "projects": [{"name": p.name, "tech": p.tech, "dates": p.dates, "link_label": p.link_label,
                      "bullets": p.bullets[:MAX_BULLETS_PER_ITEM]} for p in master.projects],
        "experience": [{"organization": e.organization, "role": e.role, "start": e.start, "end": e.end,
                        "location": e.location, "bullets": e.bullets[:MAX_BULLETS_PER_ITEM]} for e in master.experience],
        "achievements": list(master.achievements), "certifications": list(master.certifications),
    }


def assemble(plan: TailorPlan, master: ResumeData, contact: Contact) -> dict[str, Any]:
    """Build the document from the plan; anything factual (tech, dates, education) comes from master."""
    doc = untailored(master, contact)
    doc["skills"] = [{"category": g.category, "items": g.items} for g in plan.skill_groups if g.items]
    projects = []
    for p in plan.projects:
        m = next((x for x in master.projects if _norm(x.name) == _norm(p.name)), None)
        projects.append({"name": m.name if m else p.name, "tech": m.tech if m else [], "dates": m.dates if m else None,
                         "link_label": m.link_label if m else None,
                         "bullets": [b.text.strip() for b in p.bullets if b.text.strip()][:MAX_BULLETS_PER_ITEM]})
    doc["projects"] = projects
    experience = []
    for e in plan.experience:
        m = next((x for x in master.experience if _norm(x.organization) == _norm(e.organization)), None)
        experience.append({"organization": m.organization if m else e.organization, "role": m.role if m else e.role,
                           "start": m.start if m else None, "end": m.end if m else None,
                           "location": m.location if m else None,
                           "bullets": [b.text.strip() for b in e.bullets if b.text.strip()][:MAX_BULLETS_PER_ITEM]})
    doc["experience"] = experience if master.experience else []
    doc["achievements"] = plan.achievements
    return doc


async def tailor(llm: Gemini, *, master: ResumeData, contact: Contact, source_text: str | None,
                 title: str, company: str, description: str | None, job_terms: list[str]) -> dict[str, Any]:
    """Returns {"content", "validation", "tailored": bool, "attempts", "emphasis"}."""
    job = untrusted("job_posting", f"Title: {title}\nCompany: {company}\n\n{description or '(title only)'}")
    master_json = master.model_dump_json(exclude_none=True)
    errors: list[str] = []
    for attempt in (1, 2):
        note = ("Your previous answer was rejected for these unsupported claims; fix them:\n- "
                + "\n- ".join(errors[:15]) + "\n\n") if errors else ""
        plan = await llm.json(TailorPlan, PROMPT.format(max_b=MAX_BULLETS_PER_ITEM, errors=note,
                                                        master=master_json, job=job),
                              purpose="tailor_resume", temperature=0.2)
        content = assemble(plan, master, contact)
        result = validate(content, master, source_text, job_terms)
        if result["ok"]:
            return {"content": content, "validation": result, "tailored": True, "attempts": attempt,
                    "emphasis": plan.emphasis[:4]}
        errors = result["unsupported"]
    content = untailored(master, contact)
    return {"content": content, "validation": {**validate(content, master, source_text), "fallback_reason": errors[:15]},
            "tailored": False, "attempts": 2, "emphasis": []}

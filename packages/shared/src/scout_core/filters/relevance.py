"""Role relevance (spec §6.3): boost target roles, down-rank unrelated ones. Lists are editable."""

import re
from dataclasses import dataclass, field

from scout_core.text import fold


@dataclass
class RelevanceRules:
    # role family -> title patterns (regex, matched on the folded title)
    families: dict[str, list[str]] = field(default_factory=lambda: {
        "applied_ai": [r"\bai\b", r"artificial intelligence", r"\baiml\b", r"\bllm", r"gen ?ai", r"agentic", r"\bagents?\b", r"machine learning",
                       r"\bml\b", r"applied scientist", r"\bnlp\b", r"\brag\b", r"prompt engineer",
                       r"ai engineer", r"ai/ml", r"data scien", r"deep learning", r"computer vision",
                       r"\bcv engineer", r"mlops|ml ops", r"\bgenai\b", r"research engineer", r"ai research"],
        "fde": [r"forward[- ]deployed", r"\bfde\b", r"solutions? engineer", r"deployment engineer",
                r"implementation engineer", r"customer engineer"],
        "backend": [r"back[- ]?end", r"\bapi\b", r"platform engineer", r"python developer",
                    r"node(?:\.?js)? developer", r"golang|\bgo developer", r"distributed systems"],
        "fullstack": [r"full[- ]?stack", r"\bmern\b", r"web developer", r"product engineer"],
        "software": [r"software (?:development )?engineer", r"\bsde\b", r"\bswe\b", r"software developer",
                     r"\bdeveloper\b", r"programmer", r"engineer(?:ing)? trainee", r"graduate engineer",
                     r"member of technical staff", r"\bmts\b", r"founding engineer"],
    })
    # Strong boost keywords from the description (skills the user targets)
    skill_keywords: list[str] = field(default_factory=lambda: [
        "python", "fastapi", "node", "typescript", "next.js", "nextjs", "react", "llm", "rag",
        "langchain", "langgraph", "agents", "openai", "gemini", "vector", "postgres", "redis",
        "docker", "aws", "mcp", "embeddings", "django", "flask",
    ])
    unrelated: list[str] = field(default_factory=lambda: [
        r"\bsales\b", r"business development", r"\bbde\b", r"customer support", r"support (?:engineer|executive|associate)",
        r"customer success", r"\bsap\b", r"\bphp\b", r"marketing", r"\bseo\b", r"recruit", r"talent acquisition",
        r"\bhr\b", r"human resources", r"accountant", r"accounts", r"finance", r"controllership", r"legal",
        r"content writer", r"copywriter", r"graphic design", r"video editor", r"mechanical", r"civil",
        r"electrical", r"data entry", r"telecaller", r"\bbpo\b", r"operations executive", r"field executive",
        r"store manager", r"warehouse", r"delivery", r"driver", r"nurse", r"teacher", r"tutor",
        r"\bqa\b manual", r"manual test", r"salesforce admin", r"servicenow", r"mainframe", r"cobol",
    ])


@dataclass
class Relevance:
    family: str          # one of the families, or "other" / "unrelated"
    score: int           # 0-100, plain-code relevance (not the Gemini match score)
    matched: list[str]


def classify_role(title: str, description: str | None, rules: RelevanceRules | None = None) -> Relevance:
    rules = rules or RelevanceRules()
    t = fold(title)
    d = fold(description)

    unrelated_hits = [p for p in rules.unrelated if re.search(p, t)]
    family_hits: dict[str, list[str]] = {}
    for family, patterns in rules.families.items():
        hits = [p for p in patterns if re.search(p, t)]
        if hits:
            family_hits[family] = hits

    skills = [k for k in rules.skill_keywords if re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", d)]

    if unrelated_hits and not family_hits:
        return Relevance("unrelated", 5, [_clean(unrelated_hits[0])])

    if not family_hits:
        if re.search(r"\b(engineer|engineering|developer)\b", t) and not unrelated_hits:
            # e.g. "Application Engineer", "Vision Engineer": technical but outside the target families.
            return Relevance("software", min(60, 40 + 3 * min(len(skills), 6)), skills[:6])
        score = min(40, 10 + 4 * len(skills))
        return Relevance("other", score, skills[:6])

    # Priority order: the most specific target family wins.
    for family in ("applied_ai", "fde", "backend", "fullstack", "software"):
        if family in family_hits:
            base = {"applied_ai": 75, "fde": 72, "backend": 70, "fullstack": 70, "software": 60}[family]
            penalty = 15 if unrelated_hits else 0
            score = max(0, min(100, base + 3 * min(len(skills), 8) - penalty))
            return Relevance(family, score, skills[:6])
    raise AssertionError("unreachable")


def _clean(pattern: str) -> str:
    return re.sub(r"\\b|\(\?:|\)|\\", "", pattern)

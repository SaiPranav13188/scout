"""Fresher-filter eval: does Scout show exactly the postings a fresher can apply to?

Labelled set: evals/fresher_labelled.jsonl, each posting labelled before any run as
  fresher      -> a 2026 graduate with 0 years can apply: should be SHOWN
  experienced  -> asks for more than 1 year / a senior profile: should be HIDDEN
  unclear      -> the posting doesn't say: HIDDEN under my rule (show only confirmed fresher roles)

Three readers are measured on the same postings:
  rules       plain code (scout_core.filters.fresher), "show" = kept and experience confirmed
  gemini      the second reader alone (scout_core.seniority.read_seniority, quote verified)
  production  what the feed does: rules don't reject AND Gemini confirms with a verified quote
"""

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

from scout_core.filters.fresher import Decision, FresherRules, classify

DATA = Path(__file__).resolve().parents[4] / "evals" / "fresher_labelled.jsonl"


@dataclass
class Item:
    id: str
    label: str
    title: str
    company: str
    description: str


@dataclass
class Row:
    item: Item
    rules_show: bool
    rules_drop: bool
    rules_reason: str
    gemini: str | None = None  # fresher_ok | needs_experience | unclear | error
    gemini_reason: str = ""

    @property
    def truth_show(self) -> bool:
        return self.item.label == "fresher"

    @property
    def production_show(self) -> bool | None:
        if self.gemini is None or self.gemini == "error":
            return None if not self.rules_drop else False
        return (not self.rules_drop) and self.gemini == "fresher_ok"


@dataclass
class Metrics:
    n: int
    tp: int
    fp: int
    fn: int
    tn: int
    wrong: list[str] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return (self.tp + self.tn) / self.n if self.n else 0.0

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None


def load(path: Path = DATA) -> list[Item]:
    return [Item(**json.loads(line)) for line in path.read_text().splitlines() if line.strip()]


def rules_row(item: Item, rules: FresherRules | None = None) -> Row:
    v = classify(item.title, item.description, rules or FresherRules())
    drop = v.decision == Decision.DROP
    return Row(item, rules_show=not drop and not v.experience_unclear, rules_drop=drop,
               rules_reason="; ".join(v.reasons)[:160])


def metrics(rows: list[Row], pick) -> Metrics:
    m = Metrics(0, 0, 0, 0, 0)
    for r in rows:
        shown = pick(r)
        if shown is None:
            continue
        m.n += 1
        if shown and r.truth_show:
            m.tp += 1
        elif shown:
            m.fp += 1
            m.wrong.append(f"{r.item.id} SHOWN but {r.item.label}: {r.item.title} @ {r.item.company}")
        elif r.truth_show:
            m.fn += 1
            m.wrong.append(f"{r.item.id} HIDDEN but fresher: {r.item.title} @ {r.item.company}")
        else:
            m.tn += 1
    return m


HELDOUT = DATA.with_name("fresher_heldout.jsonl")


async def run(llm=None, *, delay: float = 0.0, path: Path = DATA) -> list[Row]:
    from scout_core.seniority import read_seniority

    rows = [rules_row(it) for it in load(path)]
    if llm is not None:
        for r in rows:
            try:
                r.gemini, r.gemini_reason = await read_seniority(llm, title=r.item.title, description=r.item.description)
            except Exception as exc:  # noqa: BLE001 - one failed call shouldn't sink the run; it is reported
                r.gemini, r.gemini_reason = "error", f"{type(exc).__name__}: {exc}"[:160]
            if delay:
                await asyncio.sleep(delay)
    return rows


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.0f}%"


def report(rows: list[Row]) -> str:
    counts = {lab: sum(r.item.label == lab for r in rows) for lab in ("fresher", "experienced", "unclear")}
    lines = [f"Postings: **{len(rows)}** (fresher {counts['fresher']}, experienced {counts['experienced']}, "
             f"unclear {counts['unclear']}). Correct = fresher shown, experienced/unclear hidden.", "",
             "| Reader | Judged | Correct | Accuracy | Precision (shown that are fresher) | Recall (fresher shown) | Wrongly shown | Wrongly hidden |",
             "|---|---|---|---|---|---|---|---|"]
    readers = [("Rules only", lambda r: r.rules_show)]
    if any(r.gemini for r in rows):
        readers += [("Gemini only", lambda r: None if r.gemini in (None, "error") else r.gemini == "fresher_ok"),
                    ("**Production (rules + Gemini)**", lambda r: r.production_show)]
    all_m = {}
    for name, pick in readers:
        m = metrics(rows, pick)
        all_m[name] = m
        lines.append(f"| {name} | {m.n} | {m.tp + m.tn} | {_pct(m.accuracy)} | {_pct(m.precision)} | "
                     f"{_pct(m.recall)} | {m.fp} | {m.fn} |")
    errors = sum(r.gemini == "error" for r in rows)
    if errors:
        lines.append(f"\n{errors} Gemini call(s) failed and are left out of the Gemini and production rows.")
    for name, m in all_m.items():
        if m.wrong:
            lines += ["", f"{name.strip('*')}, mistakes:"] + [f"- {w}" for w in m.wrong]
    return "\n".join(lines)


def details(rows: list[Row]) -> list[dict]:
    return [{"id": r.item.id, "label": r.item.label, "title": r.item.title, "rules_show": r.rules_show,
             "rules_reason": r.rules_reason, "gemini": r.gemini, "gemini_reason": r.gemini_reason,
             "production_show": r.production_show} for r in rows]

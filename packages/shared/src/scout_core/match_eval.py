"""Is the match score useful? Compare it with my apply / maybe / no labels (spec §11 Phase 3, §13.1).

Plain code, no LLM. Reports, for each score band floor (55 stretch, 70 good, 85 top):
precision, recall and false positives when "score >= floor" is read as "worth applying";
agreement between the score band and my label; and ranking quality (AUC: how often an
"apply" job scores higher than a "no" job). The plain-code role-fit score is shown next to it
as a baseline: the match score earns its LLM cost only if it beats that.
"""

from dataclasses import dataclass, field

FLOORS = (55, 70, 85)
LABELS = ("apply", "maybe", "no")


@dataclass
class Example:
    title: str
    company: str
    label: str  # apply | maybe | no
    score: int | None  # match score (None: not scored yet)
    baseline: int  # plain-code relevance score


@dataclass
class Report:
    labelled: int
    scored: int
    counts: dict[str, int]
    thresholds: list[dict] = field(default_factory=list)
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)
    agreement: float | None = None
    auc: float | None = None
    baseline_auc: float | None = None
    worst: list[dict] = field(default_factory=list)


def band_label(score: int) -> str:
    """What the score says I'd do: good/top -> apply, stretch -> maybe, below -> no."""
    return "apply" if score >= 70 else "maybe" if score >= 55 else "no"


def auc(pos: list[int], neg: list[int]) -> float | None:
    """Probability that a random positive outranks a random negative (ties count half)."""
    if not pos or not neg:
        return None
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def _ratio(a: int, b: int) -> float | None:
    return a / b if b else None


def evaluate(examples: list[Example], *, maybe_counts_as_apply: bool = False) -> Report:
    scored = [e for e in examples if e.score is not None]
    counts = {lab: sum(e.label == lab for e in examples) for lab in LABELS}
    rep = Report(labelled=len(examples), scored=len(scored), counts=counts)
    if not scored:
        return rep
    positive = {"apply", "maybe"} if maybe_counts_as_apply else {"apply"}

    for floor in FLOORS:
        tp = sum(e.score >= floor and e.label in positive for e in scored)
        fp = sum(e.score >= floor and e.label not in positive for e in scored)
        fn = sum(e.score < floor and e.label in positive for e in scored)
        rep.thresholds.append({"floor": floor, "flagged": tp + fp, "tp": tp, "fp": fp, "fn": fn,
                               "precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn),
                               "false_positive_jobs": [f"{e.title} @ {e.company} ({e.score}, {e.label})"
                                                       for e in scored if e.score >= floor and e.label == "no"]})

    rep.confusion = {mine: {pred: 0 for pred in LABELS} for mine in LABELS}
    for e in scored:
        rep.confusion[e.label][band_label(e.score)] += 1
    rep.agreement = sum(rep.confusion[lab][lab] for lab in LABELS) / len(scored)

    rep.auc = auc([e.score for e in scored if e.label in positive],
                  [e.score for e in scored if e.label == "no"])
    rep.baseline_auc = auc([e.baseline for e in scored if e.label in positive],
                           [e.baseline for e in scored if e.label == "no"])

    gap = {"apply": lambda s: 100 - s, "no": lambda s: s, "maybe": lambda s: abs(s - 62)}
    rep.worst = [{"title": e.title, "company": e.company, "label": e.label, "score": e.score}
                 for e in sorted(scored, key=lambda e: gap[e.label](e.score), reverse=True)
                 if band_label(e.score) != e.label][:8]
    return rep


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.0f}%"


def to_markdown(rep: Report, *, target: int = 40) -> str:
    lines = [f"Labelled jobs: **{rep.labelled}** (apply {rep.counts['apply']}, maybe {rep.counts['maybe']}, "
             f"no {rep.counts['no']}); with a match score: **{rep.scored}**."]
    if rep.labelled < target:
        lines.append(f"\n> Only {rep.labelled} of the {target} labels the spec asks for: treat these numbers as early.")
    if not rep.scored:
        lines.append("\nNothing to measure yet: no labelled job has a match score.")
        return "\n".join(lines)
    lines += ["", "| Treat as worth applying | Jobs flagged | Precision | Recall | False positives (labelled no) |",
              "|---|---|---|---|---|"]
    for t in rep.thresholds:
        lines.append(f"| score ≥ {t['floor']} | {t['flagged']} | {_pct(t['precision'])} | {_pct(t['recall'])} | "
                     f"{len(t['false_positive_jobs'])} |")
    lines += ["", f"Agreement between score band and my label: **{_pct(rep.agreement)}** "
              "(≥70 = apply, 55-69 = maybe, <55 = no).", "",
              "| My label ↓ / score says → | apply | maybe | no |", "|---|---|---|---|"]
    for mine in LABELS:
        row = rep.confusion[mine]
        lines.append(f"| {mine} | {row['apply']} | {row['maybe']} | {row['no']} |")
    lines += ["", f"Ranking (AUC, apply vs no; 50% = coin flip): match score **{_pct(rep.auc)}**, "
              f"plain-code role fit **{_pct(rep.baseline_auc)}**."]
    if rep.worst:
        lines += ["", "Biggest disagreements:"]
        lines += [f"- {w['title']} @ {w['company']}: score {w['score']}, my label {w['label']}" for w in rep.worst]
    return "\n".join(lines)

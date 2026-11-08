"""Matching eval maths, checked by hand on small sets."""

import pytest

from scout_core.match_eval import Example, auc, band_label, evaluate, to_markdown


def _ex(label, score, baseline=50, title="Job"):
    return Example(title=title, company="Co", label=label, score=score, baseline=baseline)


def test_auc_counts_ties_as_half():
    assert auc([90, 80], [10, 20]) == 1.0
    assert auc([10], [90]) == 0.0
    assert auc([50], [50]) == 0.5
    assert auc([], [1]) is None


def test_band_label():
    assert [band_label(s) for s in (90, 70, 69, 55, 54)] == ["apply", "apply", "maybe", "maybe", "no"]


def test_precision_recall_and_false_positives():
    data = [_ex("apply", 90), _ex("apply", 72), _ex("apply", 50), _ex("maybe", 60),
            _ex("no", 75, title="Senior SRE"), _ex("no", 30), _ex("no", None)]
    rep = evaluate(data)
    assert rep.labelled == 7 and rep.scored == 6 and rep.counts == {"apply": 3, "maybe": 1, "no": 3}
    at70 = next(t for t in rep.thresholds if t["floor"] == 70)
    # flagged: 90 apply, 72 apply, 75 no -> precision 2/3; recall 2 of 3 apply
    assert (at70["tp"], at70["fp"], at70["fn"]) == (2, 1, 1)
    assert at70["precision"] == pytest.approx(2 / 3) and at70["recall"] == pytest.approx(2 / 3)
    assert at70["false_positive_jobs"] == ["Senior SRE @ Co (75, no)"]
    at55 = next(t for t in rep.thresholds if t["floor"] == 55)
    assert (at55["tp"], at55["fp"]) == (2, 2)  # the maybe at 60 is not "apply" in the strict view
    # Confusion: apply->[apply, apply, no]; maybe->[maybe]; no->[apply, no]
    assert rep.confusion["apply"] == {"apply": 2, "maybe": 0, "no": 1}
    assert rep.confusion["no"] == {"apply": 1, "maybe": 0, "no": 1}
    assert rep.agreement == pytest.approx(4 / 6)
    # AUC apply {90,72,50} vs no {75,30}: 90 beats both, 72 beats 30, 50 beats 30 -> 4/6
    assert rep.auc == pytest.approx(4 / 6)
    # Disagreements, worst first: a "no" scored 75, then an "apply" scored 50.
    assert [(w["title"], w["score"]) for w in rep.worst] == [("Senior SRE", 75), ("Job", 50)]


def test_maybe_can_count_as_apply():
    rep = evaluate([_ex("maybe", 60), _ex("no", 40)], maybe_counts_as_apply=True)
    at55 = next(t for t in rep.thresholds if t["floor"] == 55)
    assert at55["precision"] == 1.0 and at55["recall"] == 1.0 and rep.auc == 1.0


def test_baseline_compared():
    rep = evaluate([_ex("apply", 80, baseline=20), _ex("no", 40, baseline=90)])
    assert rep.auc == 1.0 and rep.baseline_auc == 0.0


def test_markdown_is_honest_about_small_or_empty_sets():
    empty = to_markdown(evaluate([]))
    assert "Only 0 of the 40" in empty and "Nothing to measure" in empty
    md = to_markdown(evaluate([_ex("apply", 90), _ex("no", 20)]))
    assert "| score ≥ 70 | 1 | 100% | 100% | 0 |" in md and "match score **100%**" in md


def _parts(skills, embedding, rubric):
    return {"skills": skills, "embedding": embedding, "rubric": rubric,
            "weights": {"skills": 0.35, "embedding": 0.25, "rubric": 0.40}}


def test_variant_score_renormalises_weights():
    from scout_core.match_eval import variant_score
    p = _parts(80, 40, 60)
    assert variant_score(p, ("skills",)) == 80
    assert variant_score(p, ("skills", "embedding")) == round((0.35 * 80 + 0.25 * 40) / 0.60)
    assert variant_score(p, ("skills", "embedding", "rubric")) == round(0.35 * 80 + 0.25 * 40 + 0.40 * 60)
    assert variant_score({"skills": 50}, ("skills", "embedding")) is None  # missing part: skipped


def test_ablation_shows_which_part_ranks_well():
    # Embeddings separate apply from no perfectly; the skills part ranks them backwards.
    data = [Example("A", "Co", "apply", 70, 50, _parts(20, 90, 70)),
            Example("B", "Co", "no", 60, 50, _parts(90, 10, 60))]
    rep = evaluate(data)
    by = {a["variant"]: a for a in rep.ablation}
    assert by["Embeddings only"]["auc"] == 1.0
    assert by["Skills overlap only (rules)"]["auc"] == 0.0
    assert by["All three (production score)"]["jobs"] == 2
    assert "What each part adds" in to_markdown(rep)


def test_no_ablation_without_parts():
    assert evaluate([_ex("apply", 90), _ex("no", 20)]).ablation == []

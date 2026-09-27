"""Scoring: per-finding outcomes, per-run counts, pooled rates, consistency.

Everything here is a pure function of a saved run record (see
``evals.pipeline``), so every number in a report can be recomputed from the
raw JSON. Metric definitions are in docs/EVALUATION.md; the names used here
are the same.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable, Optional

from evals.answer_key import AnswerKey, Expectation, Scenario

EXCEPTION = "Exception"
NO_EXCEPTION = "No exception"
NOT_TESTED = "Not tested"
NOT_A_DEFICIENCY = "Not a deficiency"

# A ToE 'Effective' concluded from a single configuration read must state the
# reliance it rests on (fieldwork task rule 4). Heuristic, documented.
RELIANCE_PATTERN = re.compile(
    r"reli(ance|es|ed|y|ant)|\bitgcs?\b|change[- ]management|"
    r"config(uration)? history|aws config",
    re.IGNORECASE,
)

# Outcome labels per mapped finding: (expected result, actual result).
OUTCOMES = {
    (EXCEPTION, EXCEPTION): "correct_exception",
    (EXCEPTION, NO_EXCEPTION): "false_pass",
    (EXCEPTION, NOT_TESTED): "unwarranted_not_tested",
    (NO_EXCEPTION, NO_EXCEPTION): "correct_no_exception",
    (NO_EXCEPTION, EXCEPTION): "false_fail",
    (NO_EXCEPTION, NOT_TESTED): "unwarranted_not_tested",
    (NOT_TESTED, NOT_TESTED): "correct_not_tested",
    (NOT_TESTED, NO_EXCEPTION): "unsupported_pass",
    (NOT_TESTED, EXCEPTION): "unsupported_exception",
}

# Pooled metrics: name -> (numerator count, denominator count).
METRICS: dict[str, tuple[str, str]] = {
    "false_pass_rate": ("false_pass", "expected_exception"),
    "false_fail_rate": ("false_fail", "expected_no_exception"),
    "not_tested_correctness": ("not_tested_agree", "mapped"),
    "unsupported_conclusion_rate": ("unsupported", "expected_not_tested"),
    "unwarranted_not_tested_rate": ("unwarranted_not_tested", "expected_tested"),
    "toe_basis_correctness": ("toe_basis_ok", "config_read_tested"),
    "citation_faithfulness": ("citation_verified", "citation_present"),
    "citation_relevance": ("citation_relevant", "citation_relevance_checked"),
    "coverage": ("areas_covered", "areas_in_scope"),
    "conclusion_accuracy": ("conclusion_correct", "mapped"),
    "exception_count_accuracy": ("exception_count_ok", "exception_count_checked"),
    "deficiency_agreement": ("deficiency_agree", "deficiency_checked"),
    "deficiency_scale_correct": ("scale_ok", "scale_checked"),
}

# Metrics where a higher value is worse (for report wording only).
LOWER_IS_BETTER = {
    "false_pass_rate",
    "false_fail_rate",
    "unsupported_conclusion_rate",
    "unwarranted_not_tested_rate",
}


def _label(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def toe_basis_ok(finding: dict[str, Any]) -> bool:
    """False only for ToE 'Effective' without a stated reliance basis."""
    if _label(finding.get("toe_conclusion")) != "Effective":
        return True
    return bool(RELIANCE_PATTERN.search(str(finding.get("toe_basis") or "")))


def score_finding(
    finding: dict[str, Any],
    mapping: dict[str, Any],
    citation: dict[str, Any],
    key: AnswerKey,
    scenario: Scenario,
) -> dict[str, Any]:
    """Score one finding. Unmatched findings get outcome 'unmatched'."""
    area = mapping.get("area")
    actual = {
        "tod": _label(finding.get("tod_conclusion")),
        "toe": _label(finding.get("toe_conclusion")),
        "result": _label(finding.get("result")),
        "exceptions_noted": finding.get("exceptions_noted"),
        "toe_basis": finding.get("toe_basis"),
    }
    score: dict[str, Any] = {
        "control_id": finding.get("control_id"),
        "area": area,
        "actual": actual,
        "citation": citation,
    }
    exp: Optional[Expectation] = key.expectation(scenario, area) if area else None
    if exp is None:
        score["outcome"] = "unmatched"
        return score
    expected = _label(exp.expected_result)
    point_in_time = key.areas[area].evidence_kind == "point_in_time_config"
    basis_ok = toe_basis_ok(finding)
    tod_ok = exp.acceptable_tod is None or actual["tod"] in {
        _label(v) for v in exp.acceptable_tod
    }
    toe_ok = exp.acceptable_toe is None or actual["toe"] in {
        _label(v) for v in exp.acceptable_toe
    }
    score.update(
        expected={
            "result": expected,
            "acceptable_tod": [_label(v) for v in exp.acceptable_tod or []] or None,
            "acceptable_toe": [_label(v) for v in exp.acceptable_toe or []] or None,
            "expected_exceptions": exp.expected_exceptions,
            "from_default": area not in scenario.areas,
        },
        outcome=OUTCOMES.get((expected, actual["result"]), "invalid_result"),
        point_in_time=point_in_time,
        toe_basis_ok=basis_ok,
        conclusion_correct=(
            actual["result"] == expected and tod_ok and toe_ok and basis_ok
        ),
    )
    if exp.expected_exceptions is not None and actual["result"] != NOT_TESTED:
        noted = actual["exceptions_noted"] or 0
        score["exception_count_ok"] = noted == exp.expected_exceptions
    if citation.get("verified") and key.areas[area].evidence_sources:
        score["citation_relevant"] = (
            citation.get("source") in key.areas[area].evidence_sources
        )
    return score


def area_outcome(scores: Iterable[dict[str, Any]], area: str) -> str:
    """One outcome per area per run: Exception > No exception > Not tested >
    missing (no mapped finding)."""
    results = {s["actual"]["result"] for s in scores if s.get("area") == area}
    for label in (EXCEPTION, NO_EXCEPTION, NOT_TESTED):
        if label in results:
            return label
    return "missing"


def score_deficiencies(
    report: Optional[dict[str, Any]],
    scores: list[dict[str, Any]],
    key: AnswerKey,
    scenario: Scenario,
) -> dict[str, Any]:
    """Deficiency-classification agreement for one run.

    Checked areas: each answer-key area with ``deficiency_acceptable`` for
    which the run's fieldwork *did* raise an Exception (classification is
    scored conditional on fieldwork; a missed exception is already a false
    pass). Agreement: at least one evaluation covers the area's exception
    findings and every covering evaluation's classification is acceptable.
    Also checked: every evaluation that covers only findings the key does not
    expect to be exceptions and classifies them as a deficiency (spurious).
    """
    out: dict[str, Any] = {"checked": [], "spurious": [], "unscored": []}
    if report is None:
        out["status"] = "no report (pipeline stopped before Reporting)"
        return out
    evaluations = report.get("deficiency_evaluations") or []
    area_of = {s["control_id"]: s.get("area") for s in scores}

    for area, exp in scenario.areas.items():
        if not exp.deficiency_acceptable:
            continue
        exc_ids = {
            s["control_id"]
            for s in scores
            if s.get("area") == area and s["actual"]["result"] == EXCEPTION
        }
        if not exc_ids:
            continue
        covering = [
            e for e in evaluations if exc_ids & set(e.get("related_findings") or [])
        ]
        classes = [_label(e.get("classification")) for e in covering]
        acceptable = [_label(c) for c in exp.deficiency_acceptable]
        agree = bool(classes) and all(c in acceptable for c in classes)
        out["checked"].append(
            {
                "area": area,
                "findings": sorted(exc_ids),
                "classifications": classes,
                "acceptable": acceptable,
                "agree": agree,
                "reason": None if classes else "no evaluation covers the exception",
            }
        )

    for e in evaluations:
        cls = _label(e.get("classification"))
        related = list(e.get("related_findings") or [])
        areas = {area_of.get(cid) for cid in related}
        if None in areas or not areas:
            out["unscored"].append(
                {"deficiency_id": e.get("deficiency_id"), "related": related}
            )
            continue
        expects_exception = any(
            (x := key.expectation(scenario, a)) is not None
            and _label(x.expected_result) == EXCEPTION
            for a in areas
        )
        if not expects_exception and cls != NOT_A_DEFICIENCY:
            out["spurious"].append(
                {
                    "deficiency_id": e.get("deficiency_id"),
                    "classification": cls,
                    "areas": sorted(a for a in areas if a),
                }
            )
    scale = report.get("deficiency_scale")
    out["scale"] = {
        "expected": _label(scenario.deficiency_scale),
        "actual": _label(scale) if scale else None,
    }
    return out


def run_counts(
    scores: list[dict[str, Any]],
    deficiencies: dict[str, Any],
    key: AnswerKey,
    scenario: Scenario,
) -> Counter:
    """Numerators and denominators for the pooled metrics, for one run."""
    c: Counter = Counter()
    for s in scores:
        cit = s.get("citation") or {}
        if cit.get("quote_present"):
            c["citation_present"] += 1
            c["citation_verified"] += int(bool(cit.get("verified")))
        if s["outcome"] == "unmatched":
            c["unmatched"] += 1
            continue
        c["mapped"] += 1
        c[s["outcome"]] += 1
        expected = s["expected"]["result"]
        actual = s["actual"]["result"]
        c["expected_exception"] += int(expected == EXCEPTION)
        c["expected_no_exception"] += int(expected == NO_EXCEPTION)
        c["expected_not_tested"] += int(expected == NOT_TESTED)
        c["expected_tested"] += int(expected != NOT_TESTED)
        c["not_tested_agree"] += int((expected == NOT_TESTED) == (actual == NOT_TESTED))
        c["unsupported"] += int(expected == NOT_TESTED and actual != NOT_TESTED)
        if s["point_in_time"] and actual != NOT_TESTED:
            c["config_read_tested"] += 1
            c["toe_basis_ok"] += int(s["toe_basis_ok"])
        c["conclusion_correct"] += int(s["conclusion_correct"])
        if "exception_count_ok" in s:
            c["exception_count_checked"] += 1
            c["exception_count_ok"] += int(s["exception_count_ok"])
        if "citation_relevant" in s:
            c["citation_relevance_checked"] += 1
            c["citation_relevant"] += int(s["citation_relevant"])

    for area, exp in scenario.areas.items():
        if exp.in_scope:
            c["areas_in_scope"] += 1
            c["areas_covered"] += int(area_outcome(scores, area) != "missing")

    for item in deficiencies.get("checked", []):
        c["deficiency_checked"] += 1
        c["deficiency_agree"] += int(item["agree"])
    c["deficiency_checked"] += len(deficiencies.get("spurious", []))
    scale = deficiencies.get("scale")
    if scale and scale.get("actual"):
        c["scale_checked"] += 1
        c["scale_ok"] += int(scale["actual"] == scale["expected"])
    return c


def rates(counts: Counter) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, (num_key, den_key) in METRICS.items():
        num, den = counts.get(num_key, 0), counts.get(den_key, 0)
        out[name] = {"num": num, "den": den, "rate": (num / den) if den else None}
    return out


def consistency(
    per_run_scores: list[list[dict[str, Any]]], scenario: Scenario
) -> dict[str, Any]:
    """Run-to-run consistency of the area outcome (same scenario, N runs).

    For each answer-key area: the outcome in each run (see ``area_outcome``)
    and the share of runs that agree with the most common outcome. The
    scenario value is the mean over its areas. Needs at least two runs.
    """
    n = len(per_run_scores)
    if n < 2:
        return {"runs": n, "rate": None, "areas": {}}
    areas: dict[str, Any] = {}
    for area in scenario.areas:
        outcomes = [area_outcome(scores, area) for scores in per_run_scores]
        modal, count = Counter(outcomes).most_common(1)[0]
        areas[area] = {"outcomes": outcomes, "modal": modal, "agreement": count / n}
    values = [a["agreement"] for a in areas.values()]
    return {
        "runs": n,
        "rate": sum(values) / len(values) if values else None,
        "fully_consistent_areas": sum(1 for v in values if v == 1.0),
        "areas": areas,
    }

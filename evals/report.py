"""Aggregate scored runs and render the markdown summary."""

from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from evals import metrics

METRIC_LABELS = {
    "false_pass_rate": "False-pass rate",
    "false_fail_rate": "False-fail rate",
    "not_tested_correctness": "'Not tested' correctness",
    "unsupported_conclusion_rate": "  unsupported conclusions (no-tool areas concluded)",
    "unwarranted_not_tested_rate": "  unwarranted 'Not tested' (evidence existed)",
    "toe_basis_correctness": "ToE-basis correctness (config reads)",
    "citation_faithfulness": "Citation faithfulness (quote verifies in vault)",
    "citation_relevance": "  citation relevance (quote from the area's tool)",
    "coverage": "Coverage (in-scope areas addressed)",
    "conclusion_accuracy": "Conclusion accuracy (result + ToD/ToE + basis)",
    "exception_count_accuracy": "Exception-count accuracy",
    "deficiency_agreement": "Deficiency-classification agreement",
    "deficiency_scale_correct": "Deficiency scale correct (ICFR vs risk rating)",
}


def fmt(m: Optional[dict[str, Any]]) -> str:
    if not m or m.get("rate") is None:
        return f"n/a ({(m or {}).get('num', 0)}/{(m or {}).get('den', 0)})"
    return f"{m['rate'] * 100:.1f}% ({m['num']}/{m['den']})"


def aggregate(scenario_results: list[dict[str, Any]]) -> dict[str, Any]:
    total: Counter = Counter()
    for s in scenario_results:
        for r in s["runs"]:
            total.update(r["counts"])
    cons = [s["consistency"]["rate"] for s in scenario_results]
    cons = [c for c in cons if c is not None]
    return {
        "counts": dict(total),
        "metrics": metrics.rates(total),
        "consistency": {
            "rate": sum(cons) / len(cons) if cons else None,
            "scenarios": len(cons),
        },
        "runs_completed": sum(
            1 for s in scenario_results for r in s["runs"] if r["pipeline"]["completed"]
        ),
        "runs_total": sum(len(s["runs"]) for s in scenario_results),
    }


def notable(scenario_results: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Every instance behind the headline error metrics, for the report."""
    out: dict[str, list[dict[str, Any]]] = {
        "false_passes": [],
        "false_fails": [],
        "unsupported_conclusions": [],
        "unwarranted_not_tested": [],
        "toe_basis_violations": [],
        "unverified_citations": [],
        "unmatched_findings": [],
        "deficiency_disagreements": [],
    }
    for s in scenario_results:
        for r in s["runs"]:
            where = {"scenario": s["id"], "run": r["run"], "raw_file": r["raw_file"]}
            maps = {m["control_id"]: m for m in r["mapping"]}
            for f in r["finding_scores"]:
                item = {
                    **where,
                    "control_id": f["control_id"],
                    "area": f.get("area"),
                    "actual": f"ToD {f['actual']['tod']}; ToE {f['actual']['toe']}; result {f['actual']['result']}",
                    "expected": (f.get("expected") or {}).get("result"),
                }
                o = f["outcome"]
                if o == "false_pass":
                    out["false_passes"].append(item)
                elif o == "false_fail":
                    out["false_fails"].append(item)
                elif o in ("unsupported_pass", "unsupported_exception"):
                    out["unsupported_conclusions"].append(item)
                elif o == "unwarranted_not_tested":
                    out["unwarranted_not_tested"].append(item)
                if f.get("point_in_time") and not f.get("toe_basis_ok", True):
                    out["toe_basis_violations"].append(
                        {**item, "toe_basis": f["actual"].get("toe_basis")}
                    )
                cit = f.get("citation") or {}
                if cit.get("quote_present") and not cit.get("verified"):
                    out["unverified_citations"].append(item)
                if o == "unmatched":
                    m = maps.get(f["control_id"], {})
                    out["unmatched_findings"].append(
                        {
                            **where,
                            "control_id": f["control_id"],
                            "reason": m.get("unmatched_reason"),
                            "text": (m.get("text") or "")[:120],
                        }
                    )
            d = r["deficiencies"]
            for c in d.get("checked", []):
                if not c["agree"]:
                    out["deficiency_disagreements"].append(
                        {
                            **where,
                            "area": c["area"],
                            "got": ", ".join(c["classifications"]) or "(none)",
                            "acceptable": ", ".join(c["acceptable"]),
                        }
                    )
            for sp in d.get("spurious", []):
                out["deficiency_disagreements"].append(
                    {
                        **where,
                        "area": ", ".join(sp["areas"]),
                        "got": f"{sp['classification']} (spurious: key expects no exception)",
                        "acceptable": "Not a deficiency / no evaluation",
                    }
                )
    return out


def _table(rows: list[list[str]], header: list[str]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for row in rows:
        lines.append("| " + " | ".join(c.replace("|", "\\|") for c in row) + " |")
    return lines


def _usage_lines(usage: dict[str, Any]) -> list[str]:
    if not usage.get("total"):
        return [
            "No token usage was reported"
            + (
                " (replay mode makes no model calls)."
                if usage.get("mode") == "replay"
                else "."
            )
        ]
    t = usage["total"]
    lines = [
        f"- Total tokens: {t.get('total_tokens', 0):,} (prompt {t.get('prompt_tokens', 0):,}, "
        f"completion {t.get('completion_tokens', 0):,}), successful requests "
        f"{t.get('successful_requests', 0):,}."
    ]
    if usage.get("estimated_cost_usd") is not None:
        lines.append(
            f"- Estimated cost: ${usage['estimated_cost_usd']:.2f} at the prices "
            "passed on the command line (not verified against the provider's bill)."
        )
    else:
        lines.append(
            "- Cost not estimated: pass --usd-per-mtok-input / --usd-per-mtok-output "
            "with your provider's current prices."
        )
    return lines


def render_markdown(results: dict[str, Any]) -> str:
    meta = results["meta"]
    key = results["answer_key"]
    agg = results["aggregate"]
    lines: list[str] = []
    title = f"LLM-layer evaluation: {meta['provider']} / {meta['model']}"
    lines += [f"# {title}", ""]
    lines += [f"> **{key['review_status']}**", ""]
    if meta["mode"] == "replay":
        lines += [
            "> **REPLAY MODE.** Canned crew outputs from "
            f"`{meta['replay_fixture']}` stood in for the model. These numbers "
            "test the harness (seeding, mapping, scoring, reporting); they say "
            "nothing about any model.",
            "",
        ]
    lines += _table(
        [
            ["Date (UTC)", meta["started"]],
            ["Mode", meta["mode"]],
            ["Crew model", f"{meta['provider']} / {meta['model']}"],
            ["QA model", meta["qa_model"]],
            ["Scenarios x runs", f"{meta['scenario_count']} x {meta['runs']}"],
            [
                "Runs completed (all gates)",
                f"{agg['runs_completed']}/{agg['runs_total']}",
            ],
            ["On QA rejection", meta["on_qa_reject"]],
            [
                "Answer key",
                f"v{key['version']} ({key['status']}), reviewed_by: {key['reviewed_by'] or 'not set'}",
            ],
            ["Harness", meta["harness_version"]],
            ["Raw outputs", f"`{meta['raw_dir']}`"],
        ],
        ["", ""],
    )
    lines += ["", "## Aggregate", ""]
    rows = []
    for name, label in METRIC_LABELS.items():
        better = "lower" if name in metrics.LOWER_IS_BETTER else "higher"
        rows.append([label, fmt(agg["metrics"].get(name)), better])
    cons = agg["consistency"]
    rows.append(
        [
            "Run-to-run consistency (area outcome)",
            "n/a (needs 2+ runs)"
            if cons["rate"] is None
            else f"{cons['rate'] * 100:.1f}% (mean over {cons['scenarios']} scenarios)",
            "higher",
        ]
    )
    qa = results.get("qa_seeding")
    if qa:
        sc = qa["scores"]
        rows.append(
            ["QA catch rate (seeded bad papers)", fmt(sc["catch_rate"]), "higher"]
        )
        rows.append(
            [
                "  targeted (reason names the control)",
                fmt(sc["targeted_catch_rate"]),
                "higher",
            ]
        )
        rows.append(
            [
                "  QA false-rejection rate (good papers)",
                fmt(sc["false_rejection_rate"]),
                "lower",
            ]
        )
    lines += _table(rows, ["Metric", "Value (num/den)", "Better"])
    lines += [
        "",
        "Rates pool findings over all runs; the denominators are small, so read "
        "each rate with its counts. Definitions: docs/EVALUATION.md.",
        "",
        "## Per scenario",
        "",
    ]
    rows = []
    for s in results["scenarios"]:
        p = s["pooled"]
        c = s["consistency"]["rate"]
        done = sum(1 for r in s["runs"] if r["pipeline"]["completed"])
        rows.append(
            [
                s["id"],
                f"{done}/{len(s['runs'])}",
                fmt(p["false_pass_rate"]),
                fmt(p["false_fail_rate"]),
                fmt(p["not_tested_correctness"]),
                fmt(p["toe_basis_correctness"]),
                fmt(p["citation_faithfulness"]),
                fmt(p["coverage"]),
                fmt(p["deficiency_agreement"]),
                "n/a" if c is None else f"{c * 100:.0f}%",
            ]
        )
    lines += _table(
        rows,
        [
            "Scenario",
            "Completed",
            "False pass",
            "False fail",
            "NT correct",
            "ToE basis",
            "Citations",
            "Coverage",
            "Deficiency",
            "Consistency",
        ],
    )
    lines += ["", "### Area outcomes per run", ""]
    rows = []
    for s in results["scenarios"]:
        for area, exp in s["expected_outcomes"].items():
            outs = [r["area_outcomes"].get(area, "missing") for r in s["runs"]]
            rows.append([s["id"], area, exp, ", ".join(outs)])
    lines += _table(rows, ["Scenario", "Area", "Expected", "Outcome per run"])

    n = results["notable"]
    sections = [
        (
            "False passes (every instance)",
            "false_passes",
            ["scenario", "run", "control_id", "area", "actual"],
        ),
        (
            "False fails",
            "false_fails",
            ["scenario", "run", "control_id", "area", "actual"],
        ),
        (
            "Unsupported conclusions (no evidence tool, yet concluded)",
            "unsupported_conclusions",
            ["scenario", "run", "control_id", "area", "actual"],
        ),
        (
            "Unwarranted 'Not tested' (evidence existed)",
            "unwarranted_not_tested",
            ["scenario", "run", "control_id", "area", "expected"],
        ),
        (
            "ToE-basis violations (ToE Effective from a config read, no reliance stated)",
            "toe_basis_violations",
            ["scenario", "run", "control_id", "area", "toe_basis"],
        ),
        (
            "Citations that did not verify",
            "unverified_citations",
            ["scenario", "run", "control_id", "area", "actual"],
        ),
        (
            "Deficiency classifications outside the key's range",
            "deficiency_disagreements",
            ["scenario", "run", "area", "got", "acceptable"],
        ),
        (
            "Unmatched findings (not scored, listed for review)",
            "unmatched_findings",
            ["scenario", "run", "control_id", "reason", "text"],
        ),
    ]
    for heading, field, cols in sections:
        lines += ["", f"## {heading}", ""]
        items = n.get(field) or []
        if not items:
            lines.append("None.")
            continue
        lines += _table(
            [
                [str(i.get(c) if i.get(c) is not None else "") for c in cols]
                for i in items
            ],
            cols,
        )

    if qa:
        lines += ["", "## QA seeding: per seed", ""]
        rows = [
            [seed, v["kind"], v["defect"], f"{v['rejected']}/{v['runs']}"]
            for seed, v in qa["scores"]["per_seed"].items()
        ]
        lines += _table(rows, ["Seed", "Kind", "Planted defect", "Rejected"])

    lines += ["", "## Pipeline events", ""]
    rows = []
    for s in results["scenarios"]:
        for r in s["runs"]:
            p = r["pipeline"]
            ev = [
                f"P{e['phase']} {e['event']}"
                for e in p.get("events", [])
                if e["event"] != "gate_approved"
            ]
            qa_p = r.get("qa_in_pipeline") or {}
            rej = ", ".join(
                f"P{k}: {v['qa_rejections']}/{v['attempts']}"
                for k, v in sorted(qa_p.items())
            )
            rows.append(
                [
                    s["id"],
                    str(r["run"]),
                    p["final_status"],
                    "; ".join(ev) or "-",
                    rej or "-",
                    p.get("harness_error") or "",
                ]
            )
    lines += _table(
        rows,
        [
            "Scenario",
            "Run",
            "Final status",
            "Events",
            "QA rejections / attempts",
            "Harness error",
        ],
    )
    lines += ["", "## Token usage and cost", ""]
    lines += _usage_lines(results.get("usage") or {})
    lines += [
        "",
        "## Reading this report",
        "",
        "- Scores are against a draft answer key until the owner signs it off (see the banner).",
        "- Findings are mapped to answer-key areas by keywords and evidence source; each run's mapping is in its raw JSON (`mapping`) and unmatched findings are listed above.",
        "- AWS is simulated in moto; small n; LLM output varies between runs. See docs/EVALUATION.md, Limitations.",
        "",
    ]
    return "\n".join(lines)

"""Unit tests for the LLM-eval mapping, metrics and QA seeding (no model, no AWS
except where a test says so)."""

from __future__ import annotations

import json
import os
from collections import Counter
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from evals import metrics  # noqa: E402
from evals.answer_key import load_answer_key  # noqa: E402
from evals.mapping import map_finding, racm_controls  # noqa: E402
from evals.pipeline import ReplayCrew, expand_racm, resolve_placeholders  # noqa: E402
from evals.qa_seeding import (  # noqa: E402
    SEEDS,
    good_papers,
    replay_reviewer,
    run_qa_seeding,
    score_qa,
    seed_papers,
)
from evals.report import fmt  # noqa: E402

from swarm.schema import WorkingPaperSchema  # noqa: E402

KEY = load_answer_key()
S01 = KEY.scenario("s01-sox-access-no-password-policy")
S06 = KEY.scenario("s06-s3-account-bpa-mitigates")
S10 = KEY.scenario("s10-root-and-user-mfa")
S13 = KEY.scenario("s13-clean-account-encryption")

VAULT = {
    "v-pwd": {"source": "aws.iam.get_account_password_policy", "payload": "..."},
    "v-mfa": {"source": "aws.iam.list_users_mfa", "payload": "..."},
    "v-s3": {"source": "aws.s3.list_public_buckets", "payload": "..."},
}


def _controls(*items: tuple[str, str]) -> dict:
    racm = expand_racm(
        {"controls": [{"control_id": c, "description": d} for c, d in items]}, "t"
    )
    return racm_controls(racm)


def _finding(control_id: str, result: str = "Not tested", **kw) -> dict:
    tod = kw.pop("tod", "Not tested" if result == "Not tested" else "Effective")
    toe = kw.pop("toe", "Not tested")
    return {
        "control_id": control_id,
        "tod_conclusion": tod,
        "toe_conclusion": toe,
        "result": result,
        "test_conclusion": kw.pop("test_conclusion", ""),
        **kw,
    }


# ── Mapping ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "description,area",
    [
        ("The IAM account password policy enforces length 14.", "password_policy"),
        ("Multi-factor authentication is required for IAM users.", "iam_mfa"),
        ("No S3 bucket is publicly accessible.", "s3_public_access"),
        ("Quarterly user access review signed off.", "access_review"),
        ("Program changes are approved by the CAB.", "change_management"),
        ("CloudTrail is enabled in all regions.", "logging_monitoring"),
    ],
)
def test_mapping_by_control_text(description, area):
    m = map_finding(_finding("X-1"), _controls(("X-1", description)), {}, KEY, S13)
    # Areas outside s13's key still map when they have a default expectation.
    assert m["area"] == area
    assert m["text_source"] == "racm_control"


def test_root_mfa_maps_to_root_not_iam_mfa_even_citing_the_mfa_tool():
    controls = _controls(("R-1", "MFA is enabled on the AWS root account."))
    f = _finding("R-1", "No exception", vault_id_reference="v-mfa")
    m = map_finding(f, controls, VAULT, KEY, S10)
    assert m["area"] == "root_account"
    assert m["scores"]["iam_mfa"]["evidence_match"] is True


def test_evidence_source_breaks_a_text_tie():
    controls = _controls(("A-1", "Logical access to the console is controlled."))
    unmatched = map_finding(_finding("A-1"), controls, VAULT, KEY, S01)
    assert unmatched["area"] is None
    assert unmatched["unmatched_reason"] == "no_keyword_or_evidence_match"
    cited = map_finding(
        _finding("A-1", "Exception", vault_id_reference="v-mfa"),
        controls,
        VAULT,
        KEY,
        S01,
    )
    assert cited["area"] == "iam_mfa"


def test_tool_area_not_in_the_scenario_key_is_reported_unmatched():
    controls = _controls(("S-1", "No S3 bucket is public."))
    m = map_finding(_finding("S-1"), controls, VAULT, KEY, S01)
    assert m["area"] is None
    assert m["unmatched_reason"] == "area_not_in_key (s3_public_access)"


def test_control_missing_from_racm_falls_back_to_the_finding_text():
    m = map_finding(
        _finding("GHOST", test_conclusion="Password policy length 6."), {}, {}, KEY, S01
    )
    assert m["area"] == "password_policy"
    assert m["text_source"].startswith("finding_test_conclusion")


def test_specific_keywords_outweigh_generic_ones():
    controls = _controls(("T-1", "Backup of the CloudTrail logs."))  # 5 vs 6
    assert (
        map_finding(_finding("T-1"), controls, {}, KEY, S01)["area"]
        == "logging_monitoring"
    )
    controls = _controls(("T-2", "encrypt backups"))  # 6 vs 5
    assert (
        map_finding(_finding("T-2"), controls, {}, KEY, S01)["area"]
        == "encryption_at_rest"
    )


def test_tie_is_resolved_by_catalogue_order_and_flagged_ambiguous():
    controls = _controls(("T-3", "Monitor deployments"))  # 2 vs 2
    m = map_finding(_finding("T-3"), controls, {}, KEY, S01)
    assert m["area"] == "change_management"
    assert m["ambiguous"] is True
    assert m["tied_areas"] == ["change_management", "logging_monitoring"]


# ── Finding scores ──────────────────────────────────────────────────────────


def _score(f, area, scenario=S01, cit=None):
    return metrics.score_finding(
        f, {"area": area}, cit or {"quote_present": False}, KEY, scenario
    )


@pytest.mark.parametrize(
    "area,result,outcome",
    [
        ("iam_mfa", "No exception", "false_pass"),
        ("iam_mfa", "Exception", "correct_exception"),
        ("iam_mfa", "Not tested", "unwarranted_not_tested"),
        ("access_review", "No exception", "unsupported_pass"),
        ("access_review", "Exception", "unsupported_exception"),
        ("access_review", "Not tested", "correct_not_tested"),
    ],
)
def test_outcome_labels(area, result, outcome):
    assert _score(_finding("C", result), area)["outcome"] == outcome


def test_false_fail_and_no_exception_on_clean_account():
    assert (
        _score(_finding("C", "Exception", tod="Ineffective"), "s3_public_access", S06)[
            "outcome"
        ]
        == "false_fail"
    )
    ok = _score(_finding("C", "No exception"), "s3_public_access", S06)
    assert ok["outcome"] == "correct_no_exception"
    assert ok["conclusion_correct"] is True


def test_toe_effective_needs_a_reliance_basis():
    bare = _finding("C", "No exception", toe="Effective", toe_basis="Observed.")
    relied = _finding(
        "C",
        "No exception",
        toe="Effective",
        toe_basis="Test of one; relies on change-management ITGCs for the period.",
    )
    assert metrics.toe_basis_ok(bare) is False
    assert metrics.toe_basis_ok(relied) is True
    assert metrics.toe_basis_ok(_finding("C", "Not tested")) is True
    s = _score(bare, "s3_public_access", S06)
    assert s["outcome"] == "correct_no_exception"
    assert s["conclusion_correct"] is False  # right result, unqualified ToE


def test_wrong_design_conclusion_is_not_fully_correct():
    # Password policy missing: result Exception but ToD must be Ineffective.
    f = _finding("C", "Exception", tod="Effective", toe="Ineffective")
    s = _score(f, "password_policy")
    assert s["outcome"] == "correct_exception"
    assert s["conclusion_correct"] is False


def test_exception_count_and_citation_relevance():
    f = _finding("C", "Exception", tod="Ineffective", exceptions_noted=40)
    cit = {"quote_present": True, "verified": True, "source": "aws.iam.list_users_mfa"}
    s = _score(f, "iam_mfa", cit=cit)
    assert s["exception_count_ok"] is True and s["citation_relevant"] is True
    wrong_source = {**cit, "source": "aws.s3.list_public_buckets"}
    assert _score(f, "iam_mfa", cit=wrong_source)["citation_relevant"] is False


def test_unmatched_finding_is_not_scored_but_its_citation_counts():
    s = metrics.score_finding(
        _finding("C"),
        {"area": None},
        {"quote_present": True, "verified": False},
        KEY,
        S01,
    )
    assert s["outcome"] == "unmatched"
    c = metrics.run_counts([s], {}, KEY, S01)
    assert c["unmatched"] == 1 and c["mapped"] == 0
    assert (c["citation_present"], c["citation_verified"]) == (1, 0)


# ── Deficiencies, rates, consistency ────────────────────────────────────────


def _scores_for(results: dict[str, tuple[str, str]], scenario=S01):
    out = []
    for cid, (area, res) in results.items():
        f = (
            _finding(cid, res, tod="Ineffective")
            if res == "Exception"
            else _finding(cid, res)
        )
        out.append(_score(f, area, scenario))
    return out


def test_deficiency_agreement_missing_and_spurious():
    scores = _scores_for(
        {"P": ("password_policy", "Exception"), "M": ("iam_mfa", "Exception")}
    )
    report = {
        "deficiency_scale": "ICFR deficiency scale",
        "deficiency_evaluations": [
            {
                "deficiency_id": "D1",
                "related_findings": ["P"],
                "classification": "Significant Deficiency",
            }
        ],
    }
    d = metrics.score_deficiencies(report, scores, KEY, S01)
    by_area = {c["area"]: c for c in d["checked"]}
    assert by_area["password_policy"]["agree"] is True
    assert by_area["iam_mfa"]["agree"] is False  # exception never evaluated
    assert d["scale"] == {
        "expected": "ICFR deficiency scale",
        "actual": "ICFR deficiency scale",
    }

    clean = _scores_for({"S": ("s3_public_access", "Exception")}, S06)
    spurious = metrics.score_deficiencies(
        {
            "deficiency_evaluations": [
                {
                    "deficiency_id": "D",
                    "related_findings": ["S"],
                    "classification": "High",
                }
            ]
        },
        clean,
        KEY,
        S06,
    )
    assert spurious["spurious"][0]["classification"] == "High"
    counts = metrics.run_counts(clean, spurious, KEY, S06)
    assert (counts["deficiency_agree"], counts["deficiency_checked"]) == (0, 1)


def test_no_report_means_no_deficiency_check():
    d = metrics.score_deficiencies(None, [], KEY, S01)
    assert d["checked"] == [] and "no report" in d["status"]


def test_rates_with_empty_denominator_are_none():
    r = metrics.rates(Counter())
    assert r["false_pass_rate"] == {"num": 0, "den": 0, "rate": None}
    assert fmt(r["false_pass_rate"]) == "n/a (0/0)"
    assert fmt({"num": 1, "den": 4, "rate": 0.25}) == "25.0% (1/4)"


def test_consistency():
    run_a = _scores_for(
        {"P": ("password_policy", "Exception"), "M": ("iam_mfa", "Exception")}
    )
    run_b = _scores_for(
        {"P": ("password_policy", "Exception"), "M": ("iam_mfa", "No exception")}
    )
    c = metrics.consistency([run_a, run_b, run_a], S01)
    assert c["areas"]["password_policy"]["agreement"] == 1.0
    assert c["areas"]["iam_mfa"]["agreement"] == pytest.approx(2 / 3)
    assert c["areas"]["access_review"]["outcomes"] == ["missing"] * 3
    assert c["rate"] == pytest.approx((1 + 2 / 3 + 1) / 3)
    assert metrics.consistency([run_a], S01)["rate"] is None


# ── Replay crew plumbing ────────────────────────────────────────────────────


def test_placeholders_resolve_and_unknown_tool_fails():
    papers = {
        "findings": [
            {"control_id": "A", "vault_id_reference": "@tool:list_iam_users_with_mfa"}
        ]
    }
    out = resolve_placeholders(papers, {"list_iam_users_with_mfa": "abc"})
    assert out["findings"][0]["vault_id_reference"] == "abc"
    assert papers["findings"][0]["vault_id_reference"].startswith(
        "@tool:"
    )  # not mutated
    with pytest.raises(KeyError):
        resolve_placeholders(
            {"findings": [{"vault_id_reference": "@tool:nope"}]}, {"x": "y"}
        )


def test_replay_crew_qa_sequence_and_full_racm():
    run = {
        "planning": {
            "qa": [{"approved": False, "rejection_reason": "r"}, {"approved": True}],
            "racm": {
                "risks": [
                    {
                        "risk_id": "R1",
                        "description": "d",
                        "regulatory_mapping": [],
                        "controls": [
                            {
                                "control_id": "C1",
                                "description": "d",
                                "testing_procedures": {
                                    "test_of_design": [],
                                    "test_of_effectiveness": [],
                                },
                            }
                        ],
                    }
                ]
            },
        }
    }
    attempts: dict[int, int] = {}
    first = ReplayCrew(1, run, attempts).kickoff({"theme": "T"})
    second = ReplayCrew(1, run, attempts).kickoff({"theme": "T"})
    qa = [
        next(t.pydantic for t in r.tasks_output if t.name == "qa_gate_task")
        for r in (first, second)
    ]
    assert [q.approved for q in qa] == [False, True]
    racm = next(
        t.pydantic for t in first.tasks_output if t.name == "racm_drafting_task"
    )
    assert racm.theme == "T" and racm.risks[0].controls[0].control_id == "C1"


# ── QA seeding ───────────────────────────────────────────────────────────────

V = {
    "get_iam_password_policy": "11111111-1111-1111-1111-111111111111",
    "list_iam_users_with_mfa": "22222222-2222-2222-2222-222222222222",
    "list_public_s3_buckets": "33333333-3333-3333-3333-333333333333",
}


def test_good_seed_papers_are_schema_valid():
    WorkingPaperSchema.model_validate(good_papers(V))
    WorkingPaperSchema.model_validate(seed_papers("good-alternative-framing", V))


def test_each_bad_seed_changes_only_its_planted_control():
    reference = {f["control_id"]: f for f in good_papers(V)["findings"]}
    for seed_id, (_, control, _) in SEEDS.items():
        if seed_id.startswith("good"):
            continue
        seeded = {f["control_id"]: f for f in seed_papers(seed_id, V)["findings"]}
        changed = {
            c for c in set(reference) | set(seeded) if reference.get(c) != seeded.get(c)
        }
        assert changed == {control}, seed_id


def test_seed_set_covers_the_required_defects():
    ids = set(SEEDS)
    for required in (
        "bad-effective-without-quote",
        "bad-fabricated-quote",
        "bad-contradicting-conclusions",
    ):
        assert required in ids
    assert sum(1 for s in ids if s.startswith("good")) >= 2


def test_score_qa_fails_closed_on_unparseable_answers():
    decisions = [
        {
            "seed": "bad-x",
            "kind": "bad",
            "defect": "",
            "control": "C-1",
            "approved": None,
            "unparseable": True,
        },
        {
            "seed": "bad-y",
            "kind": "bad",
            "defect": "",
            "control": "C-2",
            "approved": True,
        },
        {
            "seed": "good",
            "kind": "good",
            "defect": "",
            "control": None,
            "approved": True,
        },
    ]
    s = score_qa(decisions)
    assert (s["catch_rate"]["num"], s["catch_rate"]["den"]) == (1, 2)
    assert s["targeted_catch_rate"]["num"] == 0  # no reason named C-1
    assert s["false_rejection_rate"]["num"] == 0
    assert s["unparseable"] == 1


def test_qa_seeding_runs_offline_against_real_evidence(tmp_path):
    seen: dict[str, str] = {}
    evidence_seen: list[str] = []

    def reviewer(seed_id, test_plan, evidence, papers):
        seen[seed_id] = papers
        evidence_seen.append(evidence)
        assert "Control C-PWD" in test_plan
        assert "Vault ID:" in evidence and '"without_mfa": 20' in evidence
        return {"approved": seed_id.startswith("good"), "rejection_reason": None}

    result = run_qa_seeding(reviewer, 1, tmp_path)
    assert set(seen) == set(SEEDS)
    assert result["scores"]["catch_rate"]["rate"] == 1.0
    assert result["scores"]["false_rejection_rate"]["rate"] == 0.0
    # The fabricated quote really is absent from the collected evidence.
    fabricated = json.loads(seen["bad-fabricated-quote"])
    quote = fabricated["findings"][0]["exact_quote_from_evidence"]
    assert quote == '"MinimumPasswordLength": 16'
    assert quote not in evidence_seen[0]
    assert '"MinimumPasswordLength": 14' in evidence_seen[0]


def test_replay_reviewer_requires_a_decision_per_seed():
    with pytest.raises(KeyError):
        replay_reviewer({})("bad-x", "", "", "")

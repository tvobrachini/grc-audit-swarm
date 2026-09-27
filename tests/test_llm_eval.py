"""LLM-layer evaluation harness (evals/): answer key, seeding, CLI safety and
an end-to-end replay run. Offline: no model is called (replay fixtures stand
in for the crews) and AWS is moto."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from evals import run as eval_run  # noqa: E402
from evals.answer_key import (  # noqa: E402
    UNREVIEWED_BANNER,
    AnswerKey,
    load_answer_key,
    select_scenarios,
)
from evals.aws_sim import mfa_less_indexes, simulated_account  # noqa: E402
from evals.pipeline import collect_evidence, score_record  # noqa: E402

from swarm.audit_flow import deficiency_scale_for_scope  # noqa: E402

REPLAY = Path(__file__).resolve().parent.parent / "evals" / "fixtures" / "replay"

_PROVIDER_VARS = (
    "OLLAMA_MODEL",
    "NVIDIA_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "GROQ_API_KEY",
    "QA_LLM_MODEL",
)
_CI_VARS = ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "JENKINS_URL")


@pytest.fixture(scope="module")
def key() -> AnswerKey:
    return load_answer_key()


# ── Answer key ───────────────────────────────────────────────────────────────


def test_answer_key_is_a_draft_pending_owner_review(key):
    assert key.status == "DRAFT"
    assert key.reviewed_by is None
    assert not key.reviewed
    assert key.review_status() == UNREVIEWED_BANNER
    assert "NOT YET REVIEWED BY THE OWNER" in key.review_status()


def test_answer_key_has_10_to_15_scenarios_with_unique_ids(key):
    ids = [s.id for s in key.scenarios]
    assert 10 <= len(ids) <= 15
    assert len(set(ids)) == len(ids)


def test_scenario_scale_matches_the_apps_scope_rule(key):
    for s in key.scenarios:
        assert (
            deficiency_scale_for_scope(s.theme, s.business_context, s.frameworks)
            == s.deficiency_scale
        ), s.id


def test_every_scenario_has_an_in_scope_area(key):
    for s in key.scenarios:
        assert any(e.in_scope for e in s.areas.values()), s.id


def test_answer_key_rejects_a_tested_outcome_for_a_no_tool_area(key):
    data = key.model_dump(mode="json")
    data["scenarios"][0]["areas"]["access_review"]["expected_result"] = "No exception"
    with pytest.raises(ValueError, match="only supported outcome is 'Not tested'"):
        AnswerKey.model_validate(data)


def test_answer_key_rejects_a_classification_off_the_scale(key):
    data = key.model_dump(mode="json")
    s01 = data["scenarios"][0]
    assert s01["deficiency_scale"] == "ICFR deficiency scale"
    s01["areas"]["password_policy"]["deficiency_acceptable"] = ["High"]
    with pytest.raises(ValueError, match="not on the"):
        AnswerKey.model_validate(data)


def test_reviewed_key_reports_the_reviewer(key):
    signed = key.model_copy(
        update={"reviewed_by": "Owner", "reviewed_on": "2026-10-01"}
    )
    assert signed.reviewed
    assert "reviewed by Owner on 2026-10-01" in signed.review_status()


def test_select_scenarios(key):
    assert len(select_scenarios(key, "all")) == len(key.scenarios)
    chosen = select_scenarios(key, "s01,s05-s3-public-policy,s01")
    assert [s.id for s in chosen] == [
        "s01-sox-access-no-password-policy",
        "s05-s3-public-policy",
    ]
    with pytest.raises(KeyError):
        select_scenarios(key, "s1")  # ambiguous prefix: s10..s13
    with pytest.raises(KeyError):
        select_scenarios(key, "nope")


# ── Seeding: the planted evidence agrees with the answer key ────────────────


def test_mfa_less_indexes_are_exact_and_spread():
    assert len(mfa_less_indexes(60, 20)) == 20
    assert len(mfa_less_indexes(60, 40)) == 40
    assert mfa_less_indexes(10, 0) == set()
    assert mfa_less_indexes(3, 5) == {0, 1, 2}


def _tool_json(output: str) -> dict:
    return json.loads(output.split("\nRaw Output: ", 1)[1])


@pytest.mark.parametrize(
    "scenario_id",
    [s.id for s in load_answer_key().scenarios],
)
def test_planted_evidence_supports_the_answer_key(
    scenario_id, key, tmp_path, monkeypatch
):
    """The key's expected outcome for each tool area is what the real tool
    reports for the planted account (so the key is internally consistent)."""
    s = key.scenario(scenario_id)
    monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path / "vault"))
    with simulated_account(s.aws):
        evidence = collect_evidence()
    for area, exp in s.areas.items():
        expected = str(exp.expected_result.value)
        if area == "s3_public_access":
            summary = _tool_json(evidence["list_public_s3_buckets"]["output"])[
                "Summary"
            ]
            public = summary["public"]
            assert (public > 0) == (expected == "Exception"), s.id
            if exp.expected_exceptions is not None:
                assert public == exp.expected_exceptions, s.id
        elif area == "iam_mfa":
            summary = _tool_json(evidence["list_iam_users_with_mfa"]["output"])[
                "Summary"
            ]
            assert (summary["without_mfa"] > 0) == (expected == "Exception"), s.id
            if exp.expected_exceptions is not None:
                assert summary["without_mfa"] == exp.expected_exceptions, s.id
        elif area == "password_policy":
            raw = evidence["get_iam_password_policy"]["output"].split(
                "\nRaw Output: ", 1
            )[1]
            if raw.startswith("Finding:"):
                assert expected == "Exception", s.id
            else:
                length = json.loads(raw)["MinimumPasswordLength"]
                assert (length < 12) == (expected == "Exception"), s.id


def test_simulated_account_never_uses_real_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "callers-own-key-id")
    monkeypatch.setenv("AWS_PROFILE", "production")
    with simulated_account({}):
        # moto's own placeholder (or the harness's), never the caller's key.
        assert os.environ["AWS_ACCESS_KEY_ID"] in {"testing", "FOOBARKEY"}
        assert "AWS_PROFILE" not in os.environ
    assert os.environ["AWS_ACCESS_KEY_ID"] == "callers-own-key-id"
    assert os.environ["AWS_PROFILE"] == "production"


# ── CLI safety: the real-LLM runner refuses without a provider / in CI ──────


def _no_provider(monkeypatch):
    for v in (*_PROVIDER_VARS, *_CI_VARS, "DEMO_MODE"):
        monkeypatch.delenv(v, raising=False)


def test_real_run_refuses_without_a_provider(monkeypatch, tmp_path, capsys):
    _no_provider(monkeypatch)
    code = eval_run.main(
        ["--env-file", str(tmp_path / "missing.env"), "--out", str(tmp_path)]
    )
    assert code == eval_run.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "No LLM provider is configured" in err
    assert "--replay" in err
    assert list(tmp_path.iterdir()) == []  # nothing ran, nothing written


@pytest.mark.parametrize("ci_var", ["CI", "GITHUB_ACTIONS"])
def test_real_run_refuses_in_ci_even_with_a_provider(
    monkeypatch, tmp_path, capsys, ci_var
):
    _no_provider(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setenv(ci_var, "true")
    code = eval_run.main(
        ["--env-file", str(tmp_path / "missing.env"), "--out", str(tmp_path)]
    )
    assert code == eval_run.EXIT_REFUSED
    assert "must never run in CI" in capsys.readouterr().err


def test_real_run_refuses_in_demo_mode(monkeypatch, tmp_path, capsys):
    _no_provider(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("DEMO_MODE", "1")
    code = eval_run.main(
        ["--env-file", str(tmp_path / "missing.env"), "--out", str(tmp_path)]
    )
    assert code == eval_run.EXIT_REFUSED
    assert "DEMO_MODE" in capsys.readouterr().err


def test_real_run_reads_the_provider_from_the_env_file(monkeypatch, tmp_path):
    _no_provider(monkeypatch)
    env_file = tmp_path / "eval.env"
    env_file.write_text("OPENAI_API_KEY=from-env-file\n")
    monkeypatch.setenv("OPENAI_API_KEY", "")  # registered for restore at teardown
    monkeypatch.delenv("OPENAI_API_KEY")
    assert eval_run.check_real_run_allowed(str(env_file)) is None
    assert os.environ["OPENAI_API_KEY"] == "from-env-file"  # pragma: allowlist secret


def test_provider_that_fails_to_initialise_is_refused(monkeypatch, tmp_path):
    _no_provider(monkeypatch)
    from swarm import llm_factory

    def broken(**_):
        raise ImportError("provider package missing")

    monkeypatch.setattr(llm_factory, "get_crew_llm", broken)
    reason = eval_run.check_real_run_allowed(str(tmp_path / "missing.env"))
    assert reason is not None and "provider package missing" in reason


def test_replay_of_unknown_scenario_is_refused(tmp_path, capsys):
    code = eval_run.main(
        ["--replay", str(REPLAY), "--scenarios", "s02", "--out", str(tmp_path)]
    )
    assert code == eval_run.EXIT_REFUSED
    assert "no runs for" in capsys.readouterr().err


def test_runs_must_be_positive(tmp_path, capsys):
    assert (
        eval_run.main(["--runs", "0", "--out", str(tmp_path)]) == eval_run.EXIT_REFUSED
    )


# ── End to end: replay through the real AuditFlow ───────────────────────────


@pytest.fixture(scope="module")
def replay_results(tmp_path_factory):
    out = tmp_path_factory.mktemp("replay")
    code = eval_run.main(["--replay", str(REPLAY), "--out", str(out)])
    assert code == 0
    md = next(out.glob("*.md"))
    data = json.loads(next(out.glob("*.json")).read_text())
    return out, md.read_text(), data


def _metric(data, name):
    m = data["aggregate"]["metrics"][name]
    return m["num"], m["den"]


def test_replay_writes_markdown_and_json_named_by_date_provider_model(replay_results):
    out, md, data = replay_results
    names = sorted(p.name for p in out.iterdir())
    stem = names[0].removesuffix(".json").removesuffix(".md")
    assert stem.endswith("-replay-replay")
    assert {f"{stem}.md", f"{stem}.json", stem} <= set(names)


def test_replay_report_leads_with_the_answer_key_review_status(replay_results):
    _, md, data = replay_results
    lines = [line for line in md.splitlines() if line.strip()]
    assert lines[0].startswith("# LLM-layer evaluation")
    assert "ANSWER KEY NOT YET REVIEWED BY THE OWNER" in lines[1]
    assert "REPLAY MODE" in md
    assert data["answer_key"]["reviewed_by"] is None


def test_replay_scores_every_metric(replay_results):
    _, md, data = replay_results
    assert data["aggregate"]["runs_completed"] == 6
    # s01 run 2: MFA (40 of 60 without) concluded No exception.
    assert _metric(data, "false_pass_rate") == (1, 4)
    # s06 run 2: the ACL that account-level BPA neutralises reported as an exception.
    assert _metric(data, "false_fail_rate") == (1, 2)
    # s01 run 2 access review and s09 run 1 change management concluded without a tool.
    assert _metric(data, "unsupported_conclusion_rate") == (2, 5)
    assert _metric(data, "not_tested_correctness") == (9, 11)
    # s06 run 1: ToE Effective on a single read with no reliance.
    assert _metric(data, "toe_basis_correctness") == (5, 6)
    # s09 run 1: fabricated quote.
    assert _metric(data, "citation_faithfulness") == (7, 8)
    # s09 run 2 has no logging control.
    assert _metric(data, "coverage") == (11, 12)
    # s01 run 2 Material Weakness; s06 run 2 spurious Medium.
    assert _metric(data, "deficiency_agreement") == (2, 4)
    assert _metric(data, "exception_count_accuracy") == (1, 2)
    assert data["aggregate"]["consistency"]["rate"] == pytest.approx(
        (2 / 3 + 0.5 + 0.5) / 3
    )
    qa = data["qa_seeding"]["scores"]
    assert (qa["catch_rate"]["num"], qa["catch_rate"]["den"]) == (9, 10)
    assert (qa["targeted_catch_rate"]["num"], qa["targeted_catch_rate"]["den"]) == (
        8,
        10,
    )
    assert (qa["false_rejection_rate"]["num"], qa["false_rejection_rate"]["den"]) == (
        1,
        2,
    )


def test_replay_lists_each_false_pass_and_unmatched_finding(replay_results):
    _, md, data = replay_results
    fp = data["notable"]["false_passes"]
    assert [(f["scenario"], f["run"], f["control_id"]) for f in fp] == [
        ("s01-sox-access-no-password-policy", 2, "AC-2")
    ]
    unmatched = data["notable"]["unmatched_findings"]
    assert [u["control_id"] for u in unmatched] == ["AC-4"]
    assert "| s01-sox-access-no-password-policy | 2 | AC-2 | iam_mfa |" in md


def test_replay_goes_through_the_real_gates(replay_results):
    _, _, data = replay_results
    s09 = next(s for s in data["scenarios"] if s["id"].startswith("s09"))
    events = [e["event"] for e in s09["runs"][0]["pipeline"]["events"]]
    # The deterministic evidence check rejected the fabricated quote twice;
    # the synthetic reviewer overrode it so Reporting could be measured.
    assert "qa_rejected_after_retry" in events
    assert "qa_overridden" in events
    s06 = next(s for s in data["scenarios"] if s["id"].startswith("s06"))
    assert s06["runs"][1]["qa_in_pipeline"]["2"] == {"attempts": 2, "qa_rejections": 1}


def test_every_number_is_recomputable_from_the_raw_json(replay_results, key):
    """Traceability: rescoring each saved raw run gives the stored scores."""
    out, _, data = replay_results
    for s in data["scenarios"]:
        scenario = key.scenario(s["id"])
        for r in s["runs"]:
            raw = json.loads((out / r["raw_file"]).read_text())
            assert raw["vault"], "raw run must carry its evidence records"
            rescored = json.loads(
                json.dumps(score_record(raw, key, scenario), default=str)
            )
            assert rescored["counts"] == r["counts"]
            assert rescored["finding_scores"] == r["finding_scores"]


def test_raw_run_keeps_the_mapping_step(replay_results):
    out, _, data = replay_results
    raw = json.loads((out / data["scenarios"][0]["runs"][0]["raw_file"]).read_text())
    mapping = raw["scoring"]["mapping"]
    assert {m["control_id"]: m["area"] for m in mapping} == {
        "AC-01": "password_policy",
        "AC-02": "iam_mfa",
        "AC-03": "access_review",
    }
    assert all("scores" in m and "text" in m for m in mapping)

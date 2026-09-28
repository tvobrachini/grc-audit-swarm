"""QA catch rate: seed deliberately bad working papers into the Fieldwork QA
reviewer and measure how many it rejects.

One fixed engagement (RACM of four controls, a moto account with a strong
password policy, 20 of 60 users without MFA, one public bucket) gives real
evidence in a real vault. A correct set of working papers is written for it,
and each *bad* seed changes exactly one thing in it, so a rejection can be
attributed to the planted defect. Two *good* seeds measure false rejections.

The reviewer is the application's ``qa_field_reviewer`` agent with the
``eval_qa_gate_task`` prompt and the QA model from ``get_qa_llm``. It runs
alone: the collected evidence and the working papers are appended to the
task description as text (in the full crew they arrive as task context).
Papers are passed as raw JSON, not through the schema, so defects that the
schema itself would reject (e.g. contradicting conclusions) still reach the
reviewer; the flow's schema and evidence checks are separate safeguards.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable, Optional

import evals  # noqa: F401  (puts src/ on sys.path)
from evals.aws_sim import simulated_account
from evals.pipeline import _env, collect_evidence, expand_racm

from swarm.audit_flow import racm_test_plan

THEME = "IAM and S3 baseline (QA seeding engagement)"

AWS = {
    "password_policy": {
        "MinimumPasswordLength": 14,
        "RequireSymbols": True,
        "RequireNumbers": True,
        "RequireUppercaseCharacters": True,
        "RequireLowercaseCharacters": True,
    },
    "users": {"count": 60, "without_mfa": 20},
    "buckets": [
        {"name": "customer-exports", "policy": "public_read", "policy_is_public": True},
        {"name": "app-logs"},
    ],
}

RACM = {
    "risks": {
        "RISK-01": "Unauthorised access through weak IAM credentials.",
        "RISK-02": "Customer data exposed through public S3 buckets.",
        "RISK-03": "Unauthorised changes to the production environment.",
    },
    "controls": [
        {
            "control_id": "C-PWD",
            "risk_id": "RISK-01",
            "description": "The IAM password policy enforces minimum length 14 and complexity.",
            "tod": "Run get_iam_password_policy and compare it with the standard.",
        },
        {
            "control_id": "C-MFA",
            "risk_id": "RISK-01",
            "description": "Every IAM user has an MFA device.",
            "tod": "Run list_iam_users_with_mfa and inspect MFA per user.",
        },
        {
            "control_id": "C-S3",
            "risk_id": "RISK-02",
            "description": "No S3 bucket is publicly accessible.",
            "tod": "Run list_public_s3_buckets and inspect each bucket's verdict.",
        },
        {
            "control_id": "C-CHG",
            "risk_id": "RISK-03",
            "description": "Production changes are approved by the change advisory board before deployment.",
            "tod": "Inspect a sample of change tickets for CAB approval.",
        },
    ],
}

_POINT_IN_TIME = (
    "Single point-in-time configuration read: design and implementation "
    "evidence only; operating effectiveness over the period was not tested."
)


def good_papers(v: dict[str, str]) -> dict[str, Any]:
    """Correct working papers for the seeding engagement (vault IDs ``v``)."""
    return {
        "theme": THEME,
        "findings": [
            {
                "control_id": "C-PWD",
                "vault_id_reference": v["get_iam_password_policy"],
                "exact_quote_from_evidence": '"MinimumPasswordLength": 14',
                "tod_conclusion": "Effective",
                "toe_conclusion": "Not tested",
                "toe_basis": _POINT_IN_TIME,
                "items_tested": 1,
                "exceptions_noted": 0,
                "result": "No exception",
                "preliminary_deficiency": False,
                "test_conclusion": "The policy requires length 14 and all character classes at the time of the read.",
            },
            {
                "control_id": "C-MFA",
                "vault_id_reference": v["list_iam_users_with_mfa"],
                "exact_quote_from_evidence": '"without_mfa": 20',
                "tod_conclusion": "Ineffective",
                "toe_conclusion": "Not tested",
                "toe_basis": _POINT_IN_TIME,
                "items_tested": 60,
                "exceptions_noted": 20,
                "result": "Exception",
                "preliminary_deficiency": True,
                "test_conclusion": "20 of 60 IAM users have no MFA device; the control is not implemented for them.",
            },
            {
                "control_id": "C-S3",
                "vault_id_reference": v["list_public_s3_buckets"],
                "exact_quote_from_evidence": "public bucket policy",
                "tod_conclusion": "Ineffective",
                "toe_conclusion": "Not tested",
                "toe_basis": _POINT_IN_TIME,
                "items_tested": 2,
                "exceptions_noted": 1,
                "result": "Exception",
                "preliminary_deficiency": True,
                "test_conclusion": "customer-exports is PUBLIC through a public bucket policy; app-logs is not public.",
            },
            {
                "control_id": "C-CHG",
                "vault_id_reference": "",
                "exact_quote_from_evidence": "",
                "tod_conclusion": "Not tested",
                "toe_conclusion": "Not tested",
                "toe_basis": "No evidence tool covers change tickets.",
                "result": "Not tested",
                "preliminary_deficiency": False,
                "test_conclusion": "No evidence tool covers change approvals, so the control was not tested (scope limitation).",
            },
        ],
    }


def _finding(papers: dict[str, Any], control_id: str) -> dict[str, Any]:
    return next(f for f in papers["findings"] if f["control_id"] == control_id)


def _mutate(
    control_id: str, **changes: Any
) -> Callable[[dict[str, Any], dict[str, str]], None]:
    def apply(papers: dict[str, Any], v: dict[str, str]) -> None:
        f = _finding(papers, control_id)
        for k, val in changes.items():
            f[k] = val(v) if callable(val) else val

    return apply


def _drop(control_id: str) -> Callable[[dict[str, Any], dict[str, str]], None]:
    def apply(papers: dict[str, Any], v: dict[str, str]) -> None:
        papers["findings"] = [
            f for f in papers["findings"] if f["control_id"] != control_id
        ]

    return apply


def _append_unknown(papers: dict[str, Any], v: dict[str, str]) -> None:
    extra = copy.deepcopy(_finding(papers, "C-PWD"))
    extra["control_id"] = "C-XYZ"
    extra["test_conclusion"] = "Security awareness training completed by all staff."
    papers["findings"].append(extra)


# id -> (defect description, control the defect is planted on, mutation).
# Good seeds have mutation None.
SEEDS: dict[str, tuple[str, Optional[str], Any]] = {
    "good-reference": ("Correct working papers (reference).", None, None),
    "good-alternative-framing": (
        "Correct papers; MFA gap framed as ToD Effective / ToE Exceptions noted.",
        None,
        _mutate(
            "C-MFA",
            tod_conclusion="Effective",
            toe_conclusion="Exceptions noted",
            toe_basis="All 60 users inspected at the time of the read (full population).",
        ),
    ),
    "bad-effective-without-quote": (
        "ToD/ToE Effective with no quote and no vault ID.",
        "C-PWD",
        _mutate(
            "C-PWD",
            vault_id_reference="",
            exact_quote_from_evidence="",
            toe_conclusion="Effective",
            toe_basis="Relies on change-management ITGCs.",
        ),
    ),
    "bad-fabricated-quote": (
        "Quote not present in the evidence (length 16; evidence says 14).",
        "C-PWD",
        _mutate("C-PWD", exact_quote_from_evidence='"MinimumPasswordLength": 16'),
    ),
    "bad-exception-concluded-effective": (
        "20 users without MFA concluded Effective / No exception using a real but cherry-picked quote.",
        "C-MFA",
        _mutate(
            "C-MFA",
            exact_quote_from_evidence='"MFA_Enabled": "Yes"',
            tod_conclusion="Effective",
            toe_conclusion="Effective",
            toe_basis="Test of one; relies on change-management ITGCs for the period.",
            exceptions_noted=0,
            result="No exception",
            preliminary_deficiency=False,
            test_conclusion="IAM users have MFA devices assigned.",
        ),
    ),
    "bad-contradicting-conclusions": (
        "ToD Ineffective with result No exception.",
        "C-S3",
        _mutate("C-S3", result="No exception", preliminary_deficiency=False),
    ),
    "bad-config-read-as-toe-effective": (
        "Single configuration read concluded ToE Effective with no reliance stated.",
        "C-PWD",
        _mutate(
            "C-PWD",
            toe_conclusion="Effective",
            toe_basis="Policy observed as configured.",
            test_conclusion="The password policy operated effectively throughout the year.",
        ),
    ),
    "bad-untestable-control-tested": (
        "Change management (no evidence tool) concluded Effective citing the password policy.",
        "C-CHG",
        _mutate(
            "C-CHG",
            vault_id_reference=lambda v: v["get_iam_password_policy"],
            exact_quote_from_evidence='"MinimumPasswordLength": 14',
            tod_conclusion="Effective",
            toe_conclusion="Effective",
            toe_basis="Relies on the IAM configuration being enforced.",
            result="No exception",
            test_conclusion="Changes are controlled because the password policy is enforced.",
        ),
    ),
    "bad-severity-in-working-papers": (
        "Severity classification (material weakness) in the working papers.",
        "C-MFA",
        _mutate(
            "C-MFA",
            test_conclusion="20 of 60 users have no MFA device. This is a material weakness in internal control over financial reporting.",
        ),
    ),
    "bad-missing-finding": (
        "No finding for a control in the test plan.",
        "C-CHG",
        _drop("C-CHG"),
    ),
    "bad-public-bucket-quoted-as-private": (
        "Public bucket missed: quotes the private bucket's NOT_PUBLIC verdict and concludes No exception.",
        "C-S3",
        _mutate(
            "C-S3",
            exact_quote_from_evidence='"Verdict": "NOT_PUBLIC"',
            tod_conclusion="Effective",
            exceptions_noted=0,
            result="No exception",
            preliminary_deficiency=False,
            test_conclusion="Buckets are not public.",
        ),
    ),
    "bad-unknown-control-id": (
        "Extra finding for a control ID that is not in the test plan.",
        "C-XYZ",
        _append_unknown,
    ),
}


def seed_papers(seed_id: str, vault_ids: dict[str, str]) -> dict[str, Any]:
    papers = good_papers(vault_ids)
    mutation = SEEDS[seed_id][2]
    if mutation is not None:
        mutation(papers, vault_ids)
    return papers


Reviewer = Callable[[str, str, str, str], dict[str, Any]]
"""(seed_id, test_plan, evidence_text, papers_json) -> {approved, rejection_reason}."""


def replay_reviewer(decisions: dict[str, Any]) -> Reviewer:
    def review(
        seed_id: str, test_plan: str, evidence: str, papers: str
    ) -> dict[str, Any]:
        if seed_id not in decisions:
            raise KeyError(f"replay fixture has no QA decision for seed {seed_id!r}")
        return dict(decisions[seed_id])

    return review


def real_reviewer() -> Reviewer:  # pragma: no cover - needs an LLM provider
    """The Fieldwork QA agent on the configured QA model."""
    from crewai import Agent, Crew, Process, Task

    from swarm.crews.fieldwork_crew import FieldworkCrew
    from swarm.llm_factory import get_qa_llm
    from swarm.schema import QA_PushbackSchema

    configs = FieldworkCrew("eval")

    def review(
        seed_id: str, test_plan: str, evidence: str, papers: str
    ) -> dict[str, Any]:
        task_cfg = configs.tasks_config["eval_qa_gate_task"]
        agent = Agent(
            **configs.agents_config["qa_field_reviewer"],
            llm=get_qa_llm(temperature=0.0),
            max_iter=3,
            verbose=False,
        )
        description = (
            task_cfg["description"].format(test_plan=test_plan)
            + "\n\nCollected evidence (the evidence collector's output, verbatim):\n"
            + evidence
            + "\n\nWorking Papers under review (JSON):\n"
            + papers
        )
        task = Task(
            description=description,
            expected_output=task_cfg["expected_output"],
            agent=agent,
            output_pydantic=QA_PushbackSchema,
            name="eval_qa_gate_task",
        )
        result = Crew(
            agents=[agent], tasks=[task], process=Process.sequential
        ).kickoff()
        qa = result.tasks_output[0].pydantic if result.tasks_output else None
        usage = getattr(result, "token_usage", None)
        return {
            "approved": getattr(qa, "approved", None),
            "rejection_reason": getattr(qa, "rejection_reason", None),
            "unparseable": qa is None,
            "token_usage": usage.model_dump() if usage is not None else None,
        }

    return review


def run_qa_seeding(reviewer: Reviewer, runs: int, out_dir: Path) -> dict[str, Any]:
    """Collect the engagement's evidence once, then review every seed ``runs``
    times. Returns the raw decisions and the scores."""
    racm = expand_racm(RACM, THEME)
    test_plan = racm_test_plan(racm)
    vault_dir = out_dir / "qa-seeding-vault"
    with _env(EVIDENCE_VAULT_PATH=str(vault_dir), VAULT_ENCRYPTION_KEY=None):
        with simulated_account(AWS):
            collected = collect_evidence()
    vault_ids = {name: c["vault_id"] for name, c in collected.items()}
    evidence = "\n\n".join(
        f"Tool: {name}\n{c['output']}" for name, c in collected.items()
    )
    decisions: list[dict[str, Any]] = []
    for run in range(1, runs + 1):
        for seed_id, (description, control, _) in SEEDS.items():
            papers = seed_papers(seed_id, vault_ids)
            try:
                d = reviewer(seed_id, test_plan, evidence, json.dumps(papers, indent=2))
            except KeyError:
                raise
            except Exception as exc:  # recorded; fails closed like the flow
                d = {"approved": None, "error": f"{type(exc).__name__}: {exc}"}
            decisions.append(
                {
                    "seed": seed_id,
                    "run": run,
                    "kind": "good" if seed_id.startswith("good") else "bad",
                    "defect": description,
                    "control": control,
                    **d,
                }
            )
    return {
        "decisions": decisions,
        "scores": score_qa(decisions),
        "test_plan": test_plan,
    }


def score_qa(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Catch rate over bad seeds; false-rejection rate over good seeds.

    A decision counts as a rejection unless ``approved`` is exactly True (an
    unparseable QA answer is a rejection, as in the flow). A *targeted* catch
    is a rejection whose reason names the control the defect was planted on.
    """
    bad = [d for d in decisions if d["kind"] == "bad"]
    good = [d for d in decisions if d["kind"] == "good"]

    def rejected(d: dict[str, Any]) -> bool:
        return d.get("approved") is not True

    def targeted(d: dict[str, Any]) -> bool:
        return (
            rejected(d)
            and bool(d.get("control"))
            and (str(d["control"]) in str(d.get("rejection_reason") or ""))
        )

    def rate(num: int, den: int) -> dict[str, Any]:
        return {"num": num, "den": den, "rate": num / den if den else None}

    per_seed: dict[str, dict[str, Any]] = {}
    for d in decisions:
        s = per_seed.setdefault(
            d["seed"],
            {"kind": d["kind"], "defect": d["defect"], "rejected": 0, "runs": 0},
        )
        s["runs"] += 1
        s["rejected"] += int(rejected(d))
    return {
        "catch_rate": rate(sum(rejected(d) for d in bad), len(bad)),
        "targeted_catch_rate": rate(sum(targeted(d) for d in bad), len(bad)),
        "false_rejection_rate": rate(sum(rejected(d) for d in good), len(good)),
        "unparseable": sum(1 for d in decisions if d.get("unparseable")),
        "per_seed": per_seed,
    }

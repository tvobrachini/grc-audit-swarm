"""Run one scenario through the real AuditFlow and record everything.

The flow is the application's own ``AuditFlow``: state machine, QA retry,
deterministic evidence check, human gates and approval trail all run as in
production. Only two things are substituted:

* the **reviewers**: a synthetic reviewer approves every gate (and, by
  default, overrides a QA rejection so later phases can still be measured;
  the rejection is recorded and reported);
* in **replay** mode, the **crews**: canned outputs from a fixture stand in
  for the model. The Fieldwork stand-in still runs the real evidence tools
  against the simulated account, so vault records and quote checks are real.

AWS is always a fresh moto account seeded from the scenario (see
``evals.aws_sim``); the evidence vault is a directory inside the run's
output folder, unencrypted so the raw evidence can be read back.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator, Optional, Protocol

import evals  # noqa: F401  (puts src/ on sys.path)
from evals import mapping as mapping_mod
from evals import metrics
from evals.answer_key import AnswerKey, Scenario
from evals.aws_sim import simulated_account

from swarm.audit_flow import AuditFlow
from swarm.crews.result_adapter import CrewResultAdapter
from swarm.evidence import EvidenceAssuranceProtocol
from swarm.schema import (
    Control,
    ControlTestStep,
    FinalReportSchema,
    QA_PushbackSchema,
    Risk,
    RiskControlMatrixSchema,
    WorkingPaperSchema,
)

logger = logging.getLogger(__name__)

PREPARER = "eval-harness (preparer)"
REVIEWER = "eval-harness synthetic reviewer (in-charge)"
MANAGER = "eval-harness synthetic reviewer (manager)"
OVERRIDE_REASON = (
    "Evaluation harness: QA rejection accepted by the synthetic reviewer so "
    "the later phases can be measured. The rejection is recorded in the "
    "results; this is not a review decision."
)

QA_TASK = {1: "qa_gate_task", 2: "eval_qa_gate_task", 3: "tone_qa_task"}
ARTIFACT_TASK = {
    1: "racm_drafting_task",
    2: "execution_evaluation_task",
    3: "final_report_assembly_task",
}
EVIDENCE_TOOLS = (
    "get_iam_password_policy",
    "list_iam_users_with_mfa",
    "list_public_s3_buckets",
)
_TOOL_PLACEHOLDER = re.compile(r"^@tool:(\w+)$")


class CrewSource(Protocol):
    """Builds the crew (or stand-in) for a phase."""

    label: str

    def build(self, phase: int, flow: AuditFlow) -> Any: ...


# ── Crew wrappers ────────────────────────────────────────────────────────────


class RecordingCrew:
    """Wraps a crew; records each kickoff's QA verdict and token usage."""

    def __init__(self, crew: Any, phase: int, log: list[dict[str, Any]]):
        self._crew = crew
        self._phase = phase
        self._log = log

    def kickoff(self, inputs: dict[str, Any]) -> Any:
        entry: dict[str, Any] = {"phase": self._phase}
        self._log.append(entry)
        result = self._crew.kickoff(inputs=inputs)
        usage = getattr(result, "token_usage", None)
        if usage is not None:
            dump = getattr(usage, "model_dump", None)
            entry["token_usage"] = dump() if callable(dump) else dict(vars(usage))
        try:
            qa = CrewResultAdapter(result).get(QA_TASK[self._phase]).pydantic
            entry["qa_approved"] = getattr(qa, "approved", None)
            entry["qa_reason"] = getattr(qa, "rejection_reason", None)
        except Exception as exc:  # recorded, the flow handles it fail-closed
            entry["qa_error"] = str(exc)
        return result


class RealCrews:
    """The application's real crews (LLM calls) for every phase."""

    label = "real"

    def __init__(self) -> None:
        self.kickoffs: list[dict[str, Any]] = []

    def build(self, phase: int, flow: AuditFlow) -> Any:
        crew = AuditFlow._build_crew(flow, phase)
        return RecordingCrew(crew, phase, self.kickoffs)


def _step(description: str, expected: str) -> ControlTestStep:
    return ControlTestStep(step_description=description, expected_result=expected)


def expand_racm(spec: dict[str, Any], theme: str) -> RiskControlMatrixSchema:
    """A RACM from a fixture: full schema (``risks``) or the compact form
    ``{"controls": [{control_id, risk_id, description, tod?}], "risks": {id:
    description}}``."""
    if isinstance(spec.get("risks"), list):
        return RiskControlMatrixSchema.model_validate({"theme": theme, **spec})
    risk_text = dict(spec.get("risks") or {})
    grouped: dict[str, list[Control]] = {}
    for c in spec.get("controls") or []:
        tod = c.get("tod") or f"Inspect the evidence for: {c['description']}"
        control = Control.model_validate(
            {
                "control_id": c["control_id"],
                "description": c["description"],
                "testing_procedures": {
                    "test_of_design": [
                        _step(tod, "The control is designed and in place.")
                    ],
                    "test_of_effectiveness": [
                        _step(
                            "Test operation over the period of reliance.",
                            "The control operated throughout the period.",
                        )
                    ],
                },
            }
        )
        grouped.setdefault(c.get("risk_id", "RISK-01"), []).append(control)
    return RiskControlMatrixSchema(
        theme=theme,
        risks=[
            Risk.model_validate(
                {
                    "risk_id": rid,
                    "description": risk_text.get(rid, f"Replay risk {rid}"),
                    "regulatory_mapping": ["(replay fixture)"],
                    "controls": controls,
                }
            )
            for rid, controls in grouped.items()
        ],
    )


def _qa_for_attempt(spec: Any, attempt: int) -> QA_PushbackSchema:
    if isinstance(spec, list):
        spec = spec[min(attempt, len(spec) - 1)] if spec else None
    return QA_PushbackSchema.model_validate(spec or {"approved": True})


def collect_evidence() -> dict[str, dict[str, str]]:
    """Run every evidence tool once (the real CrewAI tools, against whatever
    AWS the tools are wired to); return tool name -> {vault_id, output}."""
    from swarm.tools import aws_tools

    collected: dict[str, dict[str, str]] = {}
    for name in EVIDENCE_TOOLS:
        output = getattr(aws_tools, name).run("")
        first = output.split("\n", 1)[0]
        collected[name] = {
            "vault_id": first.removeprefix("Vault ID: ").strip(),
            "output": output,
        }
    return collected


def run_evidence_tools() -> dict[str, str]:
    """Run every evidence tool once; return tool name -> vault ID."""
    return {name: c["vault_id"] for name, c in collect_evidence().items()}


def resolve_placeholders(
    papers: dict[str, Any], vault_ids: dict[str, str]
) -> dict[str, Any]:
    """Replace ``"@tool:<name>"`` vault references with this run's vault IDs."""
    out = json.loads(json.dumps(papers))
    for f in out.get("findings") or []:
        ref = str(f.get("vault_id_reference") or "")
        m = _TOOL_PLACEHOLDER.match(ref)
        if m:
            if m.group(1) not in vault_ids:
                raise KeyError(f"unknown evidence tool placeholder {ref!r}")
            f["vault_id_reference"] = vault_ids[m.group(1)]
    return out


class ReplayCrew:
    """A fixed-output stand-in for one phase crew (same contract as DemoCrew)."""

    def __init__(self, phase: int, run: dict[str, Any], attempts: dict[int, int]):
        self.phase = phase
        self._run = run
        self._attempts = attempts

    def _artifact(self, inputs: dict[str, Any]) -> Any:
        theme = str(inputs.get("theme", ""))
        if self.phase == 1:
            return expand_racm(self._run["planning"]["racm"], theme)
        if self.phase == 2:
            vault_ids = run_evidence_tools()
            papers = resolve_placeholders(
                self._run["fieldwork"]["working_papers"], vault_ids
            )
            papers.setdefault("theme", theme)
            return WorkingPaperSchema.model_validate(papers)
        spec = self._run.get("reporting") or {}
        return FinalReportSchema.model_validate(
            {
                "executive_summary": "[EVAL REPLAY] Canned report for harness tests.",
                "detailed_report": "[EVAL REPLAY] Canned report for harness tests.",
                "compliance_tone_approved": True,
                "deficiency_evaluations": spec.get("deficiency_evaluations") or [],
                "deficiency_scale": spec.get("deficiency_scale"),
            }
        )

    def kickoff(self, inputs: dict[str, Any]) -> Any:
        attempt = self._attempts.get(self.phase, 0)
        self._attempts[self.phase] = attempt + 1
        key = {1: "planning", 2: "fieldwork", 3: "reporting"}[self.phase]
        qa = _qa_for_attempt((self._run.get(key) or {}).get("qa"), attempt)
        artifact = self._artifact(inputs)
        return SimpleNamespace(
            tasks_output=[
                SimpleNamespace(name=ARTIFACT_TASK[self.phase], pydantic=artifact),
                SimpleNamespace(name=QA_TASK[self.phase], pydantic=qa),
            ],
            token_usage=None,
        )


class ReplayCrews:
    """Crews replaced by one run of a replay fixture."""

    label = "replay"

    def __init__(self, run: dict[str, Any]) -> None:
        self._run = run
        self._attempts: dict[int, int] = {}
        self.kickoffs: list[dict[str, Any]] = []

    def build(self, phase: int, flow: AuditFlow) -> Any:
        return RecordingCrew(
            ReplayCrew(phase, self._run, self._attempts), phase, self.kickoffs
        )


class EvalAuditFlow(AuditFlow):
    """AuditFlow whose crews come from a CrewSource."""

    def __init__(self, crews: CrewSource) -> None:
        super().__init__()
        self._crews = crews

    def _build_crew(self, phase: int, event_callback: Any = None) -> Any:
        return self._crews.build(phase, self)


# ── Environment ──────────────────────────────────────────────────────────────


@contextlib.contextmanager
def _env(**values: Optional[str]) -> Iterator[None]:
    saved = {k: os.environ.get(k) for k in values}
    try:
        for k, v in values.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def read_vault(vault_dir: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    if not vault_dir.is_dir():
        return records
    for path in sorted(vault_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        records[data["vault_id"]] = {
            "source": data.get("mcp_source"),
            "timestamp": data.get("timestamp"),
            "sha256": data.get("sha256"),
            "payload": data.get("raw_payload"),
        }
    return records


def _dump(model: Any) -> Optional[dict[str, Any]]:
    return None if model is None else model.model_dump(mode="json")


def _drive(flow: AuditFlow, on_qa_reject: str) -> dict[str, Any]:
    """Phase 1 -> Gate 1 -> Phase 2 -> Gate 2 -> Phase 3 -> Gate 3."""
    events: list[dict[str, Any]] = []
    flow.record_preparer(PREPARER)
    flow.begin_phase_1()
    steps: list[tuple[int, Callable[[], None], Callable[[str], None], str]] = [
        (1, flow.generate_planning, flow.begin_phase_2, REVIEWER),
        (2, flow.generate_fieldwork, flow.begin_phase_3, REVIEWER),
        (3, flow.generate_reporting, flow.finalize_audit, MANAGER),
    ]
    stopped_at: Optional[str] = None
    for phase, generate, approve, approver in steps:
        generate()
        status = flow.state.status
        if status == f"QA_REJECTED_PHASE_{phase}":
            events.append(
                {
                    "phase": phase,
                    "event": "qa_rejected_after_retry",
                    "reason": flow.state.qa_rejection_reason,
                }
            )
            if on_qa_reject != "override":
                stopped_at = status
                break
            try:
                flow.override_qa_rejection(phase, REVIEWER, OVERRIDE_REASON)
            except Exception as exc:
                events.append(
                    {"phase": phase, "event": "override_failed", "error": str(exc)}
                )
                stopped_at = status
                break
            events.append({"phase": phase, "event": "qa_overridden"})
        elif status != f"WAITING_HUMAN_GATE_{phase}":
            events.append(
                {
                    "phase": phase,
                    "event": "error",
                    "reason": flow.state.qa_rejection_reason,
                }
            )
            stopped_at = status
            break
        try:
            approve(approver)
        except Exception as exc:
            events.append({"phase": phase, "event": "gate_refused", "error": str(exc)})
            stopped_at = f"WAITING_HUMAN_GATE_{phase}"
            break
        events.append({"phase": phase, "event": "gate_approved", "by": approver})
    return {
        "final_status": flow.state.status,
        "completed": flow.state.status == "COMPLETED",
        "stopped_at": stopped_at,
        "events": events,
    }


def run_scenario(
    scenario: Scenario,
    run_index: int,
    crews: CrewSource,
    run_dir: Path,
    *,
    on_qa_reject: str = "override",
) -> dict[str, Any]:
    """Run the pipeline once for ``scenario``; return the raw run record."""
    run_dir.mkdir(parents=True, exist_ok=True)
    vault_dir = run_dir / "vault"
    record: dict[str, Any] = {
        "scenario": scenario.id,
        "run": run_index,
        "mode": crews.label,
    }
    with (
        _env(
            EVIDENCE_VAULT_PATH=str(vault_dir),
            VAULT_ENCRYPTION_KEY=None,
            DEMO_MODE="0",
        ),
        simulated_account(scenario.aws),
    ):
        flow = EvalAuditFlow(crews)
        flow.state.theme = scenario.theme
        flow.state.business_context = scenario.business_context
        flow.state.frameworks = list(scenario.frameworks)
        try:
            record["pipeline"] = _drive(flow, on_qa_reject)
        except Exception as exc:  # recorded; the run is scored as far as it got
            logger.exception("Pipeline failed for %s run %d", scenario.id, run_index)
            record["pipeline"] = {
                "final_status": flow.state.status,
                "completed": False,
                "stopped_at": flow.state.status,
                "events": [],
                "harness_error": f"{type(exc).__name__}: {exc}",
            }
        state = flow.state
        record["racm"] = _dump(state.racm_plan)
        record["working_papers"] = _dump(state.working_papers)
        record["final_report"] = _dump(state.final_report)
        record["approval_trail"] = list(state.approval_trail)
        record["kickoffs"] = list(getattr(crews, "kickoffs", []))
        record["citations"] = [
            citation(f) for f in (record["working_papers"] or {}).get("findings", [])
        ]
    record["vault"] = read_vault(vault_dir)
    for c in record["citations"]:
        c["source"] = (record["vault"].get(c["vault_id"]) or {}).get("source")
    return record


def citation(finding: dict[str, Any]) -> dict[str, Any]:
    """Check a finding's quote against the vault (call inside the run env)."""
    quote = str(finding.get("exact_quote_from_evidence") or "")
    vault_id = str(finding.get("vault_id_reference") or "")
    present = bool(quote.strip())
    return {
        "control_id": finding.get("control_id"),
        "vault_id": vault_id,
        "quote_present": present,
        "verified": bool(present)
        and EvidenceAssuranceProtocol.verify_exact_quote(vault_id, quote),
    }


def score_record(
    record: dict[str, Any], key: AnswerKey, scenario: Scenario
) -> dict[str, Any]:
    """Map and score a run record (pure: uses only the record's contents)."""
    findings = (record.get("working_papers") or {}).get("findings") or []
    controls = mapping_mod.racm_controls(record.get("racm"))
    vault = record.get("vault") or {}
    by_id = {c["control_id"]: c for c in record.get("citations") or []}
    mappings, scores = [], []
    for f in findings:
        m = mapping_mod.map_finding(f, controls, vault, key, scenario)
        mappings.append(m)
        cit = by_id.get(
            f.get("control_id"), {"quote_present": False, "verified": False}
        )
        scores.append(metrics.score_finding(f, m, cit, key, scenario))
    deficiencies = metrics.score_deficiencies(
        record.get("final_report"), scores, key, scenario
    )
    counts = metrics.run_counts(scores, deficiencies, key, scenario)
    usage = _sum_usage(record.get("kickoffs") or [])
    return {
        "mapping": mappings,
        "finding_scores": scores,
        "deficiencies": deficiencies,
        "counts": dict(counts),
        "metrics": metrics.rates(counts),
        "area_outcomes": {a: metrics.area_outcome(scores, a) for a in scenario.areas},
        "qa_in_pipeline": _qa_in_pipeline(record.get("kickoffs") or []),
        "token_usage": usage,
    }


def _sum_usage(kickoffs: list[dict[str, Any]]) -> Optional[dict[str, int]]:
    total: dict[str, int] = {}
    for k in kickoffs:
        for name, value in (k.get("token_usage") or {}).items():
            if isinstance(value, int):
                total[name] = total.get(name, 0) + value
    return total or None


def _qa_in_pipeline(kickoffs: list[dict[str, Any]]) -> dict[str, Any]:
    """Per phase: crew attempts and how many the QA agent rejected."""
    out: dict[str, Any] = {}
    for k in kickoffs:
        p = out.setdefault(str(k["phase"]), {"attempts": 0, "qa_rejections": 0})
        p["attempts"] += 1
        p["qa_rejections"] += int(k.get("qa_approved") is not True)
    return out

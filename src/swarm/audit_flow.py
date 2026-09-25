import logging
from datetime import datetime, UTC
from typing import Any, Callable, Optional

from swarm.state.schema import AuditState  # noqa: F401 (re-exported for backwards compat)
from swarm.state.machine import (
    AuditStatus,
    AuditStateMachine,
    InvalidTransitionError,
)
from swarm.crews.planning_crew import PlanningCrew
from swarm.crews.fieldwork_crew import FieldworkCrew
from swarm.crews.reporting_crew import ReportingCrew
from swarm.crews.result_adapter import CrewResultAdapter

logger = logging.getLogger(__name__)

__all__ = [
    "AuditFlow",
    "AuditState",
    "InvalidTransitionError",
    "PhaseArtifactMissingError",
    "QA_UNPARSEABLE_REASON",
]

# One automatic QA-driven retry per phase run (i.e. two crew attempts in total).
_MAX_QA_ATTEMPTS = 2

QA_UNPARSEABLE_REASON = (
    "QA output could not be parsed into the QA schema — treated as a rejection "
    "(QA gates fail closed)."
)
_QA_NO_REASON = "QA rejected the artifact without giving a reason."

_PHASE_LABELS = {1: "Planning", 2: "Fieldwork", 3: "Reporting"}
_GATE_LABELS = {
    1: "Gate 1 (Planning)",
    2: "Gate 2 (Fieldwork)",
    3: "Gate 3 (Reporting)",
}
_ARTIFACT_FIELDS = {1: "racm_plan", 2: "working_papers", 3: "final_report"}

# Per phase: the crew input that carries QA feedback into the drafting prompt,
# and the text used to fill it (auto-retry and human-initiated retry alike).
_QA_FEEDBACK = {
    1: (
        "qa_feedback",
        " IMPORTANT: A previous draft was rejected for the following "
        "reason — fix all issues before re-drafting: {reason}",
    ),
    2: (
        "qa_feedback",
        " IMPORTANT: A previous evaluation was rejected — fix all "
        "severity and evidence issues: {reason}",
    ),
    3: (
        "tone_qa_feedback",
        " IMPORTANT: A previous draft was rejected for tone — fix all issues: {reason}",
    ),
}


class PhaseArtifactMissingError(RuntimeError):
    """Raised when an action needs a phase artifact that does not exist."""


def _qa_rejection(qa_output: Any) -> Optional[str]:
    """Return None if QA approved, else the rejection reason.

    Fails closed: a missing/unparseable QA result (CrewAI sets ``.pydantic`` to
    None when the LLM output can't be parsed) is a rejection, never a pass.
    """
    if qa_output is None:
        return QA_UNPARSEABLE_REASON
    approved = getattr(qa_output, "approved", None)
    if not isinstance(approved, bool):
        return QA_UNPARSEABLE_REASON
    if approved:
        return None
    reason = getattr(qa_output, "rejection_reason", None)
    if isinstance(reason, str) and reason.strip():
        return reason
    return _QA_NO_REASON


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _require_text(value: str, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} is required")
    return value.strip()


def racm_summary(racm: Any) -> str:
    """One line per risk and control — IDs, descriptions and mappings only."""
    if racm is None:
        return "(no RACM available)"
    lines: list[str] = []
    for risk in racm.risks:
        mapping = ", ".join(risk.regulatory_mapping)
        lines.append(f"{risk.risk_id}: {risk.description} [{mapping}]")
        for control in risk.controls:
            lines.append(f"  - {control.control_id}: {control.description}")
    return "\n".join(lines) or "(RACM contains no risks)"


def findings_index(papers: Any) -> str:
    """``control_id | severity | vault_id`` per finding, for OSCAL mapping."""
    if papers is None or not papers.findings:
        return "(no findings)"
    return "\n".join(
        f"{f.control_id} | {f.severity} | {f.vault_id_reference}"
        for f in papers.findings
    )


class AuditFlow:
    """
    Orchestrates the three-phase GRC audit: Planning → Fieldwork → Reporting.
    Each phase is gated by a human approval step enforced by AuditStateMachine.

    Every status change goes through ``self.machine``; ``state.status`` is only
    ever written by ``_commit_status`` as a mirror of the machine.
    """

    def __init__(self, initial_status: str = "WAITING_FOR_SCOPE") -> None:
        self.state = AuditState()
        self.machine = AuditStateMachine(AuditStatus(initial_status))
        self.state.status = self.machine.status.value
        self._skill_context: list[Any] = []

    def _commit_status(self) -> None:
        self.state.status = self.machine.status.value

    # ── Skill context ────────────────────────────────────────────────────────

    def _detect_skills(self) -> None:
        from swarm.skill_loader import detect_skills_from_scope

        scope = f"{self.state.theme} {self.state.business_context}"
        self._skill_context = detect_skills_from_scope(scope)
        # Persist the ids so a reloaded flow runs later phases with the same
        # domain skills (the skill dicts themselves are not serialised).
        self.state.active_skill_ids = [
            s["id"] for s in self._skill_context if isinstance(s.get("id"), str)
        ]

    def restore_skill_context(self) -> None:
        """Rebuild the in-memory skill context from persisted state.

        Uses ``state.active_skill_ids`` when present; snapshots from before the
        ids were persisted fall back to deterministic re-detection from the
        persisted theme and business context.
        """
        from swarm.skill_loader import get_skill_by_id

        resolved: list[Any] = []
        for skill_id in self.state.active_skill_ids:
            skill = get_skill_by_id(skill_id)
            if skill is None:
                logger.warning(
                    "Persisted skill %r no longer exists — skipped", skill_id
                )
                continue
            resolved.append(skill)
        if resolved:
            self._skill_context = resolved
            return
        if self.state.theme or self.state.business_context:
            self._detect_skills()

    def _ensure_skill_context(self) -> None:
        if not self._skill_context:
            self.restore_skill_context()

    # ── Gate helpers (call synchronously before the phase thread) ────────────

    def _stamp_trail(self, gate: str, human_id: str, action: str, **extra: str) -> None:
        entry = {
            "gate": gate,
            "human": human_id,
            "timestamp": _now(),
            "action": action,
        }
        entry.update(extra)
        self.state.approval_trail.append(entry)

    def _approve_gate(self, gate: int, human_id: str) -> None:
        human_id = _require_text(human_id, "human_id")
        transition = {
            1: self.machine.approve_gate_1,
            2: self.machine.approve_gate_2,
            3: self.machine.approve_gate_3,
        }[gate]
        transition()  # raises InvalidTransitionError when not at this gate
        self._commit_status()
        self._stamp_trail(_GATE_LABELS[gate], human_id, "gate_approval")

    def begin_phase_1(self) -> None:
        """Transition to RUNNING_PHASE_1 — call before spawning the phase 1 thread."""
        self.machine.start_phase_1()
        self._commit_status()

    def begin_phase_2(self, human_id: str) -> None:
        """Gate 1 approval: transition to RUNNING_PHASE_2, then stamp the trail.

        Raises:
            InvalidTransitionError: if the flow is not at WAITING_HUMAN_GATE_1
                (nothing is transitioned or stamped).
            ValueError: if ``human_id`` is blank.
        """
        self._approve_gate(1, human_id)

    def begin_phase_3(self, human_id: str) -> None:
        """Gate 2 approval: transition to RUNNING_PHASE_3, then stamp the trail.

        Raises:
            InvalidTransitionError: if the flow is not at WAITING_HUMAN_GATE_2.
            ValueError: if ``human_id`` is blank.
        """
        self._approve_gate(2, human_id)

    def finalize_audit(self, human_id: str) -> None:
        """Gate 3 approval: mark the audit COMPLETED, then stamp the trail.

        Raises:
            InvalidTransitionError: if the flow is not at WAITING_HUMAN_GATE_3.
            ValueError: if ``human_id`` is blank.
        """
        self._approve_gate(3, human_id)

    def retry_phase(self, phase: int, human_id: str) -> None:
        """Re-open a QA-rejected or errored phase: → RUNNING_PHASE_n.

        The caller then runs the matching ``generate_*`` method. The retry is
        recorded in the approval trail.

        Raises:
            InvalidTransitionError: unless the flow is in QA_REJECTED_PHASE_n
                or ERROR_PHASE_n.
            ValueError: if ``human_id`` is blank or ``phase`` is not 1-3.
        """
        if phase not in _PHASE_LABELS:
            raise ValueError("phase must be 1, 2, or 3")
        human_id = _require_text(human_id, "human_id")
        previous = self.machine.status.value
        self.machine.retry_phase(phase)
        self._commit_status()
        self._stamp_trail(
            f"Retry ({_PHASE_LABELS[phase]})",
            human_id,
            "retry",
            previous_status=previous,
            previous_reason=self.state.qa_rejection_reason or "",
        )

    def override_qa_rejection(self, phase: int, human_id: str, reason: str) -> None:
        """Supervisor override: accept a QA-rejected artifact as-is.

        Moves QA_REJECTED_PHASE_n → WAITING_HUMAN_GATE_n, so the normal human
        gate approval still follows. The override, the approver and the
        justification are recorded in the approval trail together with the QA
        rejection being overridden.

        Raises:
            InvalidTransitionError: unless the flow is in QA_REJECTED_PHASE_n.
            PhaseArtifactMissingError: if the phase produced no artifact to accept.
            ValueError: if ``human_id``/``reason`` are blank or ``phase`` invalid.
        """
        if phase not in _PHASE_LABELS:
            raise ValueError("phase must be 1, 2, or 3")
        human_id = _require_text(human_id, "human_id")
        reason = _require_text(reason, "reason")
        target = AuditStatus(f"WAITING_HUMAN_GATE_{phase}")
        if not self.machine.can(target, AuditStatus(f"QA_REJECTED_PHASE_{phase}")):
            raise InvalidTransitionError(self.machine.status, target)
        if getattr(self.state, _ARTIFACT_FIELDS[phase]) is None:
            raise PhaseArtifactMissingError(
                f"Phase {phase} has no {_ARTIFACT_FIELDS[phase]} to accept — "
                "retry the phase instead."
            )
        overridden = self.state.qa_rejection_reason or ""
        self.machine.override_qa(phase)
        self._commit_status()
        self._stamp_trail(
            f"QA Override ({_PHASE_LABELS[phase]})",
            human_id,
            "qa_override",
            reason=reason,
            qa_rejection_reason=overridden,
        )
        self.state.qa_rejection_reason = None
        self.state.current_human_dossier = (
            f"{_PHASE_LABELS[phase]} QA rejection overridden by {human_id}: {reason}. "
            "Review the artifact before approving the gate."
        )

    # ── Shared phase runner ──────────────────────────────────────────────────

    def _retry_feedback(self, phase: int) -> Optional[str]:
        """QA rejection reason to carry into a human-initiated retry, if any.

        Returns the reason only when the most recent trail entry is a retry of
        *this* phase out of QA_REJECTED_PHASE_n (a retry after a crew error
        carries an error message, not QA feedback). The trail is persisted, so
        this also survives an API restart between the retry and the run.
        """
        if not self.state.approval_trail:
            return None
        last = self.state.approval_trail[-1]
        if (
            last.get("action") != "retry"
            or last.get("gate") != f"Retry ({_PHASE_LABELS[phase]})"
            or last.get("previous_status") != f"QA_REJECTED_PHASE_{phase}"
        ):
            return None
        reason = (last.get("previous_reason") or "").strip()
        return reason or None

    def _seed_feedback(self, phase: int, inputs: dict[str, Any]) -> None:
        reason = self._retry_feedback(phase)
        if reason:
            key, template = _QA_FEEDBACK[phase]
            inputs[key] = template.format(reason=reason)

    def _fail_phase(self, phase: int, reason: str) -> None:
        self.machine.error_phase(phase)
        self._commit_status()
        self.state.qa_rejection_reason = reason

    def _run_crew_with_qa(
        self,
        phase: int,
        build_crew: Callable[[], Any],
        inputs: dict[str, Any],
        *,
        qa_task: str,
        artifact_task: str,
    ) -> bool:
        """Run a phase crew with one automatic QA-driven retry.

        On success stores the artifact in state and returns True, leaving the
        machine in RUNNING_PHASE_n for the caller to complete. On failure it
        has already transitioned to QA_REJECTED_PHASE_n / ERROR_PHASE_n and
        returns False.
        """
        label = _PHASE_LABELS[phase]
        field = _ARTIFACT_FIELDS[phase]
        feedback_key, feedback_template = _QA_FEEDBACK[phase]
        rejection: Optional[str] = None
        artifact: Any = None

        for attempt in range(1, _MAX_QA_ATTEMPTS + 1):
            run = "crew" if attempt == 1 else "crew retry"
            try:
                result = build_crew().kickoff(inputs=inputs)
                adapter = CrewResultAdapter(result)
                qa_output = adapter.get(qa_task).pydantic
                artifact = adapter.get(artifact_task).pydantic
            except Exception as exc:
                logger.exception("%s %s failed", label, run)
                self._fail_phase(phase, f"{label} {run} error: {exc}")
                return False

            rejection = _qa_rejection(qa_output)
            if rejection is None:
                break
            if attempt < _MAX_QA_ATTEMPTS:
                logger.warning(
                    "%s QA rejected (attempt %d) — auto-retrying with feedback: %s",
                    label,
                    attempt,
                    rejection,
                )
                inputs[feedback_key] = feedback_template.format(reason=rejection)
        else:
            logger.error(
                "%s QA rejected again after auto-retry — no further retries; "
                "phase %d halted in QA_REJECTED_PHASE_%d: %s",
                label,
                phase,
                phase,
                rejection,
            )
            # Keep the rejected draft so a supervisor can review / override it.
            # Replace (or clear) any older draft so an override can never
            # accept an artifact from a previous run of this phase.
            try:
                setattr(self.state, field, artifact)
            except Exception:
                logger.warning("Rejected %s draft failed validation", field)
                setattr(self.state, field, None)
            self.machine.reject_phase(phase)
            self._commit_status()
            self.state.qa_rejection_reason = rejection
            return False

        if artifact is None:
            self._fail_phase(
                phase,
                f"{label} crew produced no {field}: the output could not be "
                "parsed into the required schema.",
            )
            return False
        try:
            setattr(self.state, field, artifact)
        except Exception as exc:
            logger.exception("%s artifact failed validation", label)
            self._fail_phase(phase, f"{label} crew produced an invalid {field}: {exc}")
            return False
        return True

    # ── Crew inputs ──────────────────────────────────────────────────────────
    # Every ``{placeholder}`` in the crews' YAML task/agent configs must be a
    # key here (CrewAI raises on a missing one; tests/test_prompt_inputs.py
    # checks all three crews).

    def _scope_string(self) -> str:
        return (
            f"Theme: {self.state.theme}. "
            f"Business context: {self.state.business_context}. "
            f"Frameworks: {', '.join(self.state.frameworks) or 'n/a'}."
        )

    def _planning_inputs(self) -> dict[str, Any]:
        return {
            "theme": self.state.theme,
            "business_context": self.state.business_context,
            "frameworks": ", ".join(self.state.frameworks),
            "qa_feedback": "",
        }

    def _fieldwork_inputs(self) -> dict[str, Any]:
        racm = self.state.racm_plan
        return {
            # Fieldwork needs the full test procedures, but not null fields.
            "racm_string": racm.model_dump_json(exclude_none=True) if racm else "",
            "qa_feedback": "",
        }

    def _reporting_inputs(self) -> dict[str, Any]:
        papers = self.state.working_papers
        return {
            "theme": self.state.theme,
            "scope_string": self._scope_string(),
            # Compact RACM (risks → controls, no test steps): the writer needs
            # the control universe, not the procedures, and the full RACM plus
            # the working papers would overflow low-TPM providers.
            "racm_summary": racm_summary(self.state.racm_plan),
            "working_papers_string": (
                papers.model_dump_json(exclude_none=True) if papers else ""
            ),
            # Small control_id → vault_id index for the OSCAL mapper, which
            # otherwise only sees the narrative draft via task context.
            "findings_index": findings_index(papers),
            "tone_qa_feedback": "",
        }

    # ── Phase runners ────────────────────────────────────────────────────────

    def generate_planning(self, event_callback=None):
        """Phase 1 — Run the Planning Crew to produce a RACM."""
        # Handle a direct call where begin_phase_1 wasn't called first
        if self.machine.status == AuditStatus.WAITING_FOR_SCOPE:
            self.machine.start_phase_1()
            self._commit_status()
        if self.machine.status != AuditStatus.RUNNING_PHASE_1:
            raise RuntimeError(
                f"Cannot start Planning (status={self.machine.status.value})"
            )

        self._detect_skills()
        logger.info("Starting Planning Phase...")
        self.state.qa_rejection_reason = None

        inputs = self._planning_inputs()
        # A human retry of a QA-rejected run starts with that rejection as
        # feedback, so the crew does not repeat the same mistake.
        self._seed_feedback(1, inputs)

        ok = self._run_crew_with_qa(
            1,
            lambda: PlanningCrew(
                event_callback=event_callback, skill_context=self._skill_context
            ).crew(),
            inputs,
            qa_task="qa_gate_task",
            artifact_task="racm_drafting_task",
        )
        if not ok:
            return

        self.machine.complete_phase_1()
        self._commit_status()
        self.state.current_human_dossier = (
            "Planning QA Loop complete. No major structural flaws found. "
            "Please review the RACM below for final Standard 12.3 (formerly IIA 2340) approval."
        )

    def generate_fieldwork(self, event_callback=None):
        """Phase 2 — Run the Fieldwork Crew to produce Working Papers.

        Requires begin_phase_2(human_id) (or retry_phase(2, …)) to have already
        transitioned the machine to RUNNING_PHASE_2 — this method does not
        auto-approve the gate itself.
        """
        if self.machine.status != AuditStatus.RUNNING_PHASE_2:
            raise RuntimeError(
                f"Cannot start Fieldwork: Gate 1 not approved "
                f"(status={self.machine.status.value})"
            )

        self._ensure_skill_context()
        logger.info("Starting Fieldwork Execution Phase...")
        self.state.qa_rejection_reason = None

        if self.state.racm_plan is None:
            self._fail_phase(2, "Cannot run Fieldwork: no Phase 1 RACM in state.")
            return
        inputs = self._fieldwork_inputs()
        # A human retry of a QA-rejected run starts with that rejection as
        # feedback, so the crew does not repeat the same mistake.
        self._seed_feedback(2, inputs)

        ok = self._run_crew_with_qa(
            2,
            lambda: FieldworkCrew(
                event_callback=event_callback, skill_context=self._skill_context
            ).crew(),
            inputs,
            qa_task="eval_qa_gate_task",
            artifact_task="execution_evaluation_task",
        )
        if not ok:
            return

        self.machine.complete_phase_2()
        self._commit_status()
        self.state.current_human_dossier = (
            "Execution Fieldwork complete with substantive evidence evaluated. "
            "Please review Findings for final Standard 12.3 (formerly IIA 2340) approval."
        )

    def generate_reporting(self, event_callback=None):
        """Phase 3 — Run the Reporting Crew to produce the Final Report.

        Requires begin_phase_3(human_id) (or retry_phase(3, …)) to have already
        transitioned the machine to RUNNING_PHASE_3 — this method does not
        auto-approve the gate itself. On success the machine moves to
        WAITING_HUMAN_GATE_3; call finalize_audit(human_id) to complete the
        audit (Gate 3).
        """
        if self.machine.status != AuditStatus.RUNNING_PHASE_3:
            raise RuntimeError(
                f"Cannot start Reporting: Gate 2 not approved "
                f"(status={self.machine.status.value})"
            )

        self._ensure_skill_context()
        logger.info("Starting Reporting Phase...")
        self.state.qa_rejection_reason = None

        if self.state.working_papers is None:
            self._fail_phase(
                3, "Cannot run Reporting: no Phase 2 working papers in state."
            )
            return
        inputs = self._reporting_inputs()
        # A human retry of a QA-rejected run starts with that rejection as
        # feedback, so the crew does not repeat the same mistake.
        self._seed_feedback(3, inputs)

        ok = self._run_crew_with_qa(
            3,
            lambda: ReportingCrew(
                event_callback=event_callback, skill_context=self._skill_context
            ).crew(),
            inputs,
            qa_task="tone_qa_task",
            artifact_task="final_report_assembly_task",
        )
        if not ok:
            return

        self.machine.complete_phase_3()
        self._commit_status()

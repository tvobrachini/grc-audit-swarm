import logging
from datetime import datetime, UTC
from typing import Any

from swarm.state.schema import AuditState  # noqa: F401 (re-exported for backwards compat)
from swarm.state.machine import AuditStatus, AuditStateMachine
from swarm.crews.planning_crew import PlanningCrew
from swarm.crews.fieldwork_crew import FieldworkCrew
from swarm.crews.reporting_crew import ReportingCrew
from swarm.crews.result_adapter import CrewResultAdapter

logger = logging.getLogger(__name__)


class AuditFlow:
    """
    Orchestrates the three-phase GRC audit: Planning → Fieldwork → Reporting.
    Each phase is gated by a human approval step enforced by AuditStateMachine.
    """

    def __init__(self, initial_status: str = "WAITING_FOR_SCOPE") -> None:
        self.state = AuditState()
        self.machine = AuditStateMachine(AuditStatus(initial_status))
        self.state.status = self.machine.status.value
        self._skill_context: list[Any] = []

    def _commit_status(self) -> None:
        self.state.status = self.machine.status.value

    def _detect_skills(self) -> None:
        from swarm.skill_loader import detect_skills_from_scope

        scope = f"{self.state.theme} {self.state.business_context}"
        self._skill_context = detect_skills_from_scope(scope)

    # ── Gate helpers (call synchronously before the phase thread) ────────────

    def begin_phase_1(self) -> None:
        """Transition to RUNNING_PHASE_1 — call before spawning the phase 1 thread."""
        self.machine.start_phase_1()
        self._commit_status()

    def begin_phase_2(self, human_id: str) -> None:
        """IIA 2340 Gate 1 approval: stamp trail and transition to RUNNING_PHASE_2."""
        self.state.approval_trail.append(
            {
                "gate": "Gate 1 (Planning)",
                "human": human_id,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        if self.machine.status == AuditStatus.WAITING_HUMAN_GATE_1:
            self.machine.approve_gate_1()
            self._commit_status()

    def begin_phase_3(self, human_id: str) -> None:
        """IIA 2340 Gate 2 approval: stamp trail and transition to RUNNING_PHASE_3."""
        self.state.approval_trail.append(
            {
                "gate": "Gate 2 (Fieldwork)",
                "human": human_id,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        if self.machine.status == AuditStatus.WAITING_HUMAN_GATE_2:
            self.machine.approve_gate_2()
            self._commit_status()

    # ── Phase runners ────────────────────────────────────────────────────────

    def generate_planning(self, event_callback=None):
        """Phase 1 — Run the Planning Crew to produce a RACM."""
        self._detect_skills()

        # Handle direct call (e.g. Streamlit) where begin_phase_1 wasn't called first
        if self.machine.status == AuditStatus.WAITING_FOR_SCOPE:
            self.machine.start_phase_1()
            self._commit_status()

        logger.info("Starting Planning Phase...")
        self.state.qa_rejection_reason = None

        inputs = {
            "theme": self.state.theme,
            "business_context": self.state.business_context,
            "frameworks": ", ".join(self.state.frameworks),
            "qa_feedback": "",
        }

        try:
            crew = PlanningCrew(
                event_callback=event_callback, skill_context=self._skill_context
            ).crew()
            result = crew.kickoff(inputs=inputs)
        except Exception as exc:
            logger.exception("Planning crew failed")
            self.machine.error_phase_1()
            self._commit_status()
            self.state.qa_rejection_reason = f"Planning crew error: {exc}"
            return

        adapter = CrewResultAdapter(result)
        qa_output = adapter.get("qa_gate_task").pydantic
        racm_output = adapter.get("racm_drafting_task").pydantic

        if qa_output and not qa_output.approved:
            logger.warning(
                "QA rejected RACM — auto-retrying with feedback: %s",
                qa_output.rejection_reason,
            )
            inputs["qa_feedback"] = (
                f" IMPORTANT: A previous draft was rejected for the following reason — fix all issues before re-drafting: {qa_output.rejection_reason}"
            )
            try:
                crew = PlanningCrew(
                    event_callback=event_callback, skill_context=self._skill_context
                ).crew()
                result = crew.kickoff(inputs=inputs)
            except Exception as exc:
                logger.exception("Planning crew retry failed")
                self.machine.error_phase_1()
                self._commit_status()
                self.state.qa_rejection_reason = f"Planning crew retry error: {exc}"
                return
            adapter = CrewResultAdapter(result)
            qa_output = adapter.get("qa_gate_task").pydantic
            racm_output = adapter.get("racm_drafting_task").pydantic

        if qa_output and not qa_output.approved:
            self.machine.reject_phase_1()
            self._commit_status()
            self.state.qa_rejection_reason = qa_output.rejection_reason
            return

        if racm_output:
            self.state.racm_plan = racm_output

        self.machine.complete_phase_1()
        self._commit_status()
        self.state.current_human_dossier = (
            "Planning QA Loop complete. No major structural flaws found. "
            "Please review the RACM below for final IIA 2340 approval."
        )

    def generate_fieldwork(self, event_callback=None):
        """Phase 2 — Run the Fieldwork Crew to produce Working Papers.

        Call begin_phase_2(human_id) before this to stamp the approval trail and
        transition the machine. If called directly (e.g. Streamlit), the machine
        transition is handled here.
        """
        if self.machine.status == AuditStatus.WAITING_HUMAN_GATE_1:
            self.machine.approve_gate_1()
            self._commit_status()

        logger.info("Starting Fieldwork Execution Phase...")
        self.state.qa_rejection_reason = None

        racm_str = (
            self.state.racm_plan.model_dump_json() if self.state.racm_plan else ""
        )
        inputs = {"racm_string": racm_str, "qa_feedback": ""}

        try:
            crew = FieldworkCrew(
                event_callback=event_callback, skill_context=self._skill_context
            ).crew()
            result = crew.kickoff(inputs=inputs)
        except Exception as exc:
            logger.exception("Fieldwork crew failed")
            self.machine.error_phase_2()
            self._commit_status()
            self.state.qa_rejection_reason = f"Fieldwork crew error: {exc}"
            return

        adapter = CrewResultAdapter(result)
        qa_output = adapter.get("eval_qa_gate_task").pydantic
        papers_output = adapter.get("execution_evaluation_task").pydantic

        if qa_output and not qa_output.approved:
            logger.warning(
                "QA rejected Working Papers — auto-retrying with feedback: %s",
                qa_output.rejection_reason,
            )
            inputs["qa_feedback"] = (
                f" IMPORTANT: A previous evaluation was rejected — fix all severity and evidence issues: {qa_output.rejection_reason}"
            )
            try:
                crew = FieldworkCrew(
                    event_callback=event_callback, skill_context=self._skill_context
                ).crew()
                result = crew.kickoff(inputs=inputs)
            except Exception as exc:
                logger.exception("Fieldwork crew retry failed")
                self.machine.error_phase_2()
                self._commit_status()
                self.state.qa_rejection_reason = f"Fieldwork crew retry error: {exc}"
                return
            adapter = CrewResultAdapter(result)
            qa_output = adapter.get("eval_qa_gate_task").pydantic
            papers_output = adapter.get("execution_evaluation_task").pydantic

        if qa_output and not qa_output.approved:
            self.machine.reject_phase_2()
            self._commit_status()
            self.state.qa_rejection_reason = qa_output.rejection_reason
            return

        if papers_output:
            self.state.working_papers = papers_output

        self.machine.complete_phase_2()
        self._commit_status()
        self.state.current_human_dossier = (
            "Execution Fieldwork complete with Substantive Immutable Proofs evaluated. "
            "Please review Findings for final IIA 2340 approval."
        )

    def generate_reporting(self, event_callback=None):
        """Phase 3 — Run the Reporting Crew to produce the Final Report.

        Call begin_phase_3(human_id) before this to stamp the approval trail and
        transition the machine. If called directly (e.g. Streamlit), the machine
        transition is handled here.
        """
        if self.machine.status == AuditStatus.WAITING_HUMAN_GATE_2:
            self.machine.approve_gate_2()
            self._commit_status()

        logger.info("Starting Reporting Phase...")
        self.state.qa_rejection_reason = None

        papers_str = (
            self.state.working_papers.model_dump_json()
            if self.state.working_papers
            else ""
        )
        inputs = {
            "scope_string": self.state.business_context,
            "working_papers_string": papers_str,
            "tone_qa_feedback": "",
        }

        try:
            crew = ReportingCrew(
                event_callback=event_callback, skill_context=self._skill_context
            ).crew()
            result = crew.kickoff(inputs=inputs)
        except Exception as exc:
            logger.exception("Reporting crew failed")
            self.machine.error_phase_3()
            self._commit_status()
            self.state.qa_rejection_reason = f"Reporting crew error: {exc}"
            return

        adapter = CrewResultAdapter(result)
        report_output = adapter.get("final_report_assembly_task").pydantic
        qa_output = adapter.get("tone_qa_task").pydantic

        if qa_output and hasattr(qa_output, "approved") and not qa_output.approved:
            logger.warning(
                "QA rejected Report tone — auto-retrying with feedback: %s",
                getattr(qa_output, "rejection_reason", None),
            )
            inputs["tone_qa_feedback"] = (
                f" IMPORTANT: A previous draft was rejected for tone — fix all issues: "
                f"{getattr(qa_output, 'rejection_reason', '')}"
            )
            try:
                crew = ReportingCrew(
                    event_callback=event_callback, skill_context=self._skill_context
                ).crew()
                result = crew.kickoff(inputs=inputs)
            except Exception as exc:
                logger.exception("Reporting crew retry failed")
                self.machine.error_phase_3()
                self._commit_status()
                self.state.qa_rejection_reason = f"Reporting crew retry error: {exc}"
                return
            adapter = CrewResultAdapter(result)
            report_output = adapter.get("final_report_assembly_task").pydantic
            qa_output = adapter.get("tone_qa_task").pydantic

        if qa_output and hasattr(qa_output, "approved") and not qa_output.approved:
            self.machine.reject_phase_3()
            self._commit_status()
            self.state.qa_rejection_reason = getattr(
                qa_output, "rejection_reason", None
            )
            return

        if report_output:
            self.state.final_report = report_output

        self.machine.complete_audit()
        self._commit_status()

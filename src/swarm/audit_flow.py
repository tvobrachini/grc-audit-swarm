import logging
from datetime import datetime, UTC

from swarm.state.schema import AuditState  # noqa: F401 (re-exported for backwards compat)
from swarm.crews.planning_crew import PlanningCrew
from swarm.crews.fieldwork_crew import FieldworkCrew
from swarm.crews.reporting_crew import ReportingCrew

logger = logging.getLogger(__name__)


class AuditFlow:
    """
    Orchestrates the three-phase GRC audit: Planning → Fieldwork → Reporting.
    Each phase is gated by a human approval step.
    """

    def __init__(self):
        self.state = AuditState()

    def generate_planning(self):
        """Phase 1 — Run the Planning Crew to produce a RACM."""
        logger.info("Starting Planning Phase...")
        self.state.status = "RUNNING_PHASE_1"
        self.state.qa_rejection_reason = None

        inputs = {
            "theme": self.state.theme,
            "business_context": self.state.business_context,
            "frameworks": ", ".join(self.state.frameworks),
            "qa_feedback": "",
        }

        try:
            crew = PlanningCrew().crew()
            result = crew.kickoff(inputs=inputs)
        except Exception as exc:
            logger.exception("Planning crew failed")
            self.state.status = "ERROR"
            self.state.qa_rejection_reason = f"Planning crew error: {exc}"
            return

        # The last task is the QA gate (QA_PushbackSchema).
        # The actual RACM artifact is the second-to-last task output.
        qa_output = result.pydantic  # QA_PushbackSchema
        racm_output = (
            result.tasks_output[-2].pydantic if len(result.tasks_output) >= 2 else None
        )

        if qa_output and not qa_output.approved:
            # Auto-retry once with the rejection reason injected as feedback
            logger.warning(
                "QA rejected RACM — auto-retrying with feedback: %s",
                qa_output.rejection_reason,
            )
            inputs["qa_feedback"] = (
                f" IMPORTANT: A previous draft was rejected for the following reason — fix all issues before re-drafting: {qa_output.rejection_reason}"
            )
            try:
                crew = PlanningCrew().crew()
                result = crew.kickoff(inputs=inputs)
            except Exception as exc:
                logger.exception("Planning crew retry failed")
                self.state.status = "ERROR"
                self.state.qa_rejection_reason = f"Planning crew retry error: {exc}"
                return
            qa_output = result.pydantic
            racm_output = (
                result.tasks_output[-2].pydantic
                if len(result.tasks_output) >= 2
                else None
            )

        if qa_output and not qa_output.approved:
            self.state.status = "QA_REJECTED_PHASE_1"
            self.state.qa_rejection_reason = qa_output.rejection_reason
            return

        if racm_output:
            self.state.racm_plan = racm_output.model_dump()
        elif result.raw:
            self.state.racm_plan = {"raw": result.raw}

        self.state.status = "WAITING_HUMAN_GATE_1"
        self.state.current_human_dossier = (
            "Planning QA Loop complete. No major structural flaws found. "
            "Please review the RACM below for final IIA 2340 approval."
        )

    def generate_fieldwork(self, human_id: str):
        """Phase 2 — Run the Fieldwork Crew to produce Working Papers."""
        # IIA 2340 approval stamping
        self.state.approval_trail.append(
            {
                "gate": "Gate 1 (Planning)",
                "human": human_id,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

        logger.info("Starting Fieldwork Execution Phase...")
        self.state.status = "RUNNING_PHASE_2"
        self.state.qa_rejection_reason = None

        inputs = {"racm_string": str(self.state.racm_plan), "qa_feedback": ""}

        try:
            crew = FieldworkCrew().crew()
            result = crew.kickoff(inputs=inputs)
        except Exception as exc:
            logger.exception("Fieldwork crew failed")
            self.state.status = "ERROR"
            self.state.qa_rejection_reason = f"Fieldwork crew error: {exc}"
            return

        # Last task = QA gate (QA_PushbackSchema); second-to-last = WorkingPaperSchema
        qa_output = result.pydantic
        papers_output = (
            result.tasks_output[-2].pydantic if len(result.tasks_output) >= 2 else None
        )

        if qa_output and not qa_output.approved:
            # Auto-retry once with the QA rejection reason injected
            logger.warning(
                "QA rejected Working Papers — auto-retrying with feedback: %s",
                qa_output.rejection_reason,
            )
            inputs["qa_feedback"] = (
                f" IMPORTANT: A previous evaluation was rejected — fix all severity and evidence issues: {qa_output.rejection_reason}"
            )
            try:
                crew = FieldworkCrew().crew()
                result = crew.kickoff(inputs=inputs)
            except Exception as exc:
                logger.exception("Fieldwork crew retry failed")
                self.state.status = "ERROR"
                self.state.qa_rejection_reason = f"Fieldwork crew retry error: {exc}"
                return
            qa_output = result.pydantic
            papers_output = (
                result.tasks_output[-2].pydantic
                if len(result.tasks_output) >= 2
                else None
            )

        if qa_output and not qa_output.approved:
            self.state.status = "QA_REJECTED_PHASE_2"
            self.state.qa_rejection_reason = qa_output.rejection_reason
            return

        if papers_output:
            self.state.working_papers = papers_output.model_dump()

        self.state.status = "WAITING_HUMAN_GATE_2"
        self.state.current_human_dossier = (
            "Execution Fieldwork complete with Substantive Immutable Proofs evaluated. "
            "Please review Findings for final IIA 2340 approval."
        )

    def generate_reporting(self, human_id: str):
        """Phase 3 — Run the Reporting Crew to produce the Final Report."""
        # IIA 2340 approval stamping
        self.state.approval_trail.append(
            {
                "gate": "Gate 2 (Fieldwork)",
                "human": human_id,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

        logger.info("Starting Reporting Phase...")
        self.state.status = "RUNNING_PHASE_3"
        self.state.qa_rejection_reason = None

        inputs = {
            "scope_string": self.state.business_context,
            "working_papers_string": str(self.state.working_papers),
            "tone_qa_feedback": "",
        }

        try:
            crew = ReportingCrew().crew()
            result = crew.kickoff(inputs=inputs)
        except Exception as exc:
            logger.exception("Reporting crew failed")
            self.state.status = "ERROR"
            self.state.qa_rejection_reason = f"Reporting crew error: {exc}"
            return

        # Reporting crew order: drafting → summary → qa_tone → oscal → assembly (last)
        # result.pydantic = FinalReportSchema (last task = assembly)
        # tasks_output[-3].pydantic = QA_PushbackSchema (tone QA gate)
        report_output = result.pydantic
        qa_output = (
            result.tasks_output[-3].pydantic if len(result.tasks_output) >= 3 else None
        )

        if qa_output and hasattr(qa_output, "approved") and not qa_output.approved:
            # Auto-retry once with the tone rejection reason injected as feedback
            logger.warning(
                "QA rejected Report tone — auto-retrying with feedback: %s",
                getattr(qa_output, "rejection_reason", None),
            )
            inputs["tone_qa_feedback"] = (
                f" IMPORTANT: A previous draft was rejected for tone — fix all issues: "
                f"{getattr(qa_output, 'rejection_reason', '')}"
            )
            try:
                crew = ReportingCrew().crew()
                result = crew.kickoff(inputs=inputs)
            except Exception as exc:
                logger.exception("Reporting crew retry failed")
                self.state.status = "ERROR"
                self.state.qa_rejection_reason = f"Reporting crew retry error: {exc}"
                return
            report_output = result.pydantic
            qa_output = (
                result.tasks_output[-3].pydantic
                if len(result.tasks_output) >= 3
                else None
            )

        if qa_output and hasattr(qa_output, "approved") and not qa_output.approved:
            self.state.status = "QA_REJECTED_PHASE_3"
            self.state.qa_rejection_reason = getattr(
                qa_output, "rejection_reason", None
            )
            return

        if report_output:
            self.state.final_report = report_output.model_dump()

        self.state.status = "COMPLETED"

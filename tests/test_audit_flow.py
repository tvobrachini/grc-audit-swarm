"""
Tests for AuditFlow orchestration logic.
All CrewAI crew calls are mocked — no LLM keys required.
Covers: Phase 3 auto-retry, approval trail stamping, QA rejection states.
"""

import os
import sys
from unittest.mock import MagicMock, patch


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm.audit_flow import AuditFlow
from swarm.schema import FinalReportSchema, QA_PushbackSchema


def _make_qa_output(approved: bool, reason: str = ""):
    qa = MagicMock(spec=QA_PushbackSchema)
    qa.approved = approved
    qa.rejection_reason = reason
    return qa


def _make_report_output():
    report = MagicMock(spec=FinalReportSchema)
    report.model_dump.return_value = {
        "executive_summary": "All controls passed.",
        "detailed_report": "Detailed findings.",
        "compliance_tone_approved": True,
    }
    return report


def _make_crew_result(report_output, qa_output):
    """Build a mock crew kickoff result with 5 tasks (drafting, summary, qa, oscal, assembly)."""
    result = MagicMock()
    result.pydantic = report_output
    t1, t2, t3, t4, t5 = MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock()
    t3.pydantic = qa_output  # tasks_output[-3] = qa task
    result.tasks_output = [t1, t2, t3, t4, t5]
    return result


class TestGenerateReportingPhase3Retry:
    def _flow_with_fieldwork_done(self):
        flow = AuditFlow()
        flow.state.business_context = "Fintech S3 audit"
        flow.state.working_papers = {"theme": "S3", "findings": []}
        flow.state.status = "WAITING_HUMAN_GATE_2"
        return flow

    def test_approved_on_first_attempt_sets_completed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        flow = self._flow_with_fieldwork_done()

        qa_ok = _make_qa_output(approved=True)
        report = _make_report_output()
        crew_result = _make_crew_result(report, qa_ok)

        mock_crew = MagicMock()
        mock_crew.kickoff.return_value = crew_result

        with patch("swarm.audit_flow.ReportingCrew") as MockCrew:
            MockCrew.return_value.crew.return_value = mock_crew
            flow.generate_reporting(human_id="auditor@co.com")

        assert flow.state.status == "COMPLETED"
        assert flow.state.final_report is not None
        assert mock_crew.kickoff.call_count == 1

    def test_qa_rejection_triggers_single_retry(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        flow = self._flow_with_fieldwork_done()

        qa_rejected = _make_qa_output(approved=False, reason="Tone too aggressive")
        qa_ok = _make_qa_output(approved=True)
        report = _make_report_output()

        first_result = _make_crew_result(report, qa_rejected)
        second_result = _make_crew_result(report, qa_ok)

        mock_crew = MagicMock()
        mock_crew.kickoff.side_effect = [first_result, second_result]

        with patch("swarm.audit_flow.ReportingCrew") as MockCrew:
            MockCrew.return_value.crew.return_value = mock_crew
            flow.generate_reporting(human_id="auditor@co.com")

        assert flow.state.status == "COMPLETED"
        assert mock_crew.kickoff.call_count == 2

    def test_retry_injects_tone_feedback_into_inputs(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        flow = self._flow_with_fieldwork_done()

        qa_rejected = _make_qa_output(approved=False, reason="Subjective language used")
        qa_ok = _make_qa_output(approved=True)
        report = _make_report_output()

        first_result = _make_crew_result(report, qa_rejected)
        second_result = _make_crew_result(report, qa_ok)

        mock_crew = MagicMock()
        mock_crew.kickoff.side_effect = [first_result, second_result]

        with patch("swarm.audit_flow.ReportingCrew") as MockCrew:
            MockCrew.return_value.crew.return_value = mock_crew
            flow.generate_reporting(human_id="auditor@co.com")

        second_call_inputs = mock_crew.kickoff.call_args_list[1][1]["inputs"]
        assert "Subjective language used" in second_call_inputs.get(
            "tone_qa_feedback", ""
        )

    def test_double_rejection_sets_qa_rejected_phase_3(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        flow = self._flow_with_fieldwork_done()

        qa_rejected = _make_qa_output(approved=False, reason="Bad tone")
        report = _make_report_output()
        rejected_result = _make_crew_result(report, qa_rejected)

        mock_crew = MagicMock()
        mock_crew.kickoff.return_value = rejected_result

        with patch("swarm.audit_flow.ReportingCrew") as MockCrew:
            MockCrew.return_value.crew.return_value = mock_crew
            flow.generate_reporting(human_id="auditor@co.com")

        assert flow.state.status == "QA_REJECTED_PHASE_3"
        assert flow.state.qa_rejection_reason == "Bad tone"
        assert mock_crew.kickoff.call_count == 2

    def test_crew_exception_sets_error_status(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        flow = self._flow_with_fieldwork_done()

        mock_crew = MagicMock()
        mock_crew.kickoff.side_effect = RuntimeError("LLM timeout")

        with patch("swarm.audit_flow.ReportingCrew") as MockCrew:
            MockCrew.return_value.crew.return_value = mock_crew
            flow.generate_reporting(human_id="auditor@co.com")

        assert flow.state.status == "ERROR"
        assert "LLM timeout" in flow.state.qa_rejection_reason


class TestApprovalTrail:
    def test_gate1_approval_stamped_with_human_id(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        flow = AuditFlow()
        flow.state.racm_plan = {"theme": "S3", "risks": []}
        flow.state.status = "WAITING_HUMAN_GATE_1"

        qa_ok = MagicMock()
        qa_ok.approved = True
        qa_ok.rejection_reason = None

        result = MagicMock()
        result.pydantic = qa_ok
        t1, t2 = MagicMock(), MagicMock()
        t1.pydantic = MagicMock()
        t1.pydantic.model_dump.return_value = {}
        result.tasks_output = [t1, t2]

        mock_crew = MagicMock()
        mock_crew.kickoff.return_value = result

        with patch("swarm.audit_flow.FieldworkCrew") as MockCrew:
            MockCrew.return_value.crew.return_value = mock_crew
            flow.generate_fieldwork(human_id="jane.doe@company.com")

        assert any(
            e.get("human") == "jane.doe@company.com" for e in flow.state.approval_trail
        )

    def test_gate2_approval_stamped_with_human_id(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        flow = AuditFlow()
        flow.state.business_context = "ctx"
        flow.state.working_papers = {}

        qa_ok = _make_qa_output(approved=True)
        report = _make_report_output()
        crew_result = _make_crew_result(report, qa_ok)

        mock_crew = MagicMock()
        mock_crew.kickoff.return_value = crew_result

        with patch("swarm.audit_flow.ReportingCrew") as MockCrew:
            MockCrew.return_value.crew.return_value = mock_crew
            flow.generate_reporting(human_id="cfo@company.com")

        assert any(
            e.get("human") == "cfo@company.com" for e in flow.state.approval_trail
        )

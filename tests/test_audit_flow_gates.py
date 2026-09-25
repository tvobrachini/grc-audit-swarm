"""
Regression tests for AuditFlow QA gates, state-machine discipline, retry /
supervisor-override actions and skill-context persistence.
All crews are mocked — no LLM keys required.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flow_builders import (  # type: ignore[import-not-found]
    APPROVED,
    PHASES,
    crew_result,
    make_flow,
    make_papers,
    make_racm,
    run_phase,
)
from swarm import session_manager
from swarm.audit_flow import (
    QA_UNPARSEABLE_REASON,
    AuditFlow,
    PhaseArtifactMissingError,
)
from swarm.schema import QA_PushbackSchema
from swarm.state.machine import (
    AuditStateMachine,
    AuditStatus,
    InvalidTransitionError,
)
from swarm.state.repository import FlowRepository


# ── QA gates fail closed / missing artifact is an error ───────────────


@pytest.mark.parametrize("phase", [1, 2, 3])
class TestQaGatesFailClosed:
    def test_unparseable_qa_retries_then_rejects(self, phase):
        flow = make_flow(phase)
        artifact = PHASES[phase]["make"]()
        crew, _ = run_phase(
            flow,
            phase,
            [crew_result(phase, None, artifact), crew_result(phase, None, artifact)],
        )

        assert crew.kickoff.call_count == 2
        assert flow.state.status == f"QA_REJECTED_PHASE_{phase}"
        assert flow.machine.status.value == flow.state.status
        assert flow.state.qa_rejection_reason == QA_UNPARSEABLE_REASON
        # Rejected draft is kept so a supervisor can review / override it.
        assert getattr(flow.state, PHASES[phase]["field"]) == artifact

    def test_unparseable_qa_then_approval_advances(self, phase):
        flow = make_flow(phase)
        artifact = PHASES[phase]["make"]()
        crew, _ = run_phase(
            flow,
            phase,
            [
                crew_result(phase, None, artifact),
                crew_result(phase, APPROVED, artifact),
            ],
        )
        assert crew.kickoff.call_count == 2
        assert flow.state.status == f"WAITING_HUMAN_GATE_{phase}"
        # The retry carried the fail-closed reason as feedback.
        retry_inputs = crew.kickoff.call_args_list[1].kwargs["inputs"]
        assert any(
            isinstance(v, str) and QA_UNPARSEABLE_REASON in v
            for v in retry_inputs.values()
        )

    def test_qa_without_bool_approved_is_rejection(self, phase):
        flow = make_flow(phase)
        weird_qa = MagicMock(spec=[])  # no .approved attribute at all
        artifact = PHASES[phase]["make"]()
        run_phase(
            flow,
            phase,
            [
                crew_result(phase, weird_qa, artifact),
                crew_result(phase, weird_qa, artifact),
            ],
        )
        assert flow.state.status == f"QA_REJECTED_PHASE_{phase}"

    def test_missing_artifact_is_phase_error(self, phase):
        flow = make_flow(phase)
        field = PHASES[phase]["field"]
        before = getattr(flow.state, field)
        crew, _ = run_phase(flow, phase, [crew_result(phase, APPROVED, None)])

        assert crew.kickoff.call_count == 1
        assert flow.state.status == f"ERROR_PHASE_{phase}"
        assert flow.machine.status.value == f"ERROR_PHASE_{phase}"
        assert "could not be parsed" in flow.state.qa_rejection_reason
        assert getattr(flow.state, field) == before

    def test_missing_task_output_is_phase_error(self, phase):
        flow = make_flow(phase)
        result = MagicMock()
        result.tasks_output = []  # crew returned nothing we can map
        run_phase(flow, phase, [result])
        assert flow.state.status == f"ERROR_PHASE_{phase}"

    def test_approved_first_time_stores_artifact(self, phase):
        flow = make_flow(phase)
        artifact = PHASES[phase]["make"]()
        crew, _ = run_phase(flow, phase, [crew_result(phase, APPROVED, artifact)])
        assert crew.kickoff.call_count == 1
        assert flow.state.status == f"WAITING_HUMAN_GATE_{phase}"
        assert getattr(flow.state, PHASES[phase]["field"]) == artifact
        assert flow.state.qa_rejection_reason is None


# ── all status changes go through the machine ─────────────────────────


class TestMachineSourceStates:
    def test_gate_approval_cannot_restart_rejected_phase(self):
        m = AuditStateMachine(AuditStatus.QA_REJECTED_PHASE_2)
        with pytest.raises(InvalidTransitionError):
            m.approve_gate_1()
        assert m.status == AuditStatus.QA_REJECTED_PHASE_2

    def test_retry_cannot_skip_human_gate(self):
        m = AuditStateMachine(AuditStatus.WAITING_HUMAN_GATE_1)
        with pytest.raises(InvalidTransitionError):
            m.retry_phase_2()
        assert m.status == AuditStatus.WAITING_HUMAN_GATE_1

    @pytest.mark.parametrize("phase", [1, 2, 3])
    def test_retry_from_rejected_and_error(self, phase):
        for source in (f"QA_REJECTED_PHASE_{phase}", f"ERROR_PHASE_{phase}"):
            m = AuditStateMachine(AuditStatus(source))
            m.retry_phase(phase)
            assert m.status == AuditStatus(f"RUNNING_PHASE_{phase}")

    def test_override_only_from_qa_rejected(self):
        m = AuditStateMachine(AuditStatus.ERROR_PHASE_1)
        with pytest.raises(InvalidTransitionError):
            m.override_qa(1)
        m = AuditStateMachine(AuditStatus.QA_REJECTED_PHASE_1)
        m.override_qa(1)
        assert m.status == AuditStatus.WAITING_HUMAN_GATE_1


class TestGateMethodsRaiseOnWrongState:
    @pytest.mark.parametrize(
        "method,status",
        [
            ("begin_phase_2", "RUNNING_PHASE_1"),
            ("begin_phase_2", "RUNNING_PHASE_2"),  # double approval
            ("begin_phase_3", "WAITING_HUMAN_GATE_1"),
            ("begin_phase_3", "RUNNING_PHASE_3"),
            ("finalize_audit", "RUNNING_PHASE_3"),
            ("finalize_audit", "COMPLETED"),
        ],
    )
    def test_invalid_gate_raises_and_leaves_state_untouched(self, method, status):
        flow = AuditFlow(initial_status=status)
        with pytest.raises(InvalidTransitionError):
            getattr(flow, method)("alice")
        assert flow.state.status == status
        assert flow.machine.status.value == status
        assert flow.state.approval_trail == []

    def test_blank_approver_rejected(self):
        flow = AuditFlow(initial_status="WAITING_HUMAN_GATE_1")
        with pytest.raises(ValueError):
            flow.begin_phase_2("  ")
        assert flow.state.status == "WAITING_HUMAN_GATE_1"

    def test_gate_approval_stamps_action(self):
        flow = AuditFlow(initial_status="WAITING_HUMAN_GATE_1")
        flow.begin_phase_2("alice")
        entry = flow.state.approval_trail[-1]
        assert entry["gate"] == "Gate 1 (Planning)"
        assert entry["human"] == "alice"
        assert entry["action"] == "gate_approval"

    def test_generate_planning_refuses_wrong_state(self):
        flow = AuditFlow(initial_status="WAITING_HUMAN_GATE_1")
        with pytest.raises(RuntimeError):
            flow.generate_planning()


class TestRetryPhase:
    @pytest.mark.parametrize("phase", [1, 2, 3])
    def test_retry_after_rejection_reruns_phase(self, phase):
        flow = make_flow(phase)
        artifact = PHASES[phase]["make"]()
        run_phase(
            flow,
            phase,
            [crew_result(phase, None, artifact), crew_result(phase, None, artifact)],
        )
        assert flow.state.status == f"QA_REJECTED_PHASE_{phase}"

        flow.retry_phase(phase, "supervisor@co.com")
        assert flow.state.status == f"RUNNING_PHASE_{phase}"
        entry = flow.state.approval_trail[-1]
        assert entry["action"] == "retry"
        assert entry["human"] == "supervisor@co.com"
        assert entry["previous_status"] == f"QA_REJECTED_PHASE_{phase}"

        run_phase(flow, phase, [crew_result(phase, APPROVED, artifact)])
        assert flow.state.status == f"WAITING_HUMAN_GATE_{phase}"

    def test_retry_after_error(self):
        flow = make_flow(2)
        run_phase(flow, 2, [crew_result(2, APPROVED, None)])
        assert flow.state.status == "ERROR_PHASE_2"
        flow.retry_phase(2, "bob")
        assert flow.state.status == "RUNNING_PHASE_2"

    def test_retry_from_gate_raises(self):
        flow = AuditFlow(initial_status="WAITING_HUMAN_GATE_1")
        with pytest.raises(InvalidTransitionError):
            flow.retry_phase(2, "bob")
        assert flow.state.status == "WAITING_HUMAN_GATE_1"


_FEEDBACK_KEY = {1: "qa_feedback", 2: "qa_feedback", 3: "tone_qa_feedback"}


class TestRetryCarriesQaFeedback:
    """A human retry of a QA-rejected phase feeds the stored rejection reason
    back to the crew (same input the automatic retry uses)."""

    @pytest.mark.parametrize("phase", [1, 2, 3])
    def test_retry_injects_previous_rejection(self, phase):
        flow = make_flow(phase)
        artifact = PHASES[phase]["make"]()
        rejected = QA_PushbackSchema(
            approved=False, rejection_reason="Controls lack sampling {detail}"
        )
        run_phase(
            flow,
            phase,
            [crew_result(phase, rejected, artifact)] * 2,
        )
        assert flow.state.status == f"QA_REJECTED_PHASE_{phase}"

        flow.retry_phase(phase, "supervisor@co.com")
        mock_crew, _ = run_phase(flow, phase, [crew_result(phase, APPROVED, artifact)])

        inputs = mock_crew.kickoff.call_args.kwargs["inputs"]
        feedback = inputs[_FEEDBACK_KEY[phase]]
        assert "Controls lack sampling {detail}" in feedback
        assert feedback.startswith(" IMPORTANT")
        assert flow.state.status == f"WAITING_HUMAN_GATE_{phase}"

    def test_retry_after_error_has_no_feedback(self):
        flow = make_flow(2)
        run_phase(flow, 2, [crew_result(2, APPROVED, None)])
        assert flow.state.status == "ERROR_PHASE_2"
        flow.retry_phase(2, "bob")
        mock_crew, _ = run_phase(flow, 2, [crew_result(2, APPROVED, make_papers())])
        assert mock_crew.kickoff.call_args.kwargs["inputs"]["qa_feedback"] == ""

    def test_first_run_after_gate_has_no_feedback(self):
        # An old retry entry followed by a gate approval must not leak into
        # the next phase's first run.
        flow = make_flow(1)
        rejected = QA_PushbackSchema(approved=False, rejection_reason="bad RACM")
        run_phase(flow, 1, [crew_result(1, rejected, make_racm())] * 2)
        flow.retry_phase(1, "sup")
        run_phase(flow, 1, [crew_result(1, APPROVED, make_racm())])
        flow.begin_phase_2("sup")
        mock_crew, _ = run_phase(flow, 2, [crew_result(2, APPROVED, make_papers())])
        assert mock_crew.kickoff.call_args.kwargs["inputs"]["qa_feedback"] == ""

    def test_feedback_survives_reload(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            session_manager, "SESSIONS_PATH", str(tmp_path / "sessions.json")
        )
        flow = make_flow(1)
        rejected = QA_PushbackSchema(approved=False, rejection_reason="bad RACM")
        run_phase(flow, 1, [crew_result(1, rejected, make_racm())] * 2)
        flow.retry_phase(1, "sup")
        session_manager.save_session("s1", "n", flow.state.business_context)
        FlowRepository().save("s1", flow)

        loaded = FlowRepository().load("s1")
        assert loaded is not None
        mock_crew, _ = run_phase(
            loaded.flow, 1, [crew_result(1, APPROVED, make_racm())]
        )
        assert "bad RACM" in mock_crew.kickoff.call_args.kwargs["inputs"]["qa_feedback"]


class TestSupervisorOverride:
    def _rejected_flow(self, phase: int = 1) -> AuditFlow:
        flow = make_flow(phase)
        artifact = PHASES[phase]["make"]()
        rejected = QA_PushbackSchema(approved=False, rejection_reason="ToE weak")
        run_phase(
            flow,
            phase,
            [
                crew_result(phase, rejected, artifact),
                crew_result(phase, rejected, artifact),
            ],
        )
        assert flow.state.status == f"QA_REJECTED_PHASE_{phase}"
        return flow

    @pytest.mark.parametrize("phase", [1, 2, 3])
    def test_override_moves_to_gate_and_records_trail(self, phase):
        flow = self._rejected_flow(phase)
        flow.override_qa_rejection(
            phase, "cae@co.com", "Accepted: compensating control"
        )

        assert flow.state.status == f"WAITING_HUMAN_GATE_{phase}"
        entry = flow.state.approval_trail[-1]
        assert entry["action"] == "qa_override"
        assert entry["human"] == "cae@co.com"
        assert entry["reason"] == "Accepted: compensating control"
        assert entry["qa_rejection_reason"] == "ToE weak"
        assert flow.state.qa_rejection_reason is None

    def test_override_then_gate_approval_proceeds(self):
        flow = self._rejected_flow(1)
        flow.override_qa_rejection(1, "cae@co.com", "Accepted")
        flow.begin_phase_2("cae@co.com")
        assert flow.state.status == "RUNNING_PHASE_2"

    def test_override_requires_reason(self):
        flow = self._rejected_flow(1)
        with pytest.raises(ValueError):
            flow.override_qa_rejection(1, "cae@co.com", " ")
        assert flow.state.status == "QA_REJECTED_PHASE_1"

    def test_override_requires_artifact(self):
        flow = AuditFlow(initial_status="QA_REJECTED_PHASE_1")
        with pytest.raises(PhaseArtifactMissingError):
            flow.override_qa_rejection(1, "cae@co.com", "Accepted")
        assert flow.state.status == "QA_REJECTED_PHASE_1"

    def test_rejection_without_artifact_clears_stale_draft(self):
        # A stale draft from an earlier run must not be overridable.
        flow = make_flow(2)
        flow.state.working_papers = make_papers()
        run_phase(flow, 2, [crew_result(2, None, None), crew_result(2, None, None)])
        assert flow.state.status == "QA_REJECTED_PHASE_2"
        assert flow.state.working_papers is None
        with pytest.raises(PhaseArtifactMissingError):
            flow.override_qa_rejection(2, "cae@co.com", "Accepted")

    def test_override_wrong_state_raises(self):
        flow = AuditFlow(initial_status="ERROR_PHASE_1")
        flow.state.racm_plan = make_racm()
        with pytest.raises(InvalidTransitionError):
            flow.override_qa_rejection(1, "cae@co.com", "Accepted")


# ── skill context survives save / load ────────────────────────────────


class TestSkillContextPersistence:
    def test_skill_ids_persisted_and_restored(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            session_manager, "SESSIONS_PATH", str(tmp_path / "sessions.json")
        )
        flow = make_flow(1)
        run_phase(flow, 1, [crew_result(1, APPROVED, make_racm())])
        assert "aws_cloud_security" in flow.state.active_skill_ids

        session_manager.save_session("sess-1", "Audit", "scope")
        FlowRepository().save("sess-1", flow)
        loaded = FlowRepository().load("sess-1")
        assert loaded is not None
        restored = loaded.flow
        assert restored.state.active_skill_ids == flow.state.active_skill_ids
        assert [s["id"] for s in restored._skill_context] == (
            flow.state.active_skill_ids
        )

        restored.begin_phase_2("alice")
        _, MockCrew = run_phase(restored, 2, [crew_result(2, APPROVED, make_papers())])
        skill_context = MockCrew.call_args.kwargs["skill_context"]
        assert [s["id"] for s in skill_context] == flow.state.active_skill_ids

    def test_legacy_snapshot_without_ids_rederives_from_scope(self):
        flow = make_flow(2)
        assert flow.state.active_skill_ids == []
        _, MockCrew = run_phase(flow, 2, [crew_result(2, APPROVED, make_papers())])
        skill_context = MockCrew.call_args.kwargs["skill_context"]
        assert "aws_cloud_security" in [s["id"] for s in skill_context]

    def test_unknown_persisted_skill_id_is_skipped(self):
        flow = AuditFlow()
        flow.state.active_skill_ids = ["does_not_exist", "pci_dss"]
        flow.restore_skill_context()
        assert [s["id"] for s in flow._skill_context] == ["pci_dss"]

import os
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm.audit_workflow_service import AuditWorkflowService
from swarm.workflow_types import ViewPhase


class DummyState:
    def __init__(self, next_nodes, values):
        self.next = next_nodes
        self.values = values


def test_get_view_state_derives_phase_information():
    graph_service = Mock()
    graph_service.get_state.return_value = DummyState(
        ("human_review_execution",),
        {"control_matrix": [{"id": 1}], "testing_findings": [{"id": 2}]},
    )
    service = AuditWorkflowService(graph_service)

    view_state = service.get_view_state({"cfg": 1})

    assert view_state["view_phase"] == ViewPhase.PHASE2_REVIEW
    assert view_state["current_node"] == "human_review_execution"


def test_start_audit_updates_session_and_persists_record():
    service = AuditWorkflowService(Mock())
    session_state = {
        "thread_id": "thread-12345678",
        "chat_history": [],
        "scope_text_cache": "",
        "scope_submitted": False,
    }

    with (
        patch("swarm.audit_workflow_service.save_session") as save_session_mock,
        patch("swarm.audit_workflow_service.update_session") as update_session_mock,
    ):
        service.start_audit(session_state, "scope text", "")

    assert session_state["scope_text_cache"] == "scope text"
    assert session_state["scope_submitted"] is True
    assert session_state["chat_history"][-1]["role"] == "user"
    assert "Scope loaded" in session_state["chat_history"][-1]["content"]
    save_session_mock.assert_called_once()
    update_session_mock.assert_called_once()


def test_submit_phase2_feedback_resets_session_feedback_and_resumes():
    graph_service = Mock()
    service = AuditWorkflowService(graph_service)
    session_state = {
        "thread_id": "thread-1234",
        "chat_history": [],
        "scope_text_cache": "scope",
        "control_feedback": {"AC-01": "existing"},
        "resume_swarm": False,
    }

    with patch("swarm.audit_workflow_service.update_session"):
        service.submit_phase2_feedback(session_state, {"cfg": 1}, {"AC-01": "recheck"})

    graph_service.update_state.assert_called_once_with(
        {"cfg": 1}, {"control_feedback": {"AC-01": "recheck"}}
    )
    assert session_state["control_feedback"] == {}
    assert session_state["resume_swarm"] is True


def test_sync_state_snapshot_persists_structured_summary():
    service = AuditWorkflowService(Mock())
    session_state = {"thread_id": "thread-1234"}
    view_state = {
        "view_phase": ViewPhase.PHASE2_REVIEW,
        "next_nodes": ("human_review_execution",),
        "state_vals": {
            "control_matrix": [{"id": 1}],
            "testing_findings": [{"status": "Pass"}],
            "execution_status": {"AC-01": "clean"},
            "executive_summary": "summary",
        },
    }

    with patch("swarm.audit_workflow_service.update_session") as update_session_mock:
        service.sync_state_snapshot(session_state, view_state)

    update_session_mock.assert_called_once()
    snapshot = update_session_mock.call_args.kwargs["state_snapshot"]
    assert snapshot["view_phase"] == ViewPhase.PHASE2_REVIEW
    assert snapshot["finding_count"] == 1

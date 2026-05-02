"""
Tests for the FastAPI layer: models, job_store, and sessions router.
All AuditFlow / session_manager calls are mocked — no LLM keys required.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from api.job_store import (
    get_flow,
    get_job,
    get_queue,
    push_event,
    remove_flow,
    set_flow,
    set_job,
)
from api.models import (
    _needs_input,
    _phase_from_status,
)


# ---------------------------------------------------------------------------
# models.py helpers
# ---------------------------------------------------------------------------


class TestPhaseFromStatus:
    def test_waiting_for_scope_returns_0(self):
        assert _phase_from_status("WAITING_FOR_SCOPE") == 0

    def test_running_phase_1_returns_1(self):
        assert _phase_from_status("RUNNING_PHASE_1") == 1

    def test_running_phase_2_returns_2(self):
        assert _phase_from_status("RUNNING_PHASE_2") == 2

    def test_running_phase_3_returns_3(self):
        assert _phase_from_status("RUNNING_PHASE_3") == 3

    def test_waiting_human_gate_1_returns_1(self):
        assert _phase_from_status("WAITING_HUMAN_GATE_1") == 1

    def test_waiting_human_gate_2_returns_2(self):
        assert _phase_from_status("WAITING_HUMAN_GATE_2") == 2

    def test_completed_returns_3(self):
        assert _phase_from_status("COMPLETED") == 3

    def test_unknown_status_returns_0(self):
        assert _phase_from_status("UNKNOWN_STATUS") == 0


class TestNeedsInput:
    def test_waiting_human_gate_1_needs_input(self):
        assert _needs_input("WAITING_HUMAN_GATE_1") is True

    def test_waiting_human_gate_2_needs_input(self):
        assert _needs_input("WAITING_HUMAN_GATE_2") is True

    def test_waiting_human_gate_3_needs_input(self):
        assert _needs_input("WAITING_HUMAN_GATE_3") is True

    def test_running_phase_does_not_need_input(self):
        assert _needs_input("RUNNING_PHASE_1") is False

    def test_completed_does_not_need_input(self):
        assert _needs_input("COMPLETED") is False

    def test_waiting_for_scope_does_not_need_input(self):
        assert _needs_input("WAITING_FOR_SCOPE") is False


# ---------------------------------------------------------------------------
# job_store.py
# ---------------------------------------------------------------------------


class TestJobStore:
    def test_set_and_get_job_running(self):
        set_job("job-1", "running")
        assert get_job("job-1") == {"status": "running", "error": None}

    def test_set_job_completed(self):
        set_job("job-2", "completed")
        assert get_job("job-2")["status"] == "completed"

    def test_set_job_failed_with_error(self):
        set_job("job-3", "failed", "something broke")
        result = get_job("job-3")
        assert result["status"] == "failed"
        assert result["error"] == "something broke"

    def test_get_missing_job_returns_none(self):
        assert get_job("nonexistent-job-xyz") is None

    def test_set_and_remove_flow(self):
        flow = MagicMock()
        set_flow("sess-1", flow)
        assert get_flow("sess-1") is flow
        remove_flow("sess-1")
        assert get_flow("sess-1") is None

    def test_get_missing_flow_returns_none(self):
        assert get_flow("nonexistent-sess-xyz") is None

    def test_push_event_creates_queue(self):
        push_event("sess-push", {"type": "status", "status": "RUNNING_PHASE_1"})
        q = get_queue("sess-push")
        assert not q.empty()
        event = q.get_nowait()
        assert event == {"type": "status", "status": "RUNNING_PHASE_1"}

    def test_push_multiple_events_preserves_order(self):
        push_event("sess-order", {"type": "a"})
        push_event("sess-order", {"type": "b"})
        q = get_queue("sess-order")
        assert q.get_nowait()["type"] == "a"
        assert q.get_nowait()["type"] == "b"


# ---------------------------------------------------------------------------
# sessions router — via TestClient
# ---------------------------------------------------------------------------


def _make_mock_flow(status="WAITING_FOR_SCOPE"):
    flow = MagicMock()
    flow.state.status = status
    flow.state.theme = "Test Theme"
    flow.state.business_context = "Some context"
    flow.state.frameworks = ["SOC2"]
    flow.state.current_human_dossier = ""
    flow.state.racm_plan = None
    flow.state.working_papers = None
    flow.state.final_report = None
    flow.state.approval_trail = []
    flow.state.qa_rejection_reason = None
    flow.state.model_dump.return_value = {"status": status}
    return flow


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "test-token")
    from api.main import app

    return TestClient(app)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


class TestSessionsList:
    def test_anonymous_request_is_rejected(self, client):
        resp = client.get("/api/sessions")
        assert resp.status_code == 401

    def test_health_check_remains_public(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_empty_list_returns_200(self, client):
        with patch("api.routers.sessions.list_sessions", return_value={}):
            resp = client.get("/api/sessions", headers=_auth_headers())
        assert resp.status_code == 200
        assert resp.json() == []

    def test_sessions_returned_as_list(self, client):
        sessions = {
            "sess-abc": {
                "name": "IAM Audit",
                "status": "WAITING_FOR_SCOPE",
                "created_at": "2026-01-01T00:00:00",
            }
        }
        with (
            patch("api.routers.sessions.list_sessions", return_value=sessions),
            patch("api.routers.sessions.get_flow", return_value=None),
        ):
            resp = client.get("/api/sessions", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["session_id"] == "sess-abc"
        assert data[0]["name"] == "IAM Audit"


class TestSessionsGet:
    def test_missing_session_returns_404(self, client):
        with patch("api.routers.sessions.get_session", return_value=None):
            resp = client.get("/api/sessions/nonexistent-id", headers=_auth_headers())
        assert resp.status_code == 404

    def test_existing_session_returns_detail(self, client):
        data = {
            "name": "S3 Audit",
            "created_at": "2026-01-01T00:00:00",
            "state_snapshot": {"status": "COMPLETED"},
        }
        with (
            patch("api.routers.sessions.get_session", return_value=data),
            patch("api.routers.sessions.get_flow", return_value=None),
        ):
            resp = client.get("/api/sessions/some-id", headers=_auth_headers())
        assert resp.status_code == 200
        assert resp.json()["name"] == "S3 Audit"


class TestSessionsDelete:
    def test_delete_returns_204(self, client):
        with (
            patch("api.routers.sessions.delete_session"),
            patch("api.routers.sessions.remove_flow"),
        ):
            resp = client.delete("/api/sessions/sess-del", headers=_auth_headers())
        assert resp.status_code == 204


class TestSessionsCreate:
    def test_create_launches_phase_1(self, client):
        mock_flow = _make_mock_flow("RUNNING_PHASE_1")
        mock_executor = MagicMock()
        with (
            patch("api.routers.sessions.AuditFlow", return_value=mock_flow),
            patch("api.routers.sessions.set_flow"),
            patch("api.routers.sessions.save_session"),
            patch("api.routers.sessions.set_job"),
            patch("api.routers.sessions.get_executor", return_value=mock_executor),
        ):
            resp = client.post(
                "/api/sessions",
                headers=_auth_headers(),
                json={
                    "theme": "IAM Review",
                    "business_context": "AWS IAM posture review",
                    "frameworks": ["SOC2"],
                },
            )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "RUNNING_PHASE_1"
        assert data["needs_input"] is False


class TestApproveGate:
    def test_missing_session_returns_404(self, client):
        with patch("api.routers.sessions.get_session", return_value=None):
            resp = client.patch(
                "/api/sessions/nonexistent/approve",
                headers=_auth_headers(),
                json={"gate_number": 1, "human_id": "alice"},
            )
        assert resp.status_code == 404

    def test_gate_3_returns_400(self, client):
        data = {"name": "Test", "created_at": "2026-01-01T00:00:00"}
        with (
            patch("api.routers.sessions.get_session", return_value=data),
            patch("api.routers.sessions.get_flow", return_value=None),
            patch("api.routers.sessions.set_job"),
        ):
            resp = client.patch(
                "/api/sessions/sess-x/approve",
                headers=_auth_headers(),
                json={"gate_number": 3, "human_id": "alice"},
            )
        assert resp.status_code == 400

    def test_gate_1_approval_starts_phase_2(self, client):
        session_data = {"name": "IAM Audit", "created_at": "2026-01-01T00:00:00"}
        mock_flow = _make_mock_flow("WAITING_HUMAN_GATE_1")
        mock_executor = MagicMock()
        with (
            patch("api.routers.sessions.get_session", return_value=session_data),
            patch("api.routers.sessions.get_flow", return_value=mock_flow),
            patch("api.routers.sessions.set_job"),
            patch("api.routers.sessions.get_executor", return_value=mock_executor),
        ):
            resp = client.patch(
                "/api/sessions/sess-g1/approve",
                headers=_auth_headers(),
                json={"gate_number": 1, "human_id": "alice"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "RUNNING_PHASE_2"
        assert data["needs_input"] is False


class TestJobStatus:
    def test_anonymous_request_is_rejected(self, client):
        resp = client.get("/api/jobs/job-123/status")
        assert resp.status_code == 401

    def test_missing_job_returns_404(self, client):
        with patch("api.routers.phases.get_job", return_value=None):
            resp = client.get("/api/jobs/nonexistent/status", headers=_auth_headers())
        assert resp.status_code == 404

    def test_existing_job_returns_200(self, client):
        job_data = {"status": "completed", "error": None}
        with patch("api.routers.phases.get_job", return_value=job_data):
            resp = client.get("/api/jobs/job-1/status", headers=_auth_headers())
        assert resp.status_code == 200
        assert resp.json() == job_data

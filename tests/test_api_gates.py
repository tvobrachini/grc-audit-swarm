"""
API gate idempotency, retry and QA-override endpoints.

Uses real AuditFlow instances and a temp sessions file; crews are mocked and
the phase executor is replaced so no crew runs unless a test asks for it.
"""

import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flow_builders import (  # type: ignore[import-not-found]
    APPROVED,
    crew_result,
    make_racm,
    make_report,
    make_verified_papers,
)
from api.job_store import get_flow, remove_flow, set_flow
from swarm import session_manager
from swarm.audit_flow import AuditFlow
from swarm.state.repository import FlowRepository

AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("API_AUTH_TOKEN", "test-token")
    monkeypatch.setattr(
        session_manager, "SESSIONS_PATH", str(tmp_path / "audit_sessions.json")
    )
    from api.main import app

    return TestClient(app)


@pytest.fixture
def executor():
    """Record submissions without running them."""
    ex = MagicMock()
    with patch("api.routers.sessions.get_executor", return_value=ex):
        yield ex


def _new_session(
    status: str,
    *,
    cache: bool = True,
    racm: bool = True,
    papers: bool = False,
    report: bool = False,
    prepared_by: str = "",
) -> str:
    sid = f"sess-{uuid.uuid4()}"
    flow = AuditFlow(initial_status=status)
    flow.state.theme = "AWS S3"
    flow.state.business_context = "Fintech storing customer data in S3"
    if prepared_by:
        flow.record_preparer(prepared_by)
    if racm:
        flow.state.racm_plan = make_racm()
    if papers:
        flow.state.working_papers = make_verified_papers()
    if report:
        flow.state.final_report = make_report()
    session_manager.save_session(sid, "S3 audit", flow.state.business_context)
    FlowRepository().save(sid, flow)
    if cache:
        set_flow(sid, flow)
    else:
        remove_flow(sid)
    return sid


def _approve(client, sid, gate=1, human="alice"):
    return client.patch(
        f"/api/sessions/{sid}/approve",
        headers=AUTH,
        json={"gate_number": gate, "human_id": human},
    )


class TestApproveIdempotency:
    def test_double_approve_runs_one_crew(self, client, executor):
        sid = _new_session("WAITING_HUMAN_GATE_1")

        first = _approve(client, sid)
        second = _approve(client, sid)

        assert first.status_code == 200
        assert first.json()["status"] == "RUNNING_PHASE_2"
        assert second.status_code == 409
        assert "RUNNING_PHASE_2" in second.json()["detail"]
        assert executor.submit.call_count == 1
        trail = get_flow(sid).state.approval_trail
        assert [e["gate"] for e in trail] == ["Gate 1 (Planning)"]

    def test_concurrent_approvals_on_uncached_flow_run_one_crew(self, client, executor):
        # Flow only on disk: without the per-session lock both requests would
        # load *separate* AuditFlow copies, both transition, both submit.
        sid = _new_session("WAITING_HUMAN_GATE_1", cache=False)
        real_load = FlowRepository.load
        barrier = threading.Barrier(2, timeout=5)

        def slow_load(self, session_id):
            result = real_load(self, session_id)
            time.sleep(0.05)
            return result

        def call(_):
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass
            return _approve(client, sid).status_code

        with patch.object(FlowRepository, "load", slow_load):
            with ThreadPoolExecutor(max_workers=2) as pool:
                codes = sorted(pool.map(call, range(2)))

        assert codes == [200, 409]
        assert executor.submit.call_count == 1

    def test_wrong_state_returns_409_and_submits_nothing(self, client, executor):
        sid = _new_session("RUNNING_PHASE_1", racm=False)
        resp = _approve(client, sid, gate=1)
        assert resp.status_code == 409
        executor.submit.assert_not_called()
        assert get_flow(sid).state.approval_trail == []

    def test_gate_2_before_gate_1_is_409(self, client, executor):
        sid = _new_session("WAITING_HUMAN_GATE_1")
        assert _approve(client, sid, gate=2).status_code == 409
        executor.submit.assert_not_called()

    def test_gate_3_twice_is_409(self, client, executor):
        sid = _new_session("WAITING_HUMAN_GATE_3", report=True)
        assert _approve(client, sid, gate=3).status_code == 200
        assert _approve(client, sid, gate=3).status_code == 409
        saved = session_manager.get_session(sid)
        assert saved["status"] == "COMPLETED"
        assert saved["state_snapshot"]["status"] == "COMPLETED"

    def test_blank_approver_is_422(self, client, executor):
        sid = _new_session("WAITING_HUMAN_GATE_1")
        assert _approve(client, sid, human="   ").status_code == 422
        executor.submit.assert_not_called()
        assert get_flow(sid).state.status == "WAITING_HUMAN_GATE_1"

    def test_session_without_flow_is_404(self, client, executor):
        sid = f"sess-{uuid.uuid4()}"
        session_manager.save_session(sid, "Empty", "ctx")  # no snapshot
        assert _approve(client, sid).status_code == 404
        executor.submit.assert_not_called()


class TestRetryEndpoint:
    def test_retry_rejected_phase_submits_crew(self, client, executor):
        sid = _new_session("QA_REJECTED_PHASE_2")
        resp = client.post(
            f"/api/sessions/{sid}/retry",
            headers=AUTH,
            json={"phase": 2, "human_id": "supervisor"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "RUNNING_PHASE_2"
        assert executor.submit.call_count == 1
        from api.routers.sessions import _run_phase_2

        assert executor.submit.call_args.args[1] is _run_phase_2
        entry = get_flow(sid).state.approval_trail[-1]
        assert entry["action"] == "retry"
        assert entry["human"] == "supervisor"

    def test_retry_errored_phase_1(self, client, executor):
        sid = _new_session("ERROR_PHASE_1", racm=False)
        resp = client.post(
            f"/api/sessions/{sid}/retry",
            headers=AUTH,
            json={"phase": 1, "human_id": "supervisor"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "RUNNING_PHASE_1"

    def test_retry_wrong_phase_is_409(self, client, executor):
        sid = _new_session("QA_REJECTED_PHASE_2")
        resp = client.post(
            f"/api/sessions/{sid}/retry",
            headers=AUTH,
            json={"phase": 1, "human_id": "supervisor"},
        )
        assert resp.status_code == 409
        executor.submit.assert_not_called()

    def test_double_retry_is_409(self, client, executor):
        sid = _new_session("ERROR_PHASE_2")
        body = {"phase": 2, "human_id": "supervisor"}
        assert (
            client.post(f"/api/sessions/{sid}/retry", headers=AUTH, json=body)
        ).status_code == 200
        assert (
            client.post(f"/api/sessions/{sid}/retry", headers=AUTH, json=body)
        ).status_code == 409
        assert executor.submit.call_count == 1

    @pytest.mark.parametrize(
        "body", [{"phase": 4, "human_id": "x"}, {"phase": 1, "human_id": ""}]
    )
    def test_invalid_body_is_422(self, client, executor, body):
        sid = _new_session("ERROR_PHASE_1", racm=False)
        resp = client.post(f"/api/sessions/{sid}/retry", headers=AUTH, json=body)
        assert resp.status_code == 422

    def test_missing_session_is_404(self, client, executor):
        resp = client.post(
            "/api/sessions/nope/retry",
            headers=AUTH,
            json={"phase": 1, "human_id": "x"},
        )
        assert resp.status_code == 404

    def test_anonymous_is_401(self, client):
        resp = client.post(
            "/api/sessions/nope/retry", json={"phase": 1, "human_id": "x"}
        )
        assert resp.status_code == 401


class TestQAOverrideEndpoint:
    def _override(self, client, sid, **kw):
        body = {"phase": 1, "human_id": "cae@co.com", "reason": "Accepted risk"}
        body.update(kw)
        return client.post(f"/api/sessions/{sid}/qa-override", headers=AUTH, json=body)

    def test_override_moves_to_gate_and_persists(self, client, executor):
        sid = _new_session("QA_REJECTED_PHASE_1")
        resp = self._override(client, sid)

        assert resp.status_code == 200
        assert resp.json()["status"] == "WAITING_HUMAN_GATE_1"
        assert resp.json()["needs_input"] is True
        executor.submit.assert_not_called()

        saved = session_manager.get_session(sid)
        assert saved["state_snapshot"]["status"] == "WAITING_HUMAN_GATE_1"
        entry = saved["state_snapshot"]["approval_trail"][-1]
        assert entry["action"] == "qa_override"
        assert entry["human"] == "cae@co.com"
        assert entry["reason"] == "Accepted risk"

        # The normal gate approval then proceeds.
        assert _approve(client, sid, gate=1).status_code == 200

    def test_override_without_artifact_is_409(self, client, executor):
        sid = _new_session("QA_REJECTED_PHASE_1", racm=False)
        assert self._override(client, sid).status_code == 409

    def test_override_wrong_state_is_409(self, client, executor):
        sid = _new_session("ERROR_PHASE_1")
        assert self._override(client, sid).status_code == 409

    def test_override_blank_reason_is_422(self, client, executor):
        sid = _new_session("QA_REJECTED_PHASE_1")
        assert self._override(client, sid, reason="").status_code == 422
        assert self._override(client, sid, reason="   ").status_code == 422
        assert get_flow(sid).state.status == "QA_REJECTED_PHASE_1"


class TestPhaseRunIntegration:
    def test_gate_1_runs_fieldwork_and_persists(self, client):
        sid = _new_session("WAITING_HUMAN_GATE_1")
        submitted = []
        ex = MagicMock()
        ex.submit.side_effect = lambda _sid, fn, *args: submitted.append((fn, args))

        mock_crew = MagicMock()
        mock_crew.kickoff.return_value = crew_result(
            2, APPROVED, make_verified_papers()
        )
        with (
            patch("api.routers.sessions.get_executor", return_value=ex),
            patch("swarm.audit_flow.FieldworkCrew") as MockCrew,
        ):
            MockCrew.return_value.crew.return_value = mock_crew
            assert _approve(client, sid).status_code == 200
            fn, args = submitted[0]
            fn(*args)

        saved = session_manager.get_session(sid)
        assert saved["status"] == "WAITING_HUMAN_GATE_2"
        finding = saved["state_snapshot"]["working_papers"]["findings"][0]
        assert finding["exact_quote_from_evidence"] == "BlockPublicAcls: true"

    def test_phase_finishing_after_delete_does_not_resurrect(self, client):
        # Only unapproved drafts can be deleted, so the race is a Planning
        # re-run finishing after its draft audit was deleted.
        sid = _new_session("ERROR_PHASE_1", racm=False)
        submitted = []
        ex = MagicMock()
        ex.submit.side_effect = lambda _sid, fn, *args: submitted.append((fn, args))

        mock_crew = MagicMock()
        mock_crew.kickoff.return_value = crew_result(1, APPROVED, make_racm())
        with (
            patch("api.routers.sessions.get_executor", return_value=ex),
            patch("swarm.audit_flow.PlanningCrew") as MockCrew,
        ):
            MockCrew.return_value.crew.return_value = mock_crew
            resp = client.post(
                f"/api/sessions/{sid}/retry",
                headers=AUTH,
                json={"phase": 1, "human_id": "alice"},
            )
            assert resp.status_code == 200
            flow = get_flow(sid)
            assert (
                client.delete(f"/api/sessions/{sid}", headers=AUTH).status_code == 204
            )

            # The phase thread still holds its flow reference and finishes.
            with patch("api.routers.sessions._get_or_load_flow", return_value=flow):
                fn, args = submitted[0]
                fn(*args)

        assert session_manager.get_session(sid) is None
        assert client.get(f"/api/sessions/{sid}", headers=AUTH).status_code == 404


class TestSnapshotDetailMigratesArtifacts:
    """A session read from disk (not in memory) returns current-schema artifacts."""

    def test_legacy_severity_is_migrated_in_snapshot_detail(self):
        import json as _json

        from api.routers.sessions import _migrated_snapshot_artifact
        from swarm.schema import WorkingPaperSchema

        path = os.path.join(
            os.path.dirname(__file__), "mock_data", "legacy_session_snapshot.json"
        )
        with open(path) as fh:
            legacy = _json.load(fh)["working_papers"]
        assert any("severity" in f for f in legacy["findings"])

        migrated = _migrated_snapshot_artifact(WorkingPaperSchema, legacy)

        for finding in migrated["findings"]:
            assert "toe_conclusion" in finding and "result" in finding
            assert finding.get("legacy_severity")

    def test_unvalidatable_artifact_is_returned_as_stored(self):
        from api.routers.sessions import _migrated_snapshot_artifact
        from swarm.schema import WorkingPaperSchema

        raw = {"not": "a working paper"}
        assert _migrated_snapshot_artifact(WorkingPaperSchema, raw) == raw
        assert _migrated_snapshot_artifact(WorkingPaperSchema, None) is None

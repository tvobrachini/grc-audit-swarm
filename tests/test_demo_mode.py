"""
DEMO_MODE: fixed artifacts through the real AuditFlow state machine and API.

No crew class may be constructed in demo mode — the real crews are patched to
fail loudly if they are.
"""

import os
import sys
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from api.job_store import get_queue, remove_flow
from swarm import session_manager
from swarm.audit_flow import AuditFlow
from swarm.demo import (
    DEMO_LABEL,
    DemoCrew,
    DemoModeNotAllowedError,
    demo_final_report,
    demo_mode_enabled,
    demo_racm,
    demo_working_papers,
)
from swarm.schema import (
    FinalReportSchema,
    RiskControlMatrixSchema,
    WorkingPaperSchema,
)

AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
def demo_env(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("DEMO_STEP_DELAY", "0")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("DEMO_QA_REJECT_PHASE", raising=False)


@pytest.fixture
def no_real_crews():
    boom = AssertionError("a real crew was built in DEMO_MODE")
    with (
        patch("swarm.audit_flow.PlanningCrew", side_effect=boom) as p,
        patch("swarm.audit_flow.FieldworkCrew", side_effect=boom) as f,
        patch("swarm.audit_flow.ReportingCrew", side_effect=boom) as r,
    ):
        yield p, f, r


# ── Guard ────────────────────────────────────────────────────────────────


class TestGuard:
    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv("DEMO_MODE", raising=False)
        assert demo_mode_enabled() is False

    @pytest.mark.parametrize("env", ["local", "dev", "development", ""])
    def test_allowed_outside_production(self, monkeypatch, env):
        monkeypatch.setenv("DEMO_MODE", "1")
        monkeypatch.setenv("ENVIRONMENT", env)
        assert demo_mode_enabled() is True

    @pytest.mark.parametrize("env", ["production", "Production", "staging", "prod"])
    def test_refused_in_production_and_staging(self, monkeypatch, env):
        monkeypatch.setenv("DEMO_MODE", "1")
        monkeypatch.setenv("ENVIRONMENT", env)
        with pytest.raises(DemoModeNotAllowedError, match="not allowed"):
            demo_mode_enabled()

    def test_production_without_demo_is_fine(self, monkeypatch):
        monkeypatch.delenv("DEMO_MODE", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "production")
        assert demo_mode_enabled() is False

    def test_api_startup_fails_in_production(self, monkeypatch):
        monkeypatch.setenv("DEMO_MODE", "1")
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("API_AUTH_TOKEN", "test-token")
        from api.main import app

        with pytest.raises(DemoModeNotAllowedError):
            with TestClient(app):
                pass

    def test_flow_refuses_demo_crew_in_production(self, monkeypatch, no_real_crews):
        monkeypatch.setenv("DEMO_MODE", "1")
        monkeypatch.setenv("ENVIRONMENT", "staging")
        flow = AuditFlow()
        flow.state.theme = "S3"
        flow.generate_planning()
        # The guard error surfaces as a phase error, never as a demo artifact.
        assert flow.state.status == "ERROR_PHASE_1"
        assert flow.state.racm_plan is None
        assert "not allowed" in (flow.state.qa_rejection_reason or "")


# ── Artifacts ────────────────────────────────────────────────────────────


class TestArtifacts:
    def test_artifacts_are_schema_valid_and_labelled(self):
        racm = demo_racm("S3 exposure")
        papers = demo_working_papers("S3 exposure", racm.model_dump_json())
        report = demo_final_report("S3 exposure", papers.model_dump_json())
        RiskControlMatrixSchema.model_validate(racm.model_dump())
        WorkingPaperSchema.model_validate(papers.model_dump())
        FinalReportSchema.model_validate(report.model_dump())

        assert DEMO_LABEL in racm.theme
        assert all(DEMO_LABEL in f.test_conclusion for f in papers.findings)
        assert all(
            f.vault_id_reference.startswith("DEMO-NOT-IN-VAULT")
            for f in papers.findings
        )
        assert report.executive_summary.startswith("DEMO DATA")
        assert "no AWS account was examined" in report.detailed_report
        assert report.oscal_sar is not None
        assert DEMO_LABEL in report.oscal_sar.metadata.title

    def test_findings_follow_racm_controls(self):
        racm = demo_racm()
        papers = demo_working_papers("", racm.model_dump_json())
        assert [f.control_id for f in papers.findings] == ["CTRL-01", "CTRL-02"]

    def test_no_compliance_claims(self):
        text = demo_final_report().model_dump_json().lower()
        for claim in ("compliant", "compliance with", "certif", "attest"):
            assert claim not in text

    def test_deterministic(self):
        assert (
            demo_final_report("x").model_dump() == demo_final_report("x").model_dump()
        )

    def test_emits_step_events(self):
        steps = []
        DemoCrew(1, event_callback=steps.append).kickoff(inputs={"theme": "S3"})
        assert len(steps) == 5
        assert all(s.agent and s.output for s in steps)


# ── AuditFlow in demo mode ───────────────────────────────────────────────


class TestFlow:
    def test_full_run_through_three_gates(self, demo_env, no_real_crews):
        flow = AuditFlow()
        flow.state.theme = "S3 exposure"
        flow.state.business_context = "Fintech"
        events = []
        flow.generate_planning(event_callback=events.append)
        assert flow.state.status == "WAITING_HUMAN_GATE_1"
        flow.begin_phase_2("alice")
        flow.generate_fieldwork(event_callback=events.append)
        assert flow.state.status == "WAITING_HUMAN_GATE_2"
        flow.begin_phase_3("alice")
        flow.generate_reporting(event_callback=events.append)
        assert flow.state.status == "WAITING_HUMAN_GATE_3"
        flow.finalize_audit("alice")
        assert flow.state.status == "COMPLETED"
        assert [e["gate"] for e in flow.state.approval_trail] == [
            "Gate 1 (Planning)",
            "Gate 2 (Fieldwork)",
            "Gate 3 (Reporting)",
        ]
        assert len(events) == 5 + 3 + 5
        assert flow.state.final_report is not None
        for crew in no_real_crews:
            crew.assert_not_called()

    def test_simulated_qa_rejection_then_retry(
        self, demo_env, no_real_crews, monkeypatch
    ):
        monkeypatch.setenv("DEMO_QA_REJECT_PHASE", "1")
        flow = AuditFlow()
        flow.state.theme = "S3"
        flow.generate_planning()
        assert flow.state.status == "QA_REJECTED_PHASE_1"
        assert flow.state.racm_plan is not None  # rejected draft kept
        assert "Simulated QA rejection" in (flow.state.qa_rejection_reason or "")
        flow.retry_phase(1, "sup")
        flow.generate_planning()
        assert flow.state.status == "WAITING_HUMAN_GATE_1"

    def test_simulated_qa_rejection_then_override(
        self, demo_env, no_real_crews, monkeypatch
    ):
        monkeypatch.setenv("DEMO_QA_REJECT_PHASE", "1")
        flow = AuditFlow()
        flow.generate_planning()
        flow.override_qa_rejection(1, "sup", "Reviewed the draft manually")
        assert flow.state.status == "WAITING_HUMAN_GATE_1"
        assert flow.state.approval_trail[-1]["action"] == "qa_override"


# ── API end to end ───────────────────────────────────────────────────────


class _InlineExecutor:
    """Runs phase jobs synchronously so the test sees their result."""

    def submit(self, session_id, fn, *args):
        fn(*args)


@pytest.fixture
def client(demo_env, no_real_crews, monkeypatch, tmp_path):
    monkeypatch.setenv("API_AUTH_TOKEN", "test-token")
    monkeypatch.setattr(
        session_manager, "SESSIONS_PATH", str(tmp_path / "audit_sessions.json")
    )
    from api.main import app

    with patch("api.routers.sessions.get_executor", return_value=_InlineExecutor()):
        yield TestClient(app)


def _drain(sid):
    q = get_queue(sid)
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_config_reports_demo_mode(client):
    r = client.get("/api/config", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"demo_mode": True}


def test_config_requires_auth(client):
    assert client.get("/api/config").status_code == 401


def test_config_reports_off(client, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "0")
    assert client.get("/api/config", headers=AUTH).json() == {"demo_mode": False}


def test_api_demo_run_end_to_end(client):
    r = client.post(
        "/api/sessions",
        headers=AUTH,
        json={"theme": "S3 exposure", "business_context": "Fintech"},
    )
    assert r.status_code == 201
    sid = r.json()["session_id"]
    try:
        detail = client.get(f"/api/sessions/{sid}", headers=AUTH).json()
        assert detail["status"] == "WAITING_HUMAN_GATE_1"
        assert DEMO_LABEL in detail["racm_plan"]["theme"]

        events = _drain(sid)
        types = [e["type"] for e in events]
        assert types.count("agent_step") == 5
        assert types[-1] == "complete"

        for gate, expected in (
            (1, "WAITING_HUMAN_GATE_2"),
            (2, "WAITING_HUMAN_GATE_3"),
            (3, "COMPLETED"),
        ):
            r = client.patch(
                f"/api/sessions/{sid}/approve",
                headers=AUTH,
                json={"gate_number": gate, "human_id": "alice"},
            )
            assert r.status_code == 200, r.text
            detail = client.get(f"/api/sessions/{sid}", headers=AUTH).json()
            assert detail["status"] == expected

        assert len(detail["approval_trail"]) == 3
        assert detail["final_report"]["oscal_sar"] is not None
        # Persisted: a reload from disk sees the completed audit.
        remove_flow(sid)
        detail = client.get(f"/api/sessions/{sid}", headers=AUTH).json()
        assert detail["status"] == "COMPLETED"
    finally:
        client.delete(f"/api/sessions/{sid}", headers=AUTH)

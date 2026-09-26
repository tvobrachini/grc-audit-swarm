"""
Review mechanics: return for rework, segregation of duties, deterministic
evidence check, tamper-evident trail through the API, deletion rules and the
status text (dossier) of every state. Crews are mocked.
"""

import json
import os
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flow_builders import (  # type: ignore[import-not-found]
    APPROVED,
    crew_result,
    make_flow,
    make_papers,
    make_racm,
    make_report,
    make_verified_papers,
    run_phase,
)
from api.job_store import get_flow, remove_flow, set_flow
from swarm import session_manager
from swarm.audit_flow import AuditFlow, InvalidTransitionError
from swarm.evidence import finding_marked_not_tested, unverified_findings
from swarm.review_policy import (
    SegregationOfDutiesError,
    UnverifiedEvidenceError,
    normalise_identity,
    sod_violation,
)
from swarm.schema import QA_PushbackSchema
from swarm.state.machine import AuditStateMachine, AuditStatus
from swarm.state.repository import FlowRepository

AUTH = {"Authorization": "Bearer test-token"}
PREPARER = "Pat Preparer"


def _at_gate(phase: int, prepared_by: str = PREPARER) -> AuditFlow:
    """A flow waiting at gate ``phase`` with that phase's artifact in place."""
    flow = make_flow(min(phase + 1, 3))
    if prepared_by:
        flow.record_preparer(prepared_by)
    if phase == 1:
        flow.state.racm_plan = make_racm()
    if phase >= 2:
        flow.state.working_papers = make_verified_papers()
    if phase == 3:
        flow.state.final_report = make_report()
    flow.machine = AuditStateMachine(AuditStatus(f"WAITING_HUMAN_GATE_{phase}"))
    flow._commit_status()
    return flow


_FEEDBACK_KEY = {1: "qa_feedback", 2: "qa_feedback", 3: "tone_qa_feedback"}
_MAKE = {1: make_racm, 2: make_verified_papers, 3: make_report}


# ── Return for rework ────────────────────────────────────────────────────────


class TestReturnForRework:
    @pytest.mark.parametrize("phase", [1, 2, 3])
    def test_return_reruns_phase_with_notes(self, phase):
        flow = _at_gate(phase)
        flow.return_for_rework(phase, "Rita Reviewer", "Sampling is not justified.")

        assert flow.state.status == f"RUNNING_PHASE_{phase}"
        entry = flow.state.approval_trail[-1]
        assert entry["action"] == "return_for_rework"
        assert entry["human"] == "Rita Reviewer"
        assert entry["notes"] == "Sampling is not justified."
        assert "returned for rework by Rita Reviewer" in (
            flow.state.current_human_dossier
        )

        crew, _ = run_phase(flow, phase, [crew_result(phase, APPROVED, _MAKE[phase]())])
        feedback = crew.kickoff.call_args.kwargs["inputs"][_FEEDBACK_KEY[phase]]
        assert "Sampling is not justified." in feedback
        assert "returned the previous draft for rework" in feedback
        assert flow.state.status == f"WAITING_HUMAN_GATE_{phase}"
        assert flow.verify_trail()["ok"] is True

    def test_notes_survive_auto_retry(self):
        flow = _at_gate(1)
        flow.return_for_rework(1, "Rita", "Map every risk to COSO.")
        rejected = QA_PushbackSchema(approved=False, rejection_reason="Weak ToE")
        crew, _ = run_phase(
            flow,
            1,
            [
                crew_result(1, rejected, make_racm()),
                crew_result(1, APPROVED, make_racm()),
            ],
        )
        retry_feedback = crew.kickoff.call_args_list[1].kwargs["inputs"]["qa_feedback"]
        assert "Map every risk to COSO." in retry_feedback
        assert "Weak ToE" in retry_feedback

    def test_notes_do_not_leak_into_next_phase(self):
        flow = _at_gate(1)
        flow.return_for_rework(1, "Rita", "Redo")
        run_phase(flow, 1, [crew_result(1, APPROVED, make_racm())])
        flow.begin_phase_2("Rita")
        crew, _ = run_phase(flow, 2, [crew_result(2, APPROVED, make_verified_papers())])
        assert crew.kickoff.call_args.kwargs["inputs"]["qa_feedback"] == ""

    def test_blank_notes_rejected(self):
        flow = _at_gate(1)
        with pytest.raises(ValueError):
            flow.return_for_rework(1, "Rita", "   ")
        assert flow.state.status == "WAITING_HUMAN_GATE_1"

    def test_wrong_state_or_phase_raises(self):
        flow = _at_gate(2)
        with pytest.raises(InvalidTransitionError):
            flow.return_for_rework(1, "Rita", "notes")
        flow = make_flow(2)  # RUNNING_PHASE_2
        with pytest.raises(InvalidTransitionError):
            flow.return_for_rework(2, "Rita", "notes")

    def test_preparer_cannot_return(self):
        flow = _at_gate(1)
        with pytest.raises(SegregationOfDutiesError):
            flow.return_for_rework(1, "  pat PREPARER ", "notes")
        assert flow.state.status == "WAITING_HUMAN_GATE_1"
        assert [e["action"] for e in flow.state.approval_trail] == ["audit_created"]


# ── Segregation of duties ────────────────────────────────────────────────────


class TestSegregationOfDuties:
    @pytest.mark.parametrize(
        "phase,method",
        [(1, "begin_phase_2"), (2, "begin_phase_3"), (3, "finalize_audit")],
    )
    def test_preparer_cannot_approve_any_gate(self, phase, method):
        flow = _at_gate(phase)
        with pytest.raises(SegregationOfDutiesError, match="prepared this audit"):
            getattr(flow, method)("pat  preparer")
        assert flow.state.status == f"WAITING_HUMAN_GATE_{phase}"

    def test_preparer_cannot_override_qa(self):
        flow = make_flow(1)
        flow.record_preparer(PREPARER)
        rejected = QA_PushbackSchema(approved=False, rejection_reason="bad")
        run_phase(flow, 1, [crew_result(1, rejected, make_racm())] * 2)
        with pytest.raises(SegregationOfDutiesError):
            flow.override_qa_rejection(1, PREPARER, "accept")
        assert flow.state.status == "QA_REJECTED_PHASE_1"
        flow.override_qa_rejection(1, "Supervisor", "accept")
        assert flow.state.status == "WAITING_HUMAN_GATE_1"

    def test_preparer_may_retry(self):
        flow = AuditFlow(initial_status="ERROR_PHASE_1")
        flow.record_preparer(PREPARER)
        flow.retry_phase(1, PREPARER)
        assert flow.state.status == "RUNNING_PHASE_1"

    def test_gate_3_approver_must_differ_from_gate_2(self):
        flow = _at_gate(2)
        flow.begin_phase_3("Ivan In-Charge")
        run_phase(flow, 3, [crew_result(3, APPROVED, make_report())])
        with pytest.raises(SegregationOfDutiesError, match="Gate 2"):
            flow.finalize_audit(" ivan in-charge ")
        assert flow.state.status == "WAITING_HUMAN_GATE_3"
        flow.finalize_audit("Mona Manager")
        assert flow.state.status == "COMPLETED"

    def test_same_person_may_approve_gates_1_and_2(self):
        flow = _at_gate(1)
        flow.begin_phase_2("Ivan")
        run_phase(flow, 2, [crew_result(2, APPROVED, make_verified_papers())])
        flow.begin_phase_3("Ivan")
        assert flow.state.status == "RUNNING_PHASE_3"

    def test_clearing_prepared_by_does_not_lift_restriction(self):
        flow = _at_gate(1)
        flow.state.prepared_by = ""  # e.g. edited in the stored snapshot
        with pytest.raises(SegregationOfDutiesError):
            flow.begin_phase_2(PREPARER)

    def test_legacy_session_without_preparer(self):
        flow = _at_gate(1, prepared_by="")
        flow.begin_phase_2("anyone")
        assert flow.state.status == "RUNNING_PHASE_2"

    def test_preparer_recorded_once(self):
        flow = AuditFlow()
        flow.record_preparer(PREPARER)
        with pytest.raises(ValueError):
            flow.record_preparer("someone else")
        with pytest.raises(ValueError):
            AuditFlow().record_preparer("  ")

    def test_policy_function(self):
        assert normalise_identity("  Jane\tDOE ") == "jane doe"
        assert normalise_identity(None) == ""
        trail = [
            {"action": "gate_approval", "gate": "Gate 2 (Fieldwork)", "human": "A"}
        ]
        assert sod_violation("gate_approval", "a", "p", trail, gate=3)
        assert sod_violation("gate_approval", "b", "p", trail, gate=3) is None
        assert sod_violation("gate_approval", "a", "p", trail, gate=2) is None
        assert sod_violation("retry", "p", "p", trail) is None
        assert sod_violation("qa_override", "P ", "p", trail)
        # Blank identities never match each other.
        assert sod_violation("gate_approval", "x", "", trail, gate=1) is None


# ── Deterministic evidence check ─────────────────────────────────────────────


def _finding(**kw):
    base = {
        "control_id": "CTRL-X",
        "vault_id_reference": "",
        "exact_quote_from_evidence": "",
    }
    base.update(kw)
    return SimpleNamespace(**base)


class TestEvidenceCheck:
    def test_unverified_quote_rejects_fieldwork_with_control_ids(self):
        flow = make_flow(2)
        papers = make_papers()  # vault id not in the vault
        crew, _ = run_phase(flow, 2, [crew_result(2, APPROVED, papers)] * 2)
        assert crew.kickoff.call_count == 2  # the retry-once path
        assert flow.state.status == "QA_REJECTED_PHASE_2"
        assert "CTRL-01" in flow.state.qa_rejection_reason
        assert "evidence vault" in flow.state.qa_rejection_reason
        assert "deterministic evidence check" in flow.state.current_human_dossier
        retry_inputs = crew.kickoff.call_args_list[1].kwargs["inputs"]
        assert "CTRL-01" in retry_inputs["qa_feedback"]

    def test_evidence_fixed_on_retry_advances(self):
        flow = make_flow(2)
        run_phase(
            flow,
            2,
            [
                crew_result(2, APPROVED, make_papers()),
                crew_result(2, APPROVED, make_verified_papers()),
            ],
        )
        assert flow.state.status == "WAITING_HUMAN_GATE_2"
        assert "verified against the evidence vault" in (
            flow.state.current_human_dossier
        )

    def test_override_then_gate_2_approval(self):
        flow = make_flow(2)
        run_phase(flow, 2, [crew_result(2, APPROVED, make_papers())] * 2)
        flow.override_qa_rejection(2, "Supervisor", "Evidence re-performed manually")
        entry = flow.state.approval_trail[-1]
        assert entry["unverified_controls"] == "CTRL-01"
        assert "unverified evidence for: CTRL-01" in flow.state.current_human_dossier
        flow.begin_phase_3("Reviewer")
        assert flow.state.status == "RUNNING_PHASE_3"

    def test_override_does_not_cover_changed_papers(self):
        flow = make_flow(2)
        run_phase(flow, 2, [crew_result(2, APPROVED, make_papers())] * 2)
        flow.override_qa_rejection(2, "Supervisor", "Accepted")
        papers = make_papers()
        papers.findings[0].test_conclusion = "edited after the override"
        flow.state.working_papers = papers
        with pytest.raises(UnverifiedEvidenceError, match="CTRL-01"):
            flow.begin_phase_3("Reviewer")
        assert flow.state.status == "WAITING_HUMAN_GATE_2"

    def test_gate_2_rechecks_vault(self):
        flow = _at_gate(2)
        vault_dir = os.environ["EVIDENCE_VAULT_PATH"]
        for name in os.listdir(vault_dir):
            os.remove(os.path.join(vault_dir, name))
        with pytest.raises(UnverifiedEvidenceError):
            flow.begin_phase_3("Reviewer")
        assert flow.state.approval_trail[-1]["action"] == "audit_created"

    def test_empty_quote_allowed_only_when_not_tested(self):
        assert unverified_findings([_finding(result="Not tested")]) == []
        assert unverified_findings([_finding(toe_conclusion="not_tested")]) == []
        enum_like = SimpleNamespace(value="Not Tested")
        assert unverified_findings([_finding(result=enum_like)]) == []
        assert unverified_findings([_finding(result="Effective")]) == ["CTRL-X"]
        assert unverified_findings([_finding()]) == ["CTRL-X"]
        assert unverified_findings([SimpleNamespace()]) == ["finding #1"]
        assert finding_marked_not_tested(_finding(result=None)) is False

    def test_not_tested_finding_with_bad_quote_still_flagged(self):
        f = _finding(result="Not tested", exact_quote_from_evidence="made up quote")
        assert unverified_findings([f]) == ["CTRL-X"]

    def test_check_error_fails_closed(self):
        flow = make_flow(2)
        with patch(
            "swarm.audit_flow.unverified_findings", side_effect=RuntimeError("boom")
        ):
            run_phase(flow, 2, [crew_result(2, APPROVED, make_verified_papers())] * 2)
        assert flow.state.status == "QA_REJECTED_PHASE_2"
        assert "could not run" in flow.state.qa_rejection_reason


# ── Status text for every state ──────────────────────────────────────────────


class TestDossiers:
    def test_gate_3_dossier_is_about_the_report(self):
        flow = _at_gate(2)
        flow.state.current_human_dossier = "stale Gate 2 text"
        flow.begin_phase_3("Ivan")
        assert "Fieldwork" in flow.state.current_human_dossier
        run_phase(flow, 3, [crew_result(3, APPROVED, make_report())])
        dossier = flow.state.current_human_dossier
        assert "Reporting complete" in dossier
        assert "Gate 3" in dossier
        assert "Findings" not in dossier

    def test_completed_dossier(self):
        flow = _at_gate(3)
        flow.finalize_audit("Mona")
        assert flow.state.current_human_dossier.startswith("Audit completed")
        assert "Mona" in flow.state.current_human_dossier

    def test_running_and_retry_dossiers(self):
        flow = AuditFlow()
        flow.begin_phase_1()
        assert flow.state.current_human_dossier == "Planning is running."
        flow = AuditFlow(initial_status="ERROR_PHASE_2")
        flow.retry_phase(2, "sup")
        assert "retry requested by sup" in flow.state.current_human_dossier

    def test_gate_approval_records_artifact_digest(self):
        flow = _at_gate(1)
        flow.begin_phase_2("Rita")
        entry = flow.state.approval_trail[-1]
        assert entry["artifact"] == "racm_plan"
        assert len(entry["artifact_digest"]) == 64
        assert flow.verify_trail()["ok"] is True
        flow.state.racm_plan = make_racm().model_copy(update={"theme": "edited"})
        result = flow.verify_trail()
        assert result["status"] == "artifact_changed"
        assert result["changed_since_approval"] == ["Gate 1 (Planning)"]

    def test_gate_without_artifact_is_refused(self):
        from swarm.audit_flow import PhaseArtifactMissingError

        flow = AuditFlow(initial_status="WAITING_HUMAN_GATE_3")
        with pytest.raises(PhaseArtifactMissingError):
            flow.finalize_audit("Mona")
        assert flow.state.status == "WAITING_HUMAN_GATE_3"


# ── API ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("API_AUTH_TOKEN", "test-token")
    monkeypatch.delenv("TRAIL_ANCHORS_PATH", raising=False)
    monkeypatch.setattr(
        session_manager, "SESSIONS_PATH", str(tmp_path / "audit_sessions.json")
    )
    from api.main import app

    return TestClient(app)


@pytest.fixture
def executor():
    ex = MagicMock()
    with patch("api.routers.sessions.get_executor", return_value=ex):
        yield ex


def _store(flow: AuditFlow, *, cache: bool = True) -> str:
    sid = f"sess-{uuid.uuid4()}"
    session_manager.save_session(sid, "S3 audit", flow.state.business_context)
    FlowRepository().save(sid, flow)
    if cache:
        set_flow(sid, flow)
    else:
        remove_flow(sid)
    return sid


def _approve(client, sid, gate, human):
    return client.patch(
        f"/api/sessions/{sid}/approve",
        headers=AUTH,
        json={"gate_number": gate, "human_id": human},
    )


def _return(client, sid, **kw):
    body = {"phase": 1, "human_id": "Rita", "notes": "Add sampling rationale."}
    body.update(kw)
    return client.post(f"/api/sessions/{sid}/return", headers=AUTH, json=body)


class TestCreateApi:
    def _create(self, client, **kw):
        body = {"theme": "S3", "business_context": "ctx"}
        body.update(kw)
        return client.post("/api/sessions", headers=AUTH, json=body)

    def test_prepared_by_required(self, client, executor):
        assert self._create(client).status_code == 422
        assert self._create(client, prepared_by="").status_code == 422
        assert self._create(client, prepared_by="   ").status_code == 422
        executor.submit.assert_not_called()

    def test_prepared_by_recorded(self, client, executor):
        r = self._create(client, prepared_by="  Pat Preparer ")
        assert r.status_code == 201
        sid = r.json()["session_id"]
        assert r.json()["prepared_by"] == "Pat Preparer"
        saved = session_manager.get_session(sid)
        assert saved["prepared_by"] == "Pat Preparer"
        assert saved["state_snapshot"]["prepared_by"] == "Pat Preparer"
        first = saved["state_snapshot"]["approval_trail"][0]
        assert first["action"] == "audit_created"
        assert first["human"] == "Pat Preparer"
        listed = client.get("/api/sessions", headers=AUTH).json()
        assert listed[0]["prepared_by"] == "Pat Preparer"

    def test_with_document_requires_prepared_by(self, client, executor):
        r = client.post(
            "/api/sessions/with-document",
            headers=AUTH,
            data={"theme": "IAM", "prepared_by": "  "},
            files={"document": ("scope.txt", b"IAM users", "text/plain")},
        )
        assert r.status_code == 422


class TestReturnApi:
    def test_return_submits_rerun_and_persists(self, client, executor):
        sid = _store(_at_gate(1))
        r = _return(client, sid)
        assert r.status_code == 200
        assert r.json()["status"] == "RUNNING_PHASE_1"
        from api.routers.sessions import _run_phase_1

        assert executor.submit.call_args.args[1] is _run_phase_1
        entry = session_manager.get_session(sid)["state_snapshot"]["approval_trail"][-1]
        assert entry["action"] == "return_for_rework"
        assert entry["notes"] == "Add sampling rationale."

    def test_wrong_state_is_409(self, client, executor):
        sid = _store(_at_gate(2))
        assert _return(client, sid, phase=1).status_code == 409
        assert _return(client, sid, phase=2).status_code == 200
        assert _return(client, sid, phase=2).status_code == 409  # now running
        assert executor.submit.call_count == 1

    def test_blank_notes_is_422(self, client, executor):
        sid = _store(_at_gate(1))
        assert _return(client, sid, notes="").status_code == 422
        assert _return(client, sid, notes="   ").status_code == 422
        executor.submit.assert_not_called()

    def test_preparer_is_409(self, client, executor):
        sid = _store(_at_gate(1))
        r = _return(client, sid, human_id="PAT preparer")
        assert r.status_code == 409
        assert "Segregation of duties" in r.json()["detail"]
        executor.submit.assert_not_called()

    def test_missing_session_is_404(self, client, executor):
        assert _return(client, "nope").status_code == 404


class TestApproveApiPolicy:
    def test_preparer_approval_is_409(self, client, executor):
        sid = _store(_at_gate(1))
        r = _approve(client, sid, 1, PREPARER.upper())
        assert r.status_code == 409
        assert "prepared this audit" in r.json()["detail"]
        executor.submit.assert_not_called()

    def test_same_approver_gates_2_and_3_is_409(self, client, executor):
        flow = _at_gate(2)
        sid = _store(flow)
        assert _approve(client, sid, 2, "Ivan").status_code == 200
        run_phase(flow, 3, [crew_result(3, APPROVED, make_report())])
        r = _approve(client, sid, 3, "ivan")
        assert r.status_code == 409
        assert "Gate 2" in r.json()["detail"]
        assert _approve(client, sid, 3, "Mona").status_code == 200

    def test_gate_2_unverified_evidence_is_409(self, client, executor):
        flow = _at_gate(2)
        flow.state.working_papers = make_papers()
        sid = _store(flow)
        r = _approve(client, sid, 2, "Ivan")
        assert r.status_code == 409
        assert "CTRL-01" in r.json()["detail"]
        executor.submit.assert_not_called()

    def test_preparer_override_is_409(self, client, executor):
        flow = make_flow(1)
        flow.record_preparer(PREPARER)
        rejected = QA_PushbackSchema(approved=False, rejection_reason="bad")
        run_phase(flow, 1, [crew_result(1, rejected, make_racm())] * 2)
        sid = _store(flow)
        r = client.post(
            f"/api/sessions/{sid}/qa-override",
            headers=AUTH,
            json={"phase": 1, "human_id": PREPARER, "reason": "fine"},
        )
        assert r.status_code == 409

    def test_gate_1_approval_persisted_before_phase_runs(self, client, executor):
        sid = _store(_at_gate(1))
        assert _approve(client, sid, 1, "Rita").status_code == 200
        trail = session_manager.get_session(sid)["state_snapshot"]["approval_trail"]
        assert trail[-1]["gate"] == "Gate 1 (Planning)"


class TestDeleteApi:
    def test_unapproved_draft_can_be_deleted(self, client, executor):
        sid = _store(_at_gate(1))
        assert client.delete(f"/api/sessions/{sid}", headers=AUTH).status_code == 204
        assert session_manager.get_session(sid) is None

    @pytest.mark.parametrize("cache", [True, False])
    def test_approved_audit_cannot_be_deleted(self, client, executor, cache):
        flow = _at_gate(1)
        sid = _store(flow)
        assert _approve(client, sid, 1, "Rita").status_code == 200
        if not cache:
            remove_flow(sid)
        r = client.delete(f"/api/sessions/{sid}", headers=AUTH)
        assert r.status_code == 409
        assert session_manager.get_session(sid) is not None

    def test_completed_audit_cannot_be_deleted(self, client, executor):
        sid = _store(_at_gate(3), cache=False)
        assert _approve(client, sid, 3, "Mona").status_code == 200
        remove_flow(sid)
        assert client.delete(f"/api/sessions/{sid}", headers=AUTH).status_code == 409

    def test_legacy_status_past_planning_cannot_be_deleted(self, client, executor):
        sid = "legacy-1"
        session_manager.save_session(
            sid,
            "old",
            "ctx",
            status="QA_REJECTED_PHASE_2",
            state_snapshot={"status": "QA_REJECTED_PHASE_2", "approval_trail": []},
        )
        assert client.delete(f"/api/sessions/{sid}", headers=AUTH).status_code == 409


class TestTrailVerifyApi:
    def _completed(self, client) -> str:
        flow = _at_gate(1)
        sid = _store(flow)
        assert _approve(client, sid, 1, "Rita").status_code == 200
        return sid

    def _verify(self, client, sid):
        r = client.get(f"/api/sessions/{sid}/trail/verify", headers=AUTH)
        assert r.status_code == 200
        return r.json()

    def test_verify_ok_and_in_detail(self, client, executor):
        sid = self._completed(client)
        result = self._verify(client, sid)
        assert result["ok"] is True
        assert result["anchored"] is True
        assert result["entries"] == 2
        detail = client.get(f"/api/sessions/{sid}", headers=AUTH).json()
        assert detail["trail_verification"]["ok"] is True
        assert detail["prepared_by"] == PREPARER

    def _tamper(self, sid, fn):
        path = session_manager.SESSIONS_PATH
        with open(path) as f:
            data = json.load(f)
        fn(data[sid]["state_snapshot"])
        with open(path, "w") as f:
            json.dump(data, f)
        remove_flow(sid)

    def test_edited_approver_on_disk_detected(self, client, executor):
        sid = self._completed(client)

        def edit(snap):
            snap["approval_trail"][1]["human"] = "Someone Else"

        self._tamper(sid, edit)
        result = self._verify(client, sid)
        assert result["status"] == "broken"
        assert result["first_broken_index"] == 1
        detail = client.get(f"/api/sessions/{sid}", headers=AUTH).json()
        assert detail["trail_verification"]["ok"] is False

    def test_truncated_trail_on_disk_detected_by_anchor(self, client, executor):
        sid = self._completed(client)
        self._tamper(sid, lambda snap: snap["approval_trail"].pop())
        assert self._verify(client, sid)["status"] == "truncated"

    def test_edited_approved_artifact_detected(self, client, executor):
        sid = self._completed(client)

        def edit(snap):
            snap["racm_plan"]["theme"] = "quietly changed"

        self._tamper(sid, edit)
        result = self._verify(client, sid)
        assert result["status"] == "artifact_changed"

    def test_legacy_session_reports_unchained(self, client, executor):
        sid = "legacy-2"
        session_manager.save_session(
            sid,
            "old",
            "ctx",
            status="WAITING_HUMAN_GATE_1",
            state_snapshot={
                "status": "WAITING_HUMAN_GATE_1",
                "approval_trail": [
                    {"gate": "Retry (Planning)", "human": "x", "action": "retry"}
                ],
            },
        )
        result = self._verify(client, sid)
        assert result["status"] == "legacy_unchained"
        detail = client.get(f"/api/sessions/{sid}", headers=AUTH).json()
        assert detail["prepared_by"] == ""
        assert detail["trail_verification"]["status"] == "legacy_unchained"

    def test_verify_missing_session_is_404(self, client):
        r = client.get("/api/sessions/nope/trail/verify", headers=AUTH)
        assert r.status_code == 404

    def test_verify_without_snapshot_uses_memory(self, client, executor):
        sid = f"sess-{uuid.uuid4()}"
        session_manager.save_session(sid, "n", "ctx")
        assert self._verify(client, sid)["status"] == "ok"
        flow = _at_gate(1)
        set_flow(sid, flow)
        assert self._verify(client, sid)["entries"] == 1
        assert get_flow(sid) is flow

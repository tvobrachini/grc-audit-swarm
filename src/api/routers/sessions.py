import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from api.executor import get_executor
from api.job_store import (
    get_flow,
    push_event,
    remove_flow,
    session_lock,
    set_flow,
    set_job,
)
from api.models import (
    DEFAULT_FRAMEWORKS,
    ApproveGateRequest,
    CreateSessionRequest,
    QAOverrideRequest,
    RetryPhaseRequest,
    ReturnForReworkRequest,
    SessionDetail,
    SessionSummary,
    TrailVerification,
    _needs_input,
    _phase_from_status,
)
from api.scope_document import (
    MAX_UPLOAD_BYTES,
    ScopeDocumentError,
    extract_scope_document,
    merge_business_context,
)
from swarm.audit_flow import (
    AuditFlow,
    InvalidTransitionError,
    PhaseArtifactMissingError,
    ReviewBlockedError,
)
from swarm.session_manager import (
    delete_session,
    get_session,
    get_trail_anchor,
    list_sessions,
    save_session,
)
from swarm.trail import verify_trail
from swarm.state.repository import FlowRepository

router = APIRouter()
_repo = FlowRepository()


def _make_event_callback(session_id: str):
    """Returns a step_callback for CrewAI that pushes agent_step events to SSE queue."""

    def callback(step_output) -> None:
        try:
            agent = getattr(step_output, "agent", "") or ""
            if hasattr(step_output, "output"):
                preview = str(step_output.output)[:300]
                push_event(
                    session_id,
                    {"type": "agent_step", "agent": agent, "preview": preview},
                )
            elif hasattr(step_output, "tool"):
                push_event(
                    session_id,
                    {
                        "type": "agent_log",
                        "agent": agent,
                        "raw": str(step_output)[:300],
                    },
                )
        except Exception:  # nosec B110 — event callback must never crash the crew thread
            pass

    return callback


def _get_or_load_flow(session_id: str) -> AuditFlow | None:
    """Return in-memory flow, or load and cache from disk on miss."""
    flow = get_flow(session_id)
    if flow:
        return flow
    result = _repo.load(session_id)
    if result is None:
        return None
    if not result.is_clean:
        import logging

        logging.getLogger(__name__).warning(
            "Session %s loaded with schema mismatches: %s",
            session_id,
            result.skipped_fields,
        )
    set_flow(session_id, result.flow)
    return result.flow


def _artifact_dict(artifact) -> dict[str, Any] | None:
    """Serialize a typed Pydantic artifact to dict for the API response."""
    if artifact is None:
        return None
    if hasattr(artifact, "model_dump"):
        return artifact.model_dump()
    return artifact  # already a dict (snapshot path)


def _build_summary(session_id: str, data: dict[str, Any]) -> SessionSummary:
    flow = get_flow(session_id)
    status = flow.state.status if flow else data.get("status", "WAITING_FOR_SCOPE")
    return SessionSummary(
        session_id=session_id,
        name=data.get("name", session_id),
        status=status,
        phase=_phase_from_status(status),
        needs_input=_needs_input(status),
        created_at=data.get("created_at", ""),
        prepared_by=_prepared_by(data, flow),
    )


def _prepared_by(data: dict[str, Any], flow: Optional[AuditFlow]) -> str:
    """Preparer from the flow, the snapshot, or the session metadata ("" if legacy)."""
    if flow is not None and flow.state.prepared_by:
        return flow.state.prepared_by
    snapshot = data.get("state_snapshot") or {}
    return str(snapshot.get("prepared_by") or data.get("prepared_by") or "")


def _snapshot_verification(
    session_id: str, snapshot: dict[str, Any]
) -> TrailVerification:
    return TrailVerification(
        **verify_trail(
            snapshot.get("approval_trail") or [],
            anchor=get_trail_anchor(session_id),
            artifacts={
                f: snapshot.get(f)
                for f in ("racm_plan", "working_papers", "final_report")
            },
        )
    )


def _build_detail(session_id: str, data: dict[str, Any]) -> SessionDetail:
    flow = get_flow(session_id)
    if flow:
        s = flow.state
        status = s.status
        return SessionDetail(
            session_id=session_id,
            name=data.get("name", session_id),
            status=status,
            phase=_phase_from_status(status),
            needs_input=_needs_input(status),
            created_at=data.get("created_at", ""),
            theme=s.theme,
            business_context=s.business_context,
            frameworks=s.frameworks,
            current_human_dossier=s.current_human_dossier,
            racm_plan=_artifact_dict(s.racm_plan),
            working_papers=_artifact_dict(s.working_papers),
            final_report=_artifact_dict(s.final_report),
            approval_trail=s.approval_trail,
            qa_rejection_reason=s.qa_rejection_reason,
            prepared_by=_prepared_by(data, flow),
            trail_verification=TrailVerification(
                **flow.verify_trail(anchor=get_trail_anchor(session_id))
            ),
        )
    # flow not in memory — return stored snapshot
    snapshot = data.get("state_snapshot", {})
    status = snapshot.get("status", "WAITING_FOR_SCOPE")
    return SessionDetail(
        session_id=session_id,
        name=data.get("name", session_id),
        status=status,
        phase=_phase_from_status(status),
        needs_input=_needs_input(status),
        created_at=data.get("created_at", ""),
        theme=snapshot.get("theme", ""),
        business_context=snapshot.get("business_context", ""),
        frameworks=snapshot.get("frameworks", []),
        current_human_dossier=snapshot.get("current_human_dossier", ""),
        racm_plan=snapshot.get("racm_plan"),
        working_papers=snapshot.get("working_papers"),
        final_report=snapshot.get("final_report"),
        approval_trail=snapshot.get("approval_trail", []),
        qa_rejection_reason=snapshot.get("qa_rejection_reason"),
        prepared_by=_prepared_by(data, None),
        trail_verification=_snapshot_verification(session_id, snapshot),
    )


def _run_phase_1(session_id: str, job_id: str) -> None:
    flow = _get_or_load_flow(session_id)
    if not flow:
        set_job(job_id, "failed", "flow not found")
        return
    try:
        push_event(session_id, {"type": "status", "status": "RUNNING_PHASE_1"})
        flow.generate_planning(event_callback=_make_event_callback(session_id))
        push_event(session_id, {"type": "status", "status": flow.state.status})
        push_event(
            session_id,
            {"type": "complete", "status": flow.state.status, "artifact": "racm_plan"},
        )
        _repo.save(session_id, flow)
        set_job(job_id, "completed")
    except Exception as exc:
        push_event(session_id, {"type": "error", "reason": str(exc)})
        set_job(job_id, "failed", str(exc))


def _run_phase_2(session_id: str, job_id: str) -> None:
    flow = _get_or_load_flow(session_id)
    if not flow:
        set_job(job_id, "failed", "flow not found")
        return
    try:
        push_event(session_id, {"type": "status", "status": "RUNNING_PHASE_2"})
        flow.generate_fieldwork(event_callback=_make_event_callback(session_id))
        push_event(session_id, {"type": "status", "status": flow.state.status})
        push_event(
            session_id,
            {
                "type": "complete",
                "status": flow.state.status,
                "artifact": "working_papers",
            },
        )
        _repo.save(session_id, flow)
        set_job(job_id, "completed")
    except Exception as exc:
        push_event(session_id, {"type": "error", "reason": str(exc)})
        set_job(job_id, "failed", str(exc))


def _run_phase_3(session_id: str, job_id: str) -> None:
    flow = _get_or_load_flow(session_id)
    if not flow:
        set_job(job_id, "failed", "flow not found")
        return
    try:
        push_event(session_id, {"type": "status", "status": "RUNNING_PHASE_3"})
        flow.generate_reporting(event_callback=_make_event_callback(session_id))
        push_event(session_id, {"type": "status", "status": flow.state.status})
        push_event(
            session_id,
            {
                "type": "complete",
                "status": flow.state.status,
                "artifact": "final_report",
            },
        )
        _repo.save(session_id, flow)
        set_job(job_id, "completed")
    except Exception as exc:
        push_event(session_id, {"type": "error", "reason": str(exc)})
        set_job(job_id, "failed", str(exc))


@router.post("", response_model=SessionSummary, status_code=201)
def create_session(req: CreateSessionRequest) -> SessionSummary:
    return _create_and_launch(
        req.theme, req.business_context, req.frameworks, req.name, req.prepared_by
    )


@router.post("/with-document", response_model=SessionSummary, status_code=201)
def create_session_with_document(
    theme: str = Form(..., min_length=1),
    business_context: str = Form(""),
    frameworks: list[str] = Form(default_factory=lambda: list(DEFAULT_FRAMEWORKS)),
    name: Optional[str] = Form(None),
    prepared_by: str = Form(..., min_length=1, max_length=200),
    document: UploadFile = File(...),
) -> SessionSummary:
    """Create an audit with a scope document (PDF, .txt or .md, ≤ 5 MB).

    The extracted text is appended to the business context inside delimiters
    that label it as untrusted, user-supplied document content.
    """
    if not prepared_by.strip():
        raise HTTPException(status_code=422, detail="prepared_by must not be blank")
    data = document.file.read(MAX_UPLOAD_BYTES + 1)
    try:
        doc = extract_scope_document(document.filename, data)
    except ScopeDocumentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return _create_and_launch(
        theme,
        merge_business_context(business_context, doc),
        [f for f in frameworks if f.strip()],
        name or None,
        prepared_by,
    )


def _create_and_launch(
    theme: str,
    business_context: str,
    frameworks: list[str],
    name: Optional[str],
    prepared_by: str,
) -> SessionSummary:
    session_id = str(uuid.uuid4())
    name = name or f"{theme[:40]} audit"
    created_at = datetime.utcnow().isoformat(timespec="seconds")

    flow = AuditFlow()
    flow.state.theme = theme
    flow.state.business_context = business_context
    flow.state.frameworks = frameworks
    try:
        flow.record_preparer(prepared_by)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Stamp RUNNING_PHASE_1 synchronously so polls see correct state immediately
    flow.begin_phase_1()
    set_flow(session_id, flow)

    save_session(
        thread_id=session_id,
        name=name,
        scope_text=business_context,
        status=flow.state.status,
        created_at=created_at,
        prepared_by=flow.state.prepared_by,
    )
    # Persist the snapshot (and the trail's first, chained entry) right away.
    _repo.save(session_id, flow)

    job_id = str(uuid.uuid4())
    set_job(job_id, "running")
    get_executor().submit(session_id, _run_phase_1, session_id, job_id)

    return SessionSummary(
        session_id=session_id,
        name=name,
        status="RUNNING_PHASE_1",
        phase=1,
        needs_input=False,
        created_at=created_at,
        prepared_by=flow.state.prepared_by,
    )


@router.get("", response_model=list[SessionSummary])
def list_all_sessions() -> list[SessionSummary]:
    sessions = list_sessions()
    return [_build_summary(sid, data) for sid, data in sessions.items()]


@router.get("/{session_id}", response_model=SessionDetail)
def get_session_detail(session_id: str) -> SessionDetail:
    data = get_session(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found")
    return _build_detail(session_id, data)


def _has_sign_off(session_id: str, data: dict[str, Any]) -> bool:
    """True once any gate was approved or the audit completed.

    Checks the in-memory flow and the persisted snapshot, by trail and by
    status (every status past Planning implies Gate 1 was approved).
    """
    states: list[tuple[str, list[dict[str, str]]]] = []
    flow = get_flow(session_id)
    if flow is not None:
        states.append((flow.state.status, flow.state.approval_trail))
    snapshot = data.get("state_snapshot") or {}
    states.append(
        (
            str(snapshot.get("status") or data.get("status") or ""),
            snapshot.get("approval_trail") or [],
        )
    )
    for status, trail in states:
        if status == "COMPLETED" or _phase_from_status(status) >= 2:
            return True
        if any(e.get("action") == "gate_approval" for e in trail):
            return True
    return False


@router.delete("/{session_id}", status_code=204)
def remove_session(session_id: str) -> None:
    """Delete an unapproved draft audit.

    409 once any gate has been approved or the audit completed: signed-off
    work and its approval trail are retained.
    """
    # Serialise with approve/retry/override so an action racing a delete
    # cannot re-cache the flow after it was removed.
    with session_lock(session_id):
        data = get_session(session_id)
        if data and _has_sign_off(session_id, data):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cannot delete this audit: a gate has been approved or the "
                    "audit is completed, so its work and approval trail are kept."
                ),
            )
        delete_session(session_id)
        remove_flow(session_id)


_PHASE_RUNNERS = {1: _run_phase_1, 2: _run_phase_2, 3: _run_phase_3}


def _require_session(session_id: str) -> dict[str, Any]:
    data = get_session(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found")
    return data


def _require_flow(session_id: str) -> AuditFlow:
    flow = _get_or_load_flow(session_id)
    if not flow:
        raise HTTPException(status_code=404, detail="flow not found")
    return flow


def _conflict(action: str, flow: AuditFlow, exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=f"Cannot {action} (status={flow.state.status}): {exc}",
    )


def _submit_phase(session_id: str, phase: int) -> str:
    job_id = str(uuid.uuid4())
    set_job(job_id, "running")
    get_executor().submit(session_id, _PHASE_RUNNERS[phase], session_id, job_id)
    return job_id


def _summary(
    session_id: str, data: dict[str, Any], status: str, needs_input: bool = False
) -> SessionSummary:
    return SessionSummary(
        session_id=session_id,
        name=data.get("name", session_id),
        status=status,
        phase=_phase_from_status(status),
        needs_input=needs_input,
        created_at=data.get("created_at", ""),
        prepared_by=_prepared_by(data, get_flow(session_id)),
    )


def _blocked(action: str, exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=f"Cannot {action}: {exc}")


@router.patch("/{session_id}/approve", response_model=SessionSummary)
def approve_gate(session_id: str, req: ApproveGateRequest) -> SessionSummary:
    """Approve human gate 1, 2 or 3.

    Gates 1/2 start the next phase crew; gate 3 completes the audit. Returns
    409 when the session is not waiting at that gate (e.g. a double click),
    in which case no crew is started.
    """
    if req.gate_number not in (1, 2, 3):
        raise HTTPException(status_code=400, detail="gate_number must be 1, 2, or 3")
    data = _require_session(session_id)

    # check-transition-submit is atomic per session: a concurrent duplicate
    # request blocks here, then sees the new status and gets a 409.
    with session_lock(session_id):
        flow = _require_flow(session_id)
        approve = {
            1: flow.begin_phase_2,
            2: flow.begin_phase_3,
            3: flow.finalize_audit,
        }[req.gate_number]
        try:
            approve(req.human_id)
        except InvalidTransitionError as exc:
            raise _conflict(f"approve gate {req.gate_number}", flow, exc) from exc
        except (ReviewBlockedError, PhaseArtifactMissingError) as exc:
            raise _blocked(f"approve gate {req.gate_number}", exc) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        # Persist the sign-off before any crew runs.
        _repo.save(session_id, flow)
        if req.gate_number == 3:
            next_status = flow.state.status
        else:
            _submit_phase(session_id, req.gate_number + 1)
            next_status = f"RUNNING_PHASE_{req.gate_number + 1}"

    return _summary(session_id, data, next_status)


@router.post("/{session_id}/retry", response_model=SessionSummary)
def retry_phase(session_id: str, req: RetryPhaseRequest) -> SessionSummary:
    """Re-run a phase that ended QA_REJECTED_PHASE_n or ERROR_PHASE_n.

    The retry is stamped in the approval trail. 409 if the phase is not in a
    retryable state.
    """
    data = _require_session(session_id)
    with session_lock(session_id):
        flow = _require_flow(session_id)
        try:
            flow.retry_phase(req.phase, req.human_id)
        except InvalidTransitionError as exc:
            raise _conflict(f"retry phase {req.phase}", flow, exc) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _repo.save(session_id, flow)
        _submit_phase(session_id, req.phase)
        next_status = flow.state.status

    return _summary(session_id, data, next_status)


@router.post("/{session_id}/qa-override", response_model=SessionSummary)
def override_qa_rejection(session_id: str, req: QAOverrideRequest) -> SessionSummary:
    """Supervisor override: accept a QA-rejected artifact with a justification.

    Moves QA_REJECTED_PHASE_n → WAITING_HUMAN_GATE_n (the normal gate approval
    still follows) and records approver + reason in the approval trail.
    409 if the phase is not QA-rejected or produced no artifact.
    """
    data = _require_session(session_id)
    with session_lock(session_id):
        flow = _require_flow(session_id)
        try:
            flow.override_qa_rejection(req.phase, req.human_id, req.reason)
        except (InvalidTransitionError, PhaseArtifactMissingError) as exc:
            raise _conflict(
                f"override QA rejection for phase {req.phase}", flow, exc
            ) from exc
        except ReviewBlockedError as exc:
            raise _blocked(f"override QA rejection for phase {req.phase}", exc) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _repo.save(session_id, flow)
        next_status = flow.state.status

    return _summary(session_id, data, next_status, needs_input=True)


@router.post("/{session_id}/return", response_model=SessionSummary)
def return_for_rework(session_id: str, req: ReturnForReworkRequest) -> SessionSummary:
    """Reviewer returns a phase waiting at its gate for rework.

    WAITING_HUMAN_GATE_n → RUNNING_PHASE_n: the phase crew re-runs with the
    review notes as feedback, and the return (reviewer, notes) is recorded in
    the approval trail. 409 if the session is not waiting at that gate or the
    reviewer is the preparer; 422 for blank notes.
    """
    data = _require_session(session_id)
    with session_lock(session_id):
        flow = _require_flow(session_id)
        try:
            flow.return_for_rework(req.phase, req.human_id, req.notes)
        except InvalidTransitionError as exc:
            raise _conflict(f"return phase {req.phase}", flow, exc) from exc
        except ReviewBlockedError as exc:
            raise _blocked(f"return phase {req.phase}", exc) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _repo.save(session_id, flow)
        _submit_phase(session_id, req.phase)
        next_status = flow.state.status

    return _summary(session_id, data, next_status)


@router.get("/{session_id}/trail/verify", response_model=TrailVerification)
def verify_session_trail(session_id: str) -> TrailVerification:
    """Recompute the persisted approval trail's hash chain.

    Verifies the trail as stored on disk (the record), against the separately
    stored anchor; falls back to the in-memory flow if nothing is persisted.
    """
    data = _require_session(session_id)
    snapshot = data.get("state_snapshot") or {}
    if snapshot:
        return _snapshot_verification(session_id, snapshot)
    flow = get_flow(session_id)
    if flow is None:
        return TrailVerification(**verify_trail([], get_trail_anchor(session_id)))
    return TrailVerification(**flow.verify_trail(anchor=get_trail_anchor(session_id)))

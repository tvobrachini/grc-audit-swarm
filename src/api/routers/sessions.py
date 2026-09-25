import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException

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
    ApproveGateRequest,
    CreateSessionRequest,
    QAOverrideRequest,
    RetryPhaseRequest,
    SessionDetail,
    SessionSummary,
    _needs_input,
    _phase_from_status,
)
from swarm.audit_flow import (
    AuditFlow,
    InvalidTransitionError,
    PhaseArtifactMissingError,
)
from swarm.session_manager import (
    delete_session,
    get_session,
    list_sessions,
    save_session,
)
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
    session_id = str(uuid.uuid4())
    name = req.name or f"{req.theme[:40]} audit"
    created_at = datetime.utcnow().isoformat(timespec="seconds")

    flow = AuditFlow()
    flow.state.theme = req.theme
    flow.state.business_context = req.business_context
    flow.state.frameworks = req.frameworks

    # Stamp RUNNING_PHASE_1 synchronously so polls see correct state immediately
    flow.begin_phase_1()
    set_flow(session_id, flow)

    save_session(
        thread_id=session_id,
        name=name,
        scope_text=req.business_context,
        status=flow.state.status,
        created_at=created_at,
    )

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


@router.delete("/{session_id}", status_code=204)
def remove_session(session_id: str) -> None:
    # Serialise with approve/retry/override so an action racing a delete
    # cannot re-cache the flow after it was removed.
    with session_lock(session_id):
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
    )


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
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        if req.gate_number == 3:
            # No further crew phase — persist the sign-off synchronously.
            _repo.save(session_id, flow)
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
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _repo.save(session_id, flow)
        next_status = flow.state.status

    return _summary(session_id, data, next_status, needs_input=True)

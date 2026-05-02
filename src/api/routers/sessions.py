import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from api.executor import get_executor
from api.job_store import get_flow, push_event, remove_flow, set_flow, set_job
from api.models import (
    ApproveGateRequest,
    CreateSessionRequest,
    SessionDetail,
    SessionSummary,
    _needs_input,
    _phase_from_status,
)
from swarm.audit_flow import AuditFlow
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

    save_session(thread_id=session_id, name=name, scope_text=req.business_context)
    from swarm.session_manager import update_session

    update_session(session_id, status="RUNNING_PHASE_1", created_at=created_at)

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
    delete_session(session_id)
    remove_flow(session_id)


@router.patch("/{session_id}/approve", response_model=SessionSummary)
def approve_gate(session_id: str, req: ApproveGateRequest) -> SessionSummary:
    data = get_session(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found")

    job_id = str(uuid.uuid4())
    set_job(job_id, "running")

    flow = _get_or_load_flow(session_id)

    if req.gate_number == 1:
        # Stamp trail + transition machine synchronously before thread starts
        if flow:
            flow.begin_phase_2(req.human_id)
        get_executor().submit(session_id, _run_phase_2, session_id, job_id)
        next_status = "RUNNING_PHASE_2"
    elif req.gate_number == 2:
        if flow:
            flow.begin_phase_3(req.human_id)
        get_executor().submit(session_id, _run_phase_3, session_id, job_id)
        next_status = "RUNNING_PHASE_3"
    else:
        raise HTTPException(status_code=400, detail="gate_number must be 1 or 2")

    return SessionSummary(
        session_id=session_id,
        name=data.get("name", session_id),
        status=next_status,
        phase=req.gate_number + 1,
        needs_input=False,
        created_at=data.get("created_at", ""),
    )

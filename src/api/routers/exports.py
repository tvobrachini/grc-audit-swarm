"""Artifact downloads: /api/sessions/{id}/export/…

404 until the artifact exists. Served same-origin under /api so the nginx
proxy adds the API token (the browser never holds it).

Every export first verifies the approval trail (hash chain, anchor, approved
artifacts and reviewer decisions; see :mod:`swarm.trail`). A trail that
shows a change (``broken``, ``truncated``, ``anchor_mismatch``,
``artifact_changed``, ``decision_changed``) gets 409 instead of a
clean-looking export. Other statuses (``ok``, ``legacy_unchained``,
``unkeyed``, ``key_unavailable``) export, and the status is written into the
report (a line under "Approval Trail") and the OSCAL document (a
``trail-verification-status`` prop on each result).
"""

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from api.exports import (
    XLSX_MEDIA_TYPE,
    ExportContext,
    export_filename,
    oscal_json,
    racm_xlsx,
    report_markdown,
    sanitize_report,
    working_papers_xlsx,
)
from api.job_store import get_flow
from api.routers.sessions import _repo, _require_session
from swarm.audit_flow import AuditFlow
from swarm.oscal_ar import PROJECT_NS
from swarm.session_manager import get_trail_anchor
from swarm.trail import TAMPER_STATUSES

router = APIRouter()


def _verified_trail(session_id: str, flow: AuditFlow) -> dict[str, Any]:
    """Verify the exported flow's trail; 409 if it shows a change."""
    verification = flow.verify_trail(anchor=get_trail_anchor(session_id))
    if verification["status"] in TAMPER_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                "Export refused: approval trail verification failed "
                f"({verification['status']}). {verification['detail']}"
            ),
        )
    return verification


def _one_line(text: Any) -> str:
    return " ".join(str(text).split())


def _with_trail_line(markdown: str, verification: dict[str, Any]) -> str:
    """Insert the verification line right under the "## Approval Trail" heading."""
    heading = "\n## Approval Trail\n\n"
    line = sanitize_report(
        _one_line(
            f"> Trail verification at export: {verification['status']} "
            f"({verification['detail']})"
        )
    )
    if heading in markdown:
        head, tail = markdown.rsplit(heading, 1)
        return f"{head}{heading}{line}\n\n{tail}"
    return f"{markdown.rstrip()}\n\n## Approval Trail\n\n{line}\n"


def _with_trail_prop(body: bytes, verification: dict[str, Any]) -> bytes:
    """Add a ``trail-verification-status`` prop to each OSCAL result."""
    document = json.loads(body)
    for result in document.get("assessment-results", {}).get("results", []):
        result.setdefault("props", []).append(
            {
                "name": "trail-verification-status",
                "ns": PROJECT_NS,
                "value": verification["status"],
                "remarks": _one_line(verification["detail"]) or verification["status"],
            }
        )
    return json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8")


def _load(session_id: str) -> tuple[AuditFlow, ExportContext, dict[str, Any]]:
    data = _require_session(session_id)
    # Read-only: use the live flow if cached, else load from disk *without*
    # caching it (a download must not re-cache a session being deleted).
    flow = get_flow(session_id)
    if flow is None:
        loaded = _repo.load(session_id)
        flow = loaded.flow if loaded else None
    if flow is None:
        raise HTTPException(status_code=404, detail="No artifacts for this session")
    ctx = ExportContext(
        session_id=session_id,
        session_name=str(data.get("name", session_id)),
        status=flow.state.status,
    )
    return flow, ctx, _verified_trail(session_id, flow)


def _missing(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{what} is not available yet")


def _download(body: bytes, media_type: str, filename: str) -> Response:
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{session_id}/export/racm.xlsx")
def export_racm(session_id: str) -> Response:
    flow, ctx, verification = _load(session_id)
    racm = flow.state.racm_plan
    if racm is None:
        raise _missing("RACM")
    return _download(
        racm_xlsx(racm, ctx), XLSX_MEDIA_TYPE, export_filename(ctx, "racm", "xlsx")
    )


@router.get("/{session_id}/export/working-papers.xlsx")
def export_working_papers(session_id: str) -> Response:
    flow, ctx, verification = _load(session_id)
    papers = flow.state.working_papers
    if papers is None:
        raise _missing("Working papers")
    return _download(
        working_papers_xlsx(papers, ctx, flow.effective_view()),
        XLSX_MEDIA_TYPE,
        export_filename(ctx, "working-papers", "xlsx"),
    )


@router.get("/{session_id}/export/report.md")
def export_report(session_id: str) -> Response:
    flow, ctx, verification = _load(session_id)
    report = flow.state.final_report
    if report is None:
        raise _missing("Final report")
    body = _with_trail_line(
        report_markdown(report, flow.state.approval_trail, ctx, flow.effective_view()),
        verification,
    ).encode("utf-8")
    return _download(
        body, "text/markdown; charset=utf-8", export_filename(ctx, "report", "md")
    )


@router.get("/{session_id}/export/oscal.json")
def export_oscal(session_id: str) -> Response:
    flow, ctx, verification = _load(session_id)
    state = flow.state
    if state.final_report is None:
        raise _missing("Final report")
    if state.working_papers is None:
        raise _missing("Working papers")
    try:
        body = oscal_json(
            state.final_report,
            state.working_papers,
            state.racm_plan,
            state.approval_trail,
            ctx,
            theme=state.theme,
            prepared_by=state.prepared_by,
            view=flow.effective_view(),
            decisions=state.review_decisions,
        )
    except ValueError:
        raise _missing("OSCAL assessment results") from None
    return _download(
        _with_trail_prop(body, verification),
        "application/json",
        export_filename(ctx, "oscal-sar", "json"),
    )

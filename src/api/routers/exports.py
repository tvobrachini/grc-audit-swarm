"""Artifact downloads: /api/sessions/{id}/export/…

404 until the artifact exists. Served same-origin under /api so the nginx
proxy adds the API token (the browser never holds it).
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from api.exports import (
    XLSX_MEDIA_TYPE,
    ExportContext,
    export_filename,
    oscal_json,
    racm_xlsx,
    report_markdown,
    working_papers_xlsx,
)
from api.routers.sessions import _get_or_load_flow, _require_session
from swarm.audit_flow import AuditFlow

router = APIRouter()


def _load(session_id: str) -> tuple[AuditFlow, ExportContext]:
    data = _require_session(session_id)
    flow = _get_or_load_flow(session_id)
    if flow is None:
        raise HTTPException(status_code=404, detail="No artifacts for this session")
    ctx = ExportContext(
        session_id=session_id,
        session_name=str(data.get("name", session_id)),
        status=flow.state.status,
    )
    return flow, ctx


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
    flow, ctx = _load(session_id)
    racm = flow.state.racm_plan
    if racm is None:
        raise _missing("RACM")
    return _download(
        racm_xlsx(racm, ctx), XLSX_MEDIA_TYPE, export_filename(ctx, "racm", "xlsx")
    )


@router.get("/{session_id}/export/working-papers.xlsx")
def export_working_papers(session_id: str) -> Response:
    flow, ctx = _load(session_id)
    papers = flow.state.working_papers
    if papers is None:
        raise _missing("Working papers")
    return _download(
        working_papers_xlsx(papers, ctx),
        XLSX_MEDIA_TYPE,
        export_filename(ctx, "working-papers", "xlsx"),
    )


@router.get("/{session_id}/export/report.md")
def export_report(session_id: str) -> Response:
    flow, ctx = _load(session_id)
    report = flow.state.final_report
    if report is None:
        raise _missing("Final report")
    body = report_markdown(report, flow.state.approval_trail, ctx).encode("utf-8")
    return _download(
        body, "text/markdown; charset=utf-8", export_filename(ctx, "report", "md")
    )


@router.get("/{session_id}/export/oscal.json")
def export_oscal(session_id: str) -> Response:
    flow, ctx = _load(session_id)
    report = flow.state.final_report
    body = oscal_json(report) if report is not None else None
    if body is None:
        raise _missing("OSCAL assessment results")
    return _download(
        body, "application/json", export_filename(ctx, "oscal-sar", "json")
    )

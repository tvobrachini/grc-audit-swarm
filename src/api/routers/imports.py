"""Upload a Prowler JSON findings file and register it as evidence for a session.

This is a convenience on top of the same read-only import the Evidence
Collector agent can run itself via the 'Import Prowler Findings' CrewAI tool
(``swarm.tools.findings_tools``): someone reviewing a session in the UI can
also attach a Prowler export directly, without setting an environment
variable and re-running fieldwork. It only reads and parses the upload and
writes a vault record — it does not touch the session's flow state, RACM or
working papers, so this file never needs to import anything from
``api.routers.sessions`` beyond the read-only ``_require_session`` helper
used to 404 on an unknown session id.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from api.auth import ReviewerIdentity, reviewer_identity
from api.routers.sessions import _require_session
from swarm.evidence import EvidenceAssuranceProtocol, _redact_account_ids, app_version
from swarm.tools.findings_checks import (
    MAX_PROWLER_FILE_BYTES,
    FindingsImportError,
    build_prowler_summary,
    check_prowler_file_size,
    parse_prowler_findings,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class ProwlerImportResult(BaseModel):
    vault_id: str
    summary: str


@router.post(
    "/{session_id}/imports/prowler",
    response_model=ProwlerImportResult,
    status_code=201,
)
def import_prowler_findings_file(
    session_id: str,
    file: UploadFile = File(...),
    uploaded_by: str = Form("", max_length=200),
    who: ReviewerIdentity = Depends(reviewer_identity),
) -> ProwlerImportResult:
    """Upload a Prowler JSON findings file (OCSF or legacy format, <= 10 MB,
    JSON only) and register a compact summary of it as evidence.

    Read-only and additive: this stores one evidence-vault record and
    returns its vault ID and the exact summary text, for a reviewer or the
    field auditor to cite. It does not modify the session's working papers
    on its own.

    Who uploaded it is required and recorded in the vault record's metadata:
    the typed ``uploaded_by`` form field, or the authenticated reviewer when
    reviewer tokens are configured (``X-Reviewer-Token``, ADR-012). 422 when
    no identity is given.
    """
    uploaded_by, identity_source = who.resolve(uploaded_by, "uploaded_by")
    uploaded_by = " ".join(uploaded_by.split())
    if not uploaded_by:
        raise HTTPException(
            status_code=422,
            detail="uploaded_by must not be blank: say who is importing this file.",
        )
    _require_session(session_id)

    filename = file.filename or ""
    if not filename.lower().endswith(".json"):
        raise HTTPException(
            status_code=422,
            detail="Upload must be a .json file (Prowler's JSON output).",
        )

    data = file.file.read(MAX_PROWLER_FILE_BYTES + 1)
    try:
        check_prowler_file_size(len(data))
    except FindingsImportError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    try:
        raw_text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=422, detail=f"Upload is not valid UTF-8 text: {exc}"
        ) from exc

    try:
        result = parse_prowler_findings(raw_text)
    except FindingsImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    summary = build_prowler_summary(result)
    metadata = {
        "tool": "import_prowler_findings_file",
        "operation": "api_upload",
        "app_version": app_version(),
        "parameters": {
            "session_id": session_id,
            "source_filename": filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1][:200],
            "uploaded_by": uploaded_by,
            "uploaded_by_identity_source": identity_source,
        },
    }
    vault_record = EvidenceAssuranceProtocol.register_evidence(
        summary, "prowler.findings_import.api_upload", metadata=metadata
    )
    logger.info(
        "Prowler findings imported for session %s by %r (%s): vault %s",
        session_id,
        uploaded_by,
        identity_source,
        vault_record["vault_id"],
    )
    return ProwlerImportResult(
        vault_id=vault_record["vault_id"], summary=_redact_account_ids(summary)
    )

from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator


# Control frameworks the RACM maps to. Auditing standards (PCAOB, IIA) are not
# control frameworks, so they are not defaults here.
DEFAULT_FRAMEWORKS = ("COSO 2013", "NIST SP 800-53", "CIS Controls")


_MAX_IDENTITY = 200
_MAX_NOTES = 4000


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value.strip()


class CreateSessionRequest(BaseModel):
    theme: str
    business_context: str
    frameworks: list[str] = list(DEFAULT_FRAMEWORKS)
    name: Optional[str] = None
    # Declared (not authenticated) identity of the preparer; the preparer may
    # not approve gates, override QA or return work on this audit.
    prepared_by: str = Field(min_length=1, max_length=_MAX_IDENTITY)

    _prepared_by_not_blank = field_validator("prepared_by")(_not_blank)


class ApproveGateRequest(BaseModel):
    human_id: str
    gate_number: int  # 1, 2, or 3


class RetryPhaseRequest(BaseModel):
    """Re-run a phase that is QA_REJECTED_PHASE_n or ERROR_PHASE_n."""

    human_id: str = Field(min_length=1)
    phase: int = Field(ge=1, le=3)


class QAOverrideRequest(BaseModel):
    """Supervisor accepts a QA-rejected artifact (→ WAITING_HUMAN_GATE_n)."""

    human_id: str = Field(min_length=1)
    phase: int = Field(ge=1, le=3)
    reason: str = Field(min_length=1)


class ReturnForReworkRequest(BaseModel):
    """Reviewer returns WAITING_HUMAN_GATE_n → RUNNING_PHASE_n with notes."""

    human_id: str = Field(min_length=1, max_length=_MAX_IDENTITY)
    phase: int = Field(ge=1, le=3)
    notes: str = Field(min_length=1, max_length=_MAX_NOTES)


class TrailVerification(BaseModel):
    """Result of recomputing the approval trail's hash chain.

    ``status``: ok | legacy_unchained | broken | truncated | unkeyed |
    key_unavailable | artifact_changed. ``ok`` is true only for ``ok``.
    """

    ok: bool
    status: str
    entries: int
    legacy_entries: int = 0
    first_broken_index: Optional[int] = None
    keyed: bool = False
    anchored: bool = False
    head_hash: Optional[str] = None
    changed_since_approval: list[str] = Field(default_factory=list)
    detail: str = ""


class SessionSummary(BaseModel):
    session_id: str
    name: str
    status: str
    phase: int
    needs_input: bool
    created_at: str
    prepared_by: str = ""


class SessionDetail(BaseModel):
    session_id: str
    name: str
    status: str
    phase: int
    needs_input: bool
    created_at: str
    theme: str
    business_context: str
    frameworks: list[str]
    current_human_dossier: str
    racm_plan: Optional[dict[str, Any]]
    working_papers: Optional[dict[str, Any]]
    final_report: Optional[dict[str, Any]]
    approval_trail: list[dict[str, str]]
    qa_rejection_reason: Optional[str]
    prepared_by: str = ""
    trail_verification: Optional[TrailVerification] = None


class VerifyEvidenceRequest(BaseModel):
    vault_id: str
    exact_quote: str


def _phase_from_status(status: str) -> int:
    if "1" in status:
        return 1
    if "2" in status:
        return 2
    if "3" in status or status == "COMPLETED":
        return 3
    return 0


def _needs_input(status: str) -> bool:
    return status.startswith("WAITING_HUMAN_GATE")

from typing import Any, Optional
from pydantic import BaseModel


class CreateSessionRequest(BaseModel):
    theme: str
    business_context: str
    frameworks: list[str] = ["COSO", "PCAOB", "IIA"]
    name: Optional[str] = None


class ApproveGateRequest(BaseModel):
    human_id: str
    gate_number: int  # 1, 2, or 3


class SessionSummary(BaseModel):
    session_id: str
    name: str
    status: str
    phase: int
    needs_input: bool
    created_at: str


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

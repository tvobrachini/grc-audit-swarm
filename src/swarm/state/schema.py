"""
Shared Pydantic state models used across agents, workers, and the flow orchestrator.
"""

from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List, Dict, Any

from swarm.schema import RiskControlMatrixSchema, WorkingPaperSchema, FinalReportSchema


class AuditProcedures(BaseModel):
    """Test procedures for a single control, used by the Execution Worker."""

    tod_steps: List[str] = Field(default_factory=list)
    toe_steps: List[str] = Field(default_factory=list)
    substantive_steps: List[str] = Field(default_factory=list)


class ControlMatrixItem(BaseModel):
    """A single control row from the RACM, passed to the Execution Worker."""

    control_id: str
    domain: str
    description: str
    procedures: Optional[AuditProcedures] = None


class AuditFinding(BaseModel):
    """Structured finding produced by the Execution Worker for a single control."""

    control_id: str
    agent_role: str
    status: str = Field(description="'Pass', 'Fail', or 'Exception'")
    justification: str
    evidence_extracted: List[str] = Field(default_factory=list)
    risk_rating: Optional[str] = Field(
        default=None,
        description="'High', 'Medium', 'Low', or None for Pass findings",
    )
    tod_result: str = ""
    toe_result: str = ""
    substantive_result: str = ""


class AuditState(BaseModel):
    """
    Full state for a GRC audit run.
    Shared by AuditFlow and agent utilities (specialist, worker).
    """

    model_config = ConfigDict(validate_assignment=True)

    theme: str = ""
    business_context: str = ""
    frameworks: List[str] = Field(default_factory=list)

    # Artifact payloads saved after each phase — typed for schema validation on load
    racm_plan: Optional[RiskControlMatrixSchema] = None
    working_papers: Optional[WorkingPaperSchema] = None
    final_report: Optional[FinalReportSchema] = None

    # Human review routing and dossier state
    current_human_dossier: str = ""
    status: str = "WAITING_FOR_SCOPE"
    qa_rejection_reason: Optional[str] = None

    # Skill system (used by specialist + worker)
    active_skill_ids: List[str] = Field(default_factory=list)
    specialist_roles_required: List[str] = Field(default_factory=list)

    # Evidence log keyed by control_id (used by worker)
    evidence_log: Dict[str, Any] = Field(default_factory=dict)

    # Findings produced by the parallel worker pool
    findings: List[AuditFinding] = Field(default_factory=list)

    # IIA 2340 Audit Trail Log
    approval_trail: List[Dict[str, str]] = Field(default_factory=list)

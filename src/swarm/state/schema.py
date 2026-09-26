"""
Shared Pydantic state models used across agents, workers, and the flow orchestrator.
"""

from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List, Dict

from swarm.schema import RiskControlMatrixSchema, WorkingPaperSchema, FinalReportSchema


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

    # Declared identity of the person who prepared (created) the audit. The
    # preparer may not review their own work (swarm.review_policy). Empty for
    # sessions created before this field existed.
    prepared_by: str = ""

    # Audit trail log (see Standard 12.3, formerly IIA 2340). Append-only and
    # hash-chained: only swarm.trail.append_entry (via AuditFlow._stamp_trail)
    # may add entries; swarm.trail.verify_trail checks the chain.
    approval_trail: List[Dict[str, str]] = Field(default_factory=list)

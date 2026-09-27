"""
Shared Pydantic state models used across agents, workers, and the flow orchestrator.
"""

from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List, Dict

from swarm.schema import (
    FinalReportSchema,
    ReviewDecision,
    RiskControlMatrixSchema,
    WorkingPaperSchema,
)


class GenerationRun(BaseModel):
    """Generation provenance for one phase run (see DECISIONS.md, ADR-010).

    One entry is recorded per attempted phase run (``AuditFlow.generate_*``),
    whether it QA-approves, is QA-rejected, or errors. Never holds an API key,
    base URL or token — only the provider/model *names* (see
    ``swarm.llm_factory.describe_crew_llm`` / ``describe_qa_llm``).
    """

    model_config = ConfigDict(validate_assignment=True)

    run_id: str
    phase: int
    provider: str
    model: str
    qa_provider: str
    qa_model: str
    temperature: float
    qa_temperature: float
    crewai_version: str
    app_version: str
    # SHA-256 over the phase's agent/task YAML config and any skill text
    # injected into an agent's backstory (see
    # ``swarm.audit_flow.prompt_fingerprint``); deterministic for the same
    # config + skills.
    prompt_fingerprint: str
    started_at: str
    ended_at: Optional[str] = None
    # Crew attempts made in this run (auto-retry: 1 or 2 — see
    # AuditFlow._MAX_QA_ATTEMPTS).
    attempts: int = 0
    demo_mode: bool = False
    # "running" | "qa_approved" | "qa_rejected" | "error"
    outcome: str = "running"


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

    # Generation provenance: one entry per attempted phase run (see
    # GenerationRun and DECISIONS.md, ADR-010). Never mutated in place after
    # its owning run finishes; a retry appends a new entry instead.
    generation_runs: List[GenerationRun] = Field(default_factory=list)

    # Reviewer decisions (see swarm.review_decisions and DECISIONS.md,
    # ADR-011). Append-only: only AuditFlow.record_decision adds to it, and
    # each addition is also a chained ``review_decision`` trail entry. The AI
    # drafts above never change because of a decision.
    review_decisions: List[ReviewDecision] = Field(default_factory=list)

    # Whether gate approvals on this audit require reviewer decisions
    # (review_policy gate preconditions). Set when the audit is created
    # through the API, and recorded in its ``audit_created`` trail entry.
    # False for audits created before decisions existed.
    review_decisions_required: bool = False

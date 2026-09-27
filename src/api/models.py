from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator

from swarm.review_decisions import EffectiveView


# Control frameworks the RACM maps to. Auditing standards (PCAOB, IIA) are not
# control frameworks, so they are not defaults here.
DEFAULT_FRAMEWORKS = ("COSO 2013", "NIST SP 800-53", "CIS Controls")


_MAX_IDENTITY = 200
_MAX_NOTES = 4000


def _strip(value: str) -> str:
    return value.strip()


# Identity fields (prepared_by, human_id, decided_by) may be omitted: with
# per-reviewer tokens configured (REVIEWER_TOKENS_FILE, ADR-012) the API takes
# the identity from the X-Reviewer-Token header and refuses (403) a typed name
# that differs. Without tokens the typed name is the declared identity and a
# blank one is refused with 422, as before.
def _identity_field() -> Any:
    return Field(default="", max_length=_MAX_IDENTITY)


class CreateSessionRequest(BaseModel):
    theme: str
    business_context: str
    frameworks: list[str] = list(DEFAULT_FRAMEWORKS)
    name: Optional[str] = None
    # Identity of the preparer (declared, or from the reviewer token); the
    # preparer may not approve gates, override QA or return work on this audit.
    prepared_by: str = _identity_field()

    _prepared_by_strip = field_validator("prepared_by")(_strip)


class ApproveGateRequest(BaseModel):
    human_id: str = _identity_field()
    gate_number: int  # 1, 2, or 3


class RetryPhaseRequest(BaseModel):
    """Re-run a phase that is QA_REJECTED_PHASE_n or ERROR_PHASE_n."""

    human_id: str = _identity_field()
    phase: int = Field(ge=1, le=3)


class QAOverrideRequest(BaseModel):
    """Supervisor accepts a QA-rejected artifact (→ WAITING_HUMAN_GATE_n)."""

    human_id: str = _identity_field()
    phase: int = Field(ge=1, le=3)
    reason: str = Field(min_length=1)


class ReturnForReworkRequest(BaseModel):
    """Reviewer returns WAITING_HUMAN_GATE_n → RUNNING_PHASE_n with notes."""

    human_id: str = _identity_field()
    phase: int = Field(ge=1, le=3)
    notes: str = Field(min_length=1, max_length=_MAX_NOTES)


class TrailVerification(BaseModel):
    """Result of recomputing the approval trail's hash chain.

    ``status``: ok | legacy_unchained | broken | truncated | unkeyed |
    key_unavailable | artifact_changed | decision_changed. ``ok`` is true
    only for ``ok``.
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
    # Reviewer decisions that no longer match their trail entry (status
    # ``decision_changed``; see ADR-011).
    decisions_changed: list[str] = Field(default_factory=list)
    detail: str = ""


class SessionSummary(BaseModel):
    session_id: str
    name: str
    status: str
    phase: int
    needs_input: bool
    created_at: str
    prepared_by: str = ""


class GenerationRunSummary(BaseModel):
    """Generation provenance for one phase run — see DECISIONS.md, ADR-010.

    Never carries an API key, base URL or token: only provider/model names,
    a deterministic prompt fingerprint, timing and outcome.
    """

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
    prompt_fingerprint: str
    started_at: str
    ended_at: Optional[str] = None
    attempts: int = 0
    demo_mode: bool = False
    outcome: str = "running"


_MAX_DECISION_VALUE = 8000
_MAX_DECISION_VALUES = 12


class RecordDecisionRequest(BaseModel):
    """Append one reviewer decision (see DECISIONS.md, ADR-011).

    ``values`` depends on ``decision_type``:

    * ``sign_off`` / ``scope_limitation``: none.
    * ``challenge``: optional ``tod_conclusion`` / ``toe_conclusion`` (only
      "Effective" → "Not tested" is allowed without rework).
    * ``classify``: ``classification`` and optional ``likelihood`` /
      ``magnitude`` (default: the AI draft's).
    * ``writeup``: ``criteria``, ``condition``, ``cause``, ``effect``,
      ``recommendation``.
    * ``management_response``: ``text``, ``agreement`` (agree | partial |
      disagree), ``received_from``, ``received_on`` (YYYY-MM-DD), and for
      agree / partial ``action_owner_role`` and ``target_date``.
    * ``engagement_conclusion``: ``conclusion`` (Satisfactory | Needs
      improvement | Unsatisfactory).
    """

    decision_type: str = Field(min_length=1, max_length=64)
    subject_id: str = Field(default="", max_length=_MAX_IDENTITY)
    # Optional: derived from decision_type; checked when given.
    subject_type: Optional[str] = Field(default=None, max_length=32)
    values: dict[str, Any] = Field(default_factory=dict)
    rationale: str = Field(default="", max_length=_MAX_NOTES)
    # Identity of the reviewer (declared, or from the reviewer token).
    decided_by: str = _identity_field()
    # decision_id of the active decision this one corrects.
    supersedes: Optional[str] = Field(default=None, max_length=64)

    _decided_by_strip = field_validator("decided_by")(_strip)

    @field_validator("values")
    @classmethod
    def _scalar_values(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > _MAX_DECISION_VALUES:
            raise ValueError(f"at most {_MAX_DECISION_VALUES} values")
        for key, item in value.items():
            if item is not None and not isinstance(item, str):
                raise ValueError(f"values.{key} must be a string")
            if isinstance(item, str) and len(item) > _MAX_DECISION_VALUE:
                raise ValueError(
                    f"values.{key} is longer than {_MAX_DECISION_VALUE} characters"
                )
        return {k: v for k, v in value.items() if v is not None}


class ReviewDecisionRecord(BaseModel):
    """A stored reviewer decision plus its current state.

    ``state``: active (counts in the effective view) | superseded (a later
    decision replaced it) | stale (its artifact was re-drafted since).
    """

    decision_id: str
    phase: int
    artifact: str
    draft_digest: str
    subject_type: str
    subject_id: str
    decision_type: str
    values: dict[str, str] = Field(default_factory=dict)
    rationale: str = ""
    decided_by: str
    identity_source: str
    decided_at: str
    supersedes: Optional[str] = None
    state: str = "active"


class ReviewDecisionsResponse(BaseModel):
    decisions: list[ReviewDecisionRecord] = Field(default_factory=list)
    effective: Optional[EffectiveView] = None


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
    generation_runs: list[GenerationRunSummary] = Field(default_factory=list)
    # Reviewer decisions (ADR-011): every decision as recorded, with its
    # state, and the effective view (AI draft + active decisions) that the
    # report and exports render. ``review_decisions_required`` says whether
    # gate approvals on this audit need them.
    review_decisions: list[ReviewDecisionRecord] = Field(default_factory=list)
    effective: Optional[EffectiveView] = None
    review_decisions_required: bool = False


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

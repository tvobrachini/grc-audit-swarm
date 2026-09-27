"""
Reviewer decisions: validation, the effective view, gate preconditions and
the reviewer change rate (see DECISIONS.md, ADR-011).

The AI drafts (RACM, working papers, final report) are never changed by a
reviewer. A reviewer's judgement is recorded as an append-only
:class:`~swarm.schema.ReviewDecision`, and every renderer uses the
**effective view**: the draft, plus the latest active decision for each
subject. A decision is

* **active** — not superseded, and made on the artifact as it is now;
* **superseded** — a later decision names it in ``supersedes``;
* **stale** — its artifact was re-drafted since (a return for rework), so it
  judged a draft that no longer exists. It stays on the record but no longer
  counts, and the reviewer decides again on the new draft.

Decisions are grouped into *slots* per subject: a finding has one review
slot (``sign_off`` or ``challenge``) and one ``scope_limitation`` slot; a
deficiency has ``classify``, ``writeup`` and ``management_response`` slots;
the engagement has one ``engagement_conclusion`` slot. A slot holds at most
one active decision: a correction must name the decision it replaces.

Policy (who may decide what, when, and what each gate needs) lives in
:mod:`swarm.review_policy`; this module applies it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Iterable, Literal, Mapping, Optional, Sequence, cast

from pydantic import BaseModel, Field, ValidationError

from swarm import review_policy as policy
from swarm.review_policy import ReviewBlockedError, SegregationOfDutiesError
from swarm.schema import (
    DECISION_VALUE_MODELS,
    SCALE_CLASSIFICATIONS,
    AuditFindingSchema,
    ChallengeValues,
    ClassifyValues,
    DecisionSubjectType,
    DecisionType,
    DeficiencyClassification,
    DeficiencyEvaluationSchema,
    DesignConclusion,
    FinalReportSchema,
    FindingResult,
    IdentitySource,
    ManagementResponseValues,
    OperatingConclusion,
    ReviewDecision,
    RiskControlMatrixSchema,
    RiskRating,
    WorkingPaperSchema,
    _derive_result,
)
from swarm.trail import artifact_digest

__all__ = [
    "DecisionConflictError",
    "DecisionContext",
    "DecisionValidationError",
    "EffectiveView",
    "build_decision",
    "decision_states",
    "effective_view",
]

ENGAGEMENT_SUBJECT_ID = "engagement"

SUBJECT_FOR_TYPE: dict[DecisionType, DecisionSubjectType] = {
    DecisionType.SIGN_OFF: DecisionSubjectType.FINDING,
    DecisionType.CHALLENGE: DecisionSubjectType.FINDING,
    DecisionType.SCOPE_LIMITATION: DecisionSubjectType.FINDING,
    DecisionType.CLASSIFY: DecisionSubjectType.DEFICIENCY,
    DecisionType.WRITEUP: DecisionSubjectType.DEFICIENCY,
    DecisionType.MANAGEMENT_RESPONSE: DecisionSubjectType.DEFICIENCY,
    DecisionType.ENGAGEMENT_CONCLUSION: DecisionSubjectType.ENGAGEMENT,
}

# Gate whose approval seals a decision of each type.
PHASE_FOR_TYPE: dict[DecisionType, int] = {
    DecisionType.SIGN_OFF: 2,
    DecisionType.CHALLENGE: 2,
    DecisionType.SCOPE_LIMITATION: 3,
    DecisionType.CLASSIFY: 3,
    DecisionType.WRITEUP: 3,
    DecisionType.MANAGEMENT_RESPONSE: 3,
    DecisionType.ENGAGEMENT_CONCLUSION: 3,
}

ARTIFACT_FOR_SUBJECT: dict[DecisionSubjectType, str] = {
    DecisionSubjectType.FINDING: "working_papers",
    DecisionSubjectType.DEFICIENCY: "final_report",
    DecisionSubjectType.ENGAGEMENT: "final_report",
}

# sign_off and challenge share a slot: either one is "the review" of a finding.
_FINDING_REVIEW = "finding_review"


def slot_of(decision_type: DecisionType | str) -> str:
    dt = DecisionType(decision_type)
    if dt in (DecisionType.SIGN_OFF, DecisionType.CHALLENGE):
        return _FINDING_REVIEW
    return dt.value


class DecisionValidationError(ValueError):
    """The decision request is malformed or does not fit the draft (HTTP 422)."""


class DecisionConflictError(ReviewBlockedError):
    """The decision conflicts with the audit's state or policy (HTTP 409)."""


# ── Context: the artifacts decisions refer to ───────────────────────────────


@dataclass(frozen=True)
class DecisionContext:
    racm: Optional[RiskControlMatrixSchema]
    papers: Optional[WorkingPaperSchema]
    report: Optional[FinalReportSchema]

    def artifact(self, name: str) -> Any:
        return {"working_papers": self.papers, "final_report": self.report}.get(name)

    def digests(self) -> dict[str, Optional[str]]:
        return {
            name: (artifact_digest(a) if a is not None else None)
            for name in ("working_papers", "final_report")
            for a in [self.artifact(name)]
        }

    def finding(self, control_id: str) -> Optional[AuditFindingSchema]:
        for f in self.papers.findings if self.papers else []:
            if f.control_id == control_id:
                return f
        return None

    def deficiency(self, deficiency_id: str) -> Optional[DeficiencyEvaluationSchema]:
        for e in self.report.deficiency_evaluations if self.report else []:
            if e.deficiency_id == deficiency_id:
                return e
        return None

    def key_controls(self) -> dict[str, Optional[bool]]:
        out: dict[str, Optional[bool]] = {}
        for risk in self.racm.risks if self.racm else []:
            for c in risk.controls:
                out.setdefault(c.control_id, c.key_control)
        return out


# ── Decision states ──────────────────────────────────────────────────────────

DecisionState = Literal["active", "superseded", "stale"]


def decision_states(
    ctx: DecisionContext, decisions: Sequence[ReviewDecision]
) -> dict[str, DecisionState]:
    """``decision_id`` → active / superseded / stale."""
    superseded = {d.supersedes for d in decisions if d.supersedes}
    digests = ctx.digests()
    states: dict[str, DecisionState] = {}
    for d in decisions:
        if d.decision_id in superseded:
            states[d.decision_id] = "superseded"
        elif digests.get(d.artifact) != d.draft_digest:
            states[d.decision_id] = "stale"
        else:
            states[d.decision_id] = "active"
    return states


def _slot_key(d: ReviewDecision) -> tuple[str, str, str]:
    return (slot_of(d.decision_type), str(d.subject_type), d.subject_id)


def active_by_slot(
    ctx: DecisionContext, decisions: Sequence[ReviewDecision]
) -> dict[tuple[str, str, str], ReviewDecision]:
    """(slot, subject_type, subject_id) → the latest active decision."""
    states = decision_states(ctx, decisions)
    out: dict[tuple[str, str, str], ReviewDecision] = {}
    for d in decisions:
        if states[d.decision_id] == "active":
            out[_slot_key(d)] = d
    return out


# ── Recording: validate one decision request ────────────────────────────────


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _validation_message(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "values"
        parts.append(f"values.{loc}: {err.get('msg', 'invalid')}")
    return "; ".join(parts)


def _iso_date(value: Optional[str], what: str) -> Optional[str]:
    if value is None or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10]).isoformat()
    except ValueError as exc:
        raise DecisionValidationError(
            f"values.{what} must be an ISO date (YYYY-MM-DD)"
        ) from exc


def _effective_conclusions(
    finding: AuditFindingSchema, review: Optional[ReviewDecision]
) -> tuple[str, str, str]:
    """(ToD, ToE, result) after an active challenge's conclusion changes."""
    tod = str(finding.tod_conclusion)
    toe = str(finding.toe_conclusion)
    if review is not None and review.decision_type == DecisionType.CHALLENGE:
        tod = review.values.get("tod_conclusion", tod)
        toe = review.values.get("toe_conclusion", toe)
    result = _derive_result(
        DesignConclusion(tod), OperatingConclusion(toe), finding.exceptions_noted
    )
    return tod, toe, str(result)


def _challenge_values(
    finding: AuditFindingSchema, parsed: ChallengeValues
) -> dict[str, str]:
    values: dict[str, str] = {}
    for field, draft in (
        ("tod_conclusion", str(finding.tod_conclusion)),
        ("toe_conclusion", str(finding.toe_conclusion)),
    ):
        new = getattr(parsed, field)
        if new is None or str(new) == draft:
            continue
        if not policy.conclusion_change_allowed(draft, str(new)):
            raise DecisionConflictError(
                f"{finding.control_id}: changing {field} from '{draft}' to "
                f"'{new}' needs new work — return the phase for rework. Without "
                "rework a reviewer may only withdraw a positive conclusion "
                f"({', '.join(sorted(policy.CONCLUSION_DOWNGRADE_FROM))} → "
                f"'{policy.CONCLUSION_DOWNGRADE_TARGET}')."
            )
        values[field] = str(new)
    return values


def _classify_values(
    report: FinalReportSchema,
    evaluation: DeficiencyEvaluationSchema,
    parsed: ClassifyValues,
) -> dict[str, str]:
    likelihood = parsed.likelihood or evaluation.likelihood
    magnitude = parsed.magnitude or evaluation.magnitude
    classification = parsed.classification
    scale = report.deficiency_scale
    if scale is not None and classification not in SCALE_CLASSIFICATIONS[scale]:
        allowed = ", ".join(sorted(str(c) for c in SCALE_CLASSIFICATIONS[scale]))
        raise DecisionValidationError(
            f"values.classification '{classification}' is not on this report's "
            f"'{scale}' scale ({allowed})"
        )
    if classification == DeficiencyClassification.MATERIAL_WEAKNESS and (
        likelihood == RiskRating.LOW or magnitude != RiskRating.HIGH
    ):
        raise DecisionValidationError(
            "a Material Weakness requires at least a reasonable possibility "
            "(likelihood Medium/High) of a material misstatement (magnitude High)"
        )
    return {
        "classification": str(classification),
        "likelihood": str(likelihood),
        "magnitude": str(magnitude),
    }


def _draft_classification(evaluation: DeficiencyEvaluationSchema) -> dict[str, str]:
    return {
        "classification": str(evaluation.classification),
        "likelihood": str(evaluation.likelihood),
        "magnitude": str(evaluation.magnitude),
    }


def _management_values(
    parsed: ManagementResponseValues, rationale: str, recorded_on: str
) -> dict[str, str]:
    agreement = str(parsed.agreement)
    received_on = _iso_date(parsed.received_on, "received_on") or ""
    if received_on and received_on > recorded_on:
        raise DecisionValidationError(
            f"values.received_on ({received_on}) is after the date this "
            f"decision is being recorded ({recorded_on}): the response cannot "
            "have been received before it happened"
        )
    values = {
        "text": parsed.text,
        "agreement": agreement,
        "received_from": parsed.received_from,
        "received_on": received_on,
    }
    owner = _text(parsed.action_owner_role)
    target = _iso_date(parsed.target_date, "target_date")
    if policy.management_response_needs_action_plan(agreement) and not (
        owner and target
    ):
        raise DecisionValidationError(
            "an agreed or partly agreed management response needs "
            "values.action_owner_role and values.target_date"
        )
    if owner:
        values["action_owner_role"] = owner
    if target:
        values["target_date"] = target
    if policy.management_response_needs_rebuttal(agreement) and not rationale:
        raise DecisionValidationError(
            "management disagrees: the rationale must state the auditor's "
            "rebuttal (it is shown in the report next to the response)"
        )
    return values


def build_decision(
    ctx: DecisionContext,
    *,
    status: str,
    preparers: Iterable[str],
    decisions: Sequence[ReviewDecision],
    decision_type: str,
    subject_id: str,
    decided_by: str,
    values: Optional[Mapping[str, Any]] = None,
    rationale: str = "",
    subject_type: Optional[str] = None,
    supersedes: Optional[str] = None,
    identity_source: str = policy.DEFAULT_IDENTITY_SOURCE,
    now: Optional[str] = None,
) -> ReviewDecision:
    """Validate a decision request against policy and the current drafts.

    Returns the decision to append; does not store it.

    Raises:
        DecisionValidationError: malformed values, unknown subject, missing
            rationale (HTTP 422).
        DecisionConflictError: wrong status, a conclusion change that needs
            rework, or a supersede conflict (HTTP 409).
        SegregationOfDutiesError: the preparer may not record this type
            (HTTP 409).
    """
    decided_at = now or _now()
    try:
        dtype = DecisionType(decision_type)
    except ValueError as exc:
        allowed = ", ".join(t.value for t in DecisionType)
        raise DecisionValidationError(
            f"decision_type must be one of: {allowed}"
        ) from exc
    decided_by = _text(decided_by)
    if not decided_by:
        raise DecisionValidationError("decided_by is required")
    rationale = _text(rationale)
    try:
        source = IdentitySource(identity_source)
    except ValueError as exc:
        raise DecisionValidationError(
            "identity_source must be one of: "
            + ", ".join(s.value for s in IdentitySource)
        ) from exc

    violation = policy.decision_status_violation(dtype.value, status)
    if violation:
        raise DecisionConflictError(violation)
    for preparer in preparers:
        violation = policy.decision_sod_violation(dtype.value, decided_by, preparer)
        if violation:
            raise SegregationOfDutiesError(violation)

    stype = SUBJECT_FOR_TYPE[dtype]
    if subject_type is not None and _text(subject_type) != stype.value:
        raise DecisionValidationError(
            f"a '{dtype.value}' decision is about a {stype.value}, not '{subject_type}'"
        )
    subject_id = _text(subject_id)
    if stype == DecisionSubjectType.ENGAGEMENT:
        subject_id = subject_id or ENGAGEMENT_SUBJECT_ID
        if subject_id != ENGAGEMENT_SUBJECT_ID:
            raise DecisionValidationError(
                f"an engagement decision's subject_id is '{ENGAGEMENT_SUBJECT_ID}'"
            )
    if not subject_id:
        raise DecisionValidationError("subject_id is required")

    artifact_name = ARTIFACT_FOR_SUBJECT[stype]
    artifact = ctx.artifact(artifact_name)
    if artifact is None:
        raise DecisionConflictError(f"there are no {artifact_name} to decide on yet")

    try:
        parsed = DECISION_VALUE_MODELS[dtype].model_validate(dict(values or {}))
    except ValidationError as exc:
        raise DecisionValidationError(_validation_message(exc)) from exc

    active = active_by_slot(ctx, decisions)
    stored: dict[str, str]
    if stype == DecisionSubjectType.FINDING:
        finding = ctx.finding(subject_id)
        if finding is None:
            raise DecisionValidationError(
                f"no working-paper finding for control '{subject_id}'"
            )
        if dtype == DecisionType.CHALLENGE:
            stored = _challenge_values(finding, cast(ChallengeValues, parsed))
        else:
            stored = {}
        if dtype == DecisionType.SCOPE_LIMITATION:
            review = active.get((_FINDING_REVIEW, stype.value, subject_id))
            _, _, result = _effective_conclusions(finding, review)
            if result != FindingResult.NOT_TESTED:
                raise DecisionValidationError(
                    f"{subject_id} is concluded '{result}', not 'Not tested': "
                    "only an untested control is reported as a scope limitation"
                )
    elif stype == DecisionSubjectType.DEFICIENCY:
        evaluation = ctx.deficiency(subject_id)
        if evaluation is None or ctx.report is None:
            raise DecisionValidationError(
                f"no deficiency evaluation '{subject_id}' in the report"
            )
        if dtype == DecisionType.CLASSIFY:
            stored = _classify_values(
                ctx.report, evaluation, cast(ClassifyValues, parsed)
            )
            if (
                policy.classification_differs(_draft_classification(evaluation), stored)
                and not rationale
            ):
                raise DecisionValidationError(
                    "the classification differs from the AI draft "
                    f"({evaluation.classification}, likelihood "
                    f"{evaluation.likelihood}, magnitude {evaluation.magnitude}): "
                    "a rationale is required"
                )
        elif dtype == DecisionType.MANAGEMENT_RESPONSE:
            stored = _management_values(
                cast(ManagementResponseValues, parsed), rationale, decided_at[:10]
            )
        else:
            stored = {k: str(v) for k, v in parsed.model_dump().items()}
    else:
        stored = {k: str(v) for k, v in parsed.model_dump().items()}

    if dtype.value in policy.RATIONALE_ALWAYS_REQUIRED and not rationale:
        raise DecisionValidationError(f"a '{dtype.value}' decision needs a rationale")

    key = (slot_of(dtype), stype.value, subject_id)
    current = active.get(key)
    supersedes = _text(supersedes) or None
    if supersedes is None:
        if current is not None:
            raise DecisionConflictError(
                f"{subject_id} already has an active {slot_of(dtype)} decision "
                f"({current.decision_id}, {current.decision_type} by "
                f"{current.decided_by}). To correct it, record the new decision "
                f"with supersedes='{current.decision_id}'."
            )
    else:
        target = next((d for d in decisions if d.decision_id == supersedes), None)
        if target is None:
            raise DecisionValidationError(f"supersedes: no decision '{supersedes}'")
        if _slot_key(target) != key:
            raise DecisionValidationError(
                f"supersedes: decision '{supersedes}' is a {target.decision_type} "
                f"on {target.subject_type} '{target.subject_id}'; a correction "
                "must be about the same subject and the same kind of decision"
            )
        if current is None or current.decision_id != supersedes:
            state = decision_states(ctx, decisions).get(supersedes)
            raise DecisionConflictError(
                f"supersedes: decision '{supersedes}' is {state}, so it cannot be "
                "superseded"
                + (
                    f"; the active decision is '{current.decision_id}'"
                    if current is not None
                    else ""
                )
            )

    return ReviewDecision(
        decision_id=str(uuid.uuid4()),
        phase=PHASE_FOR_TYPE[dtype],
        artifact=artifact_name,
        draft_digest=artifact_digest(artifact),
        subject_type=stype,
        subject_id=subject_id,
        decision_type=dtype,
        values=stored,
        rationale=rationale,
        decided_by=decided_by,
        identity_source=source,
        decided_at=decided_at,
        supersedes=supersedes,
    )


# ── The effective view ───────────────────────────────────────────────────────


class DecisionRef(BaseModel):
    """The decision a subject's record rests on (a compact copy)."""

    decision_id: str
    decision_type: str
    decided_by: str
    identity_source: str
    decided_at: str
    rationale: str = ""
    values: dict[str, str] = Field(default_factory=dict)
    supersedes: Optional[str] = None

    @classmethod
    def of(cls, d: Optional[ReviewDecision]) -> Optional["DecisionRef"]:
        if d is None:
            return None
        return cls(
            decision_id=d.decision_id,
            decision_type=str(d.decision_type),
            decided_by=d.decided_by,
            identity_source=str(d.identity_source),
            decided_at=d.decided_at,
            rationale=d.rationale,
            values=dict(d.values),
            supersedes=d.supersedes,
        )


class FindingConclusions(BaseModel):
    tod_conclusion: str
    toe_conclusion: str
    result: str
    preliminary_deficiency: bool


class FindingView(BaseModel):
    control_id: str
    key_control: Optional[bool] = None
    draft: FindingConclusions
    effective: FindingConclusions
    # signed_off | challenged | not_reviewed
    review_status: str
    review: Optional[DecisionRef] = None
    scope_limitation: Optional[DecisionRef] = None
    differs_from_draft: bool = False
    review_required_for_gate_2: bool = False


class Classification(BaseModel):
    classification: str
    likelihood: str
    magnitude: str


class DeficiencyView(BaseModel):
    deficiency_id: str
    title: str
    related_findings: list[str]
    draft: Classification
    effective: Classification
    # "reviewer" once a classify decision is active, else "ai_draft".
    classification_source: str
    classification_decision: Optional[DecisionRef] = None
    differs_from_draft: bool = False
    writeup: Optional[DecisionRef] = None
    management_response: Optional[DecisionRef] = None


class ReviewerChangeRate(BaseModel):
    """Share of reviewed subjects where the reviewer departed from the AI
    draft (a challenge, or a classification that differs). Computed per
    session for the reviewer's own information; not a published metric."""

    subjects_decided: int
    subjects_changed: int
    rate: Optional[float] = None
    published: bool = policy.REVIEWER_CHANGE_RATE_PUBLISHED


class MissingDecision(BaseModel):
    gate: int
    subject_type: str
    subject_id: str
    # Any one of these decision types satisfies the requirement.
    required: list[str]
    reason: str


class EffectiveView(BaseModel):
    """The conclusion of record: AI drafts plus active reviewer decisions."""

    decisions_required: bool
    deficiency_scale: Optional[str] = None
    findings: list[FindingView] = Field(default_factory=list)
    deficiencies: list[DeficiencyView] = Field(default_factory=list)
    engagement_conclusion: Optional[DecisionRef] = None
    missing_for_gate: dict[str, list[MissingDecision]] = Field(default_factory=dict)
    reviewer_change_rate: ReviewerChangeRate
    stale_decision_ids: list[str] = Field(default_factory=list)
    superseded_decision_ids: list[str] = Field(default_factory=list)

    def finding(self, control_id: str) -> Optional[FindingView]:
        return next((f for f in self.findings if f.control_id == control_id), None)

    def deficiency(self, deficiency_id: str) -> Optional[DeficiencyView]:
        return next(
            (d for d in self.deficiencies if d.deficiency_id == deficiency_id), None
        )


def _finding_views(
    ctx: DecisionContext, active: dict[tuple[str, str, str], ReviewDecision]
) -> list[FindingView]:
    keys = ctx.key_controls()
    views: list[FindingView] = []
    seen: set[str] = set()
    for f in ctx.papers.findings if ctx.papers else []:
        if f.control_id in seen:  # duplicate control IDs share one subject
            continue
        seen.add(f.control_id)
        fid = DecisionSubjectType.FINDING.value
        review = active.get((_FINDING_REVIEW, fid, f.control_id))
        scope = active.get((DecisionType.SCOPE_LIMITATION.value, fid, f.control_id))
        tod, toe, result = _effective_conclusions(f, review)
        draft = FindingConclusions(
            tod_conclusion=str(f.tod_conclusion),
            toe_conclusion=str(f.toe_conclusion),
            result=str(f.result),
            preliminary_deficiency=bool(f.preliminary_deficiency),
        )
        effective = FindingConclusions(
            tod_conclusion=tod,
            toe_conclusion=toe,
            result=result,
            preliminary_deficiency=bool(f.preliminary_deficiency)
            and result == FindingResult.EXCEPTION,
        )
        status = "not_reviewed"
        if review is not None:
            status = (
                "challenged"
                if review.decision_type == DecisionType.CHALLENGE
                else "signed_off"
            )
        views.append(
            FindingView(
                control_id=f.control_id,
                key_control=keys.get(f.control_id),
                draft=draft,
                effective=effective,
                review_status=status,
                review=DecisionRef.of(review),
                scope_limitation=DecisionRef.of(scope),
                differs_from_draft=draft != effective,
                review_required_for_gate_2=policy.finding_needs_review(
                    draft.result, keys.get(f.control_id)
                ),
            )
        )
    return views


def _deficiency_views(
    ctx: DecisionContext, active: dict[tuple[str, str, str], ReviewDecision]
) -> list[DeficiencyView]:
    views: list[DeficiencyView] = []
    seen: set[str] = set()
    did = DecisionSubjectType.DEFICIENCY.value
    for e in ctx.report.deficiency_evaluations if ctx.report else []:
        if e.deficiency_id in seen:
            continue
        seen.add(e.deficiency_id)
        classify = active.get((DecisionType.CLASSIFY.value, did, e.deficiency_id))
        draft = Classification(**_draft_classification(e))
        effective = Classification(**classify.values) if classify else draft
        views.append(
            DeficiencyView(
                deficiency_id=e.deficiency_id,
                title=e.title,
                related_findings=list(e.related_findings),
                draft=draft,
                effective=effective,
                classification_source="reviewer" if classify else "ai_draft",
                classification_decision=DecisionRef.of(classify),
                differs_from_draft=draft != effective,
                writeup=DecisionRef.of(
                    active.get((DecisionType.WRITEUP.value, did, e.deficiency_id))
                ),
                management_response=DecisionRef.of(
                    active.get(
                        (DecisionType.MANAGEMENT_RESPONSE.value, did, e.deficiency_id)
                    )
                ),
            )
        )
    return views


def _missing(
    ctx: DecisionContext,
    findings: list[FindingView],
    deficiencies: list[DeficiencyView],
    conclusion: Optional[DecisionRef],
) -> dict[str, list[MissingDecision]]:
    out: dict[str, list[MissingDecision]] = {}
    if ctx.papers is not None:
        gate2: list[MissingDecision] = []
        for f in findings:
            if f.review_required_for_gate_2 and f.review is None:
                reasons = []
                if f.draft.result == FindingResult.EXCEPTION:
                    reasons.append("result is Exception")
                if f.key_control:
                    reasons.append("key control")
                gate2.append(
                    MissingDecision(
                        gate=2,
                        subject_type="finding",
                        subject_id=f.control_id,
                        required=["sign_off", "challenge"],
                        reason="Needs a reviewer sign-off or challenge ("
                        + "; ".join(reasons)
                        + ").",
                    )
                )
        out["2"] = gate2
    if ctx.report is not None:
        gate3: list[MissingDecision] = []
        if policy.GATE_3_CLASSIFY_EVERY_DEFICIENCY:
            for d in deficiencies:
                if d.classification_decision is None:
                    gate3.append(
                        MissingDecision(
                            gate=3,
                            subject_type="deficiency",
                            subject_id=d.deficiency_id,
                            required=["classify"],
                            reason=(
                                "Needs the reviewer's classification (the AI "
                                f"draft proposes {d.draft.classification})."
                            ),
                        )
                    )
        for f in findings:
            if (
                policy.finding_needs_scope_limitation(f.effective.result, f.key_control)
                and f.scope_limitation is None
            ):
                gate3.append(
                    MissingDecision(
                        gate=3,
                        subject_type="finding",
                        subject_id=f.control_id,
                        required=["scope_limitation"],
                        reason=(
                            "Key control not tested: record how it is reported "
                            "as a scope limitation."
                        ),
                    )
                )
        if policy.GATE_3_ENGAGEMENT_CONCLUSION and conclusion is None:
            gate3.append(
                MissingDecision(
                    gate=3,
                    subject_type="engagement",
                    subject_id=ENGAGEMENT_SUBJECT_ID,
                    required=["engagement_conclusion"],
                    reason=(
                        "Needs the engagement conclusion ("
                        + " / ".join(policy.ENGAGEMENT_CONCLUSION_SCALE)
                        + ")."
                    ),
                )
            )
        out["3"] = gate3
    return out


def effective_view(
    ctx: DecisionContext,
    decisions: Sequence[ReviewDecision],
    *,
    decisions_required: bool = True,
) -> EffectiveView:
    """The conclusion of record for every subject (see module docstring)."""
    states = decision_states(ctx, decisions)
    active = active_by_slot(ctx, decisions)
    findings = _finding_views(ctx, active)
    deficiencies = _deficiency_views(ctx, active)
    conclusion = DecisionRef.of(
        active.get(
            (
                DecisionType.ENGAGEMENT_CONCLUSION.value,
                DecisionSubjectType.ENGAGEMENT.value,
                ENGAGEMENT_SUBJECT_ID,
            )
        )
    )
    decided = sum(1 for f in findings if f.review) + sum(
        1 for d in deficiencies if d.classification_decision
    )
    changed = sum(1 for f in findings if f.review_status == "challenged") + sum(
        1 for d in deficiencies if d.classification_decision and d.differs_from_draft
    )
    return EffectiveView(
        decisions_required=decisions_required,
        deficiency_scale=(
            str(ctx.report.deficiency_scale)
            if ctx.report and ctx.report.deficiency_scale
            else None
        ),
        findings=findings,
        deficiencies=deficiencies,
        engagement_conclusion=conclusion,
        missing_for_gate=_missing(ctx, findings, deficiencies, conclusion),
        reviewer_change_rate=ReviewerChangeRate(
            subjects_decided=decided,
            subjects_changed=changed,
            rate=(changed / decided) if decided else None,
        ),
        stale_decision_ids=[i for i, s in states.items() if s == "stale"],
        superseded_decision_ids=[i for i, s in states.items() if s == "superseded"],
    )

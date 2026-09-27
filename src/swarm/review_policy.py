"""
Review policy: segregation of duties (SoD) between preparer and reviewers.

By default identities here are *declared* by the caller (the API has one
shared bearer token, so it cannot tell users apart). These checks then stop
honest mistakes and make a self-review visible; they do not stop someone who
types a different name. With per-reviewer tokens configured
(``REVIEWER_TOKENS_FILE``, ADR-012) the API passes the name a reviewer's
token belongs to instead, so the same name comparisons stop a reviewer from
acting under another reviewer's name — but not someone who can issue
tokens. Adjust the policy by editing the constants below; every check goes
through :func:`sod_violation`.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

# Reviewer actions the preparer of the audit may not perform on their own work.
PREPARER_EXCLUDED_ACTIONS = frozenset(
    {"gate_approval", "qa_override", "return_for_rework"}
)

# gate → gates whose approver must be a different person. Default: the final
# report (Gate 3) is signed off by someone other than whoever approved the
# fieldwork (Gate 2) — manager / in-charge separation.
DISTINCT_APPROVER_GATES: dict[int, tuple[int, ...]] = {3: (2,)}

GATE_LABELS = {
    1: "Gate 1 (Planning)",
    2: "Gate 2 (Fieldwork)",
    3: "Gate 3 (Reporting)",
}


class ReviewBlockedError(Exception):
    """A review action is refused by policy (maps to HTTP 409)."""


class SegregationOfDutiesError(ReviewBlockedError):
    """The declared identity may not perform this review action."""


class UnverifiedEvidenceError(ReviewBlockedError):
    """Gate 2 cannot be approved while evidence quotes are unverified."""


def normalise_identity(value: Optional[str]) -> str:
    """Case- and whitespace-insensitive form of a declared identity."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).casefold()


def same_person(a: Optional[str], b: Optional[str]) -> bool:
    na, nb = normalise_identity(a), normalise_identity(b)
    return bool(na) and na == nb


def gate_approver(trail: Iterable[Mapping[str, Any]], gate: int) -> Optional[str]:
    """Declared identity of the most recent approval of ``gate``, if any."""
    label = GATE_LABELS[gate]
    approver = None
    for e in trail:
        if e.get("action") == "gate_approval" and e.get("gate") == label:
            approver = e.get("human")
    return approver


def sod_violation(
    action: str,
    human_id: str,
    prepared_by: Optional[str],
    trail: Iterable[Mapping[str, Any]],
    gate: Optional[int] = None,
) -> Optional[str]:
    """Return why ``human_id`` may not perform ``action``, or None if allowed.

    Rules:
      1. The preparer may not approve a gate, override a QA rejection or
         return work for rework (``PREPARER_EXCLUDED_ACTIONS``). Sessions
         created before ``prepared_by`` existed have no preparer, so this
         rule cannot apply to them.
      2. For a gate approval, the approver must differ from the approver of
         each gate listed in ``DISTINCT_APPROVER_GATES[gate]``.
    """
    if action in PREPARER_EXCLUDED_ACTIONS and same_person(human_id, prepared_by):
        return (
            f"Segregation of duties: '{human_id}' prepared this audit and cannot "
            f"perform '{action}' on it. A different reviewer must act."
        )
    if action == "gate_approval" and gate is not None:
        trail = list(trail)
        for other in DISTINCT_APPROVER_GATES.get(gate, ()):
            if same_person(human_id, gate_approver(trail, other)):
                return (
                    f"Segregation of duties: '{human_id}' approved "
                    f"{GATE_LABELS[other]}, so {GATE_LABELS[gate]} must be "
                    "approved by someone else."
                )
    return None


# ── Reviewer decisions (DECISIONS.md, ADR-011) ──────────────────────────────
#
# Every value below is a PROVISIONAL default awaiting the owner's
# confirmation; ADR-011 lists each one with its alternative. Decision types
# and statuses are plain strings here so this module stays free of schema
# imports.


class MissingReviewDecisionsError(ReviewBlockedError):
    """A gate cannot be approved until required reviewer decisions exist.

    ``missing`` lists what is outstanding, one dict per subject:
    ``{"gate", "subject_type", "subject_id", "required", "reason"}``.
    """

    def __init__(self, message: str, missing: list[dict[str, Any]]):
        super().__init__(message)
        self.missing = missing


# Default 12: a decision records the identity as typed by the caller and says
# so, unless per-reviewer tokens are configured (ADR-012), in which case the
# API records the token's name as "authenticated".
DEFAULT_IDENTITY_SOURCE = "declared"

# Decision types the preparer of the audit may not record on it: they are the
# reviewer's judgement. The preparer may transcribe a management response and
# draft a five-part write-up (the reviewer reviews it; it does not by itself
# satisfy any gate precondition).
PREPARER_EXCLUDED_DECISIONS = frozenset(
    {
        "sign_off",
        "challenge",
        "classify",
        "scope_limitation",
        "engagement_conclusion",
    }
)

# Statuses in which each decision type may be recorded (defaults 5, 7, 11).
# A decision belongs to the gate that is open when it is recorded, so a gate
# approval seals that phase's decisions; after COMPLETED only management
# responses may be added (they arrive after the report is issued).
DECISION_RECORDABLE_STATUSES: dict[str, frozenset[str]] = {
    "sign_off": frozenset({"WAITING_HUMAN_GATE_2"}),
    "challenge": frozenset({"WAITING_HUMAN_GATE_2"}),
    "classify": frozenset({"WAITING_HUMAN_GATE_3"}),
    "scope_limitation": frozenset({"WAITING_HUMAN_GATE_3"}),
    "engagement_conclusion": frozenset({"WAITING_HUMAN_GATE_3"}),
    "writeup": frozenset({"WAITING_HUMAN_GATE_3"}),
    "management_response": frozenset({"WAITING_HUMAN_GATE_3", "COMPLETED"}),
}

# Decision types that always need a rationale: a challenge says why the
# reviewer disagrees, a scope limitation is reported in the rationale's words,
# and an engagement conclusion is a judgement that must be explained. Others
# need one only when they differ from the draft (classify) or record a
# disagreement (management_response, default 8).
RATIONALE_ALWAYS_REQUIRED = frozenset(
    {"challenge", "scope_limitation", "engagement_conclusion"}
)

# Default 3: the only change to a ToD / ToE conclusion a reviewer may make
# without returning the phase for rework is withdrawing a positive
# conclusion ("Effective" -> "Not tested"). A negative conclusion
# ("Ineffective", "Exceptions noted") can never be withdrawn this way: that
# would remove an exception without new work.
CONCLUSION_DOWNGRADE_TARGET = "Not tested"
CONCLUSION_DOWNGRADE_FROM = frozenset({"Effective"})

# Default 4: findings that need a sign-off or challenge before Gate 2.
GATE_2_REVIEW_EXCEPTIONS = True
GATE_2_REVIEW_KEY_CONTROLS = True

# Default 5: every deficiency evaluation needs a classification decision
# before Gate 3.
GATE_3_CLASSIFY_EVERY_DEFICIENCY = True
# Default 6: an engagement conclusion is required before Gate 3.
GATE_3_ENGAGEMENT_CONCLUSION = True
# Scale of the engagement conclusion (values of schema.EngagementRating).
ENGAGEMENT_CONCLUSION_SCALE = ("Satisfactory", "Needs improvement", "Unsatisfactory")
# A key control left "Not tested" must be reported as a scope limitation
# before Gate 3 (the only alternative, extending testing, needs Gate 2 to be
# returned for rework before it is approved).
GATE_3_SCOPE_LIMITATION_FOR_UNTESTED_KEY_CONTROLS = True

# Default 10: renderers show the AI draft next to the conclusion of record
# when they differ.
SHOW_AI_DRAFT_WHEN_DIFFERENT = True

# Default 13: the reviewer change rate is computed and exposed per session,
# but it is not a published metric.
REVIEWER_CHANGE_RATE_PUBLISHED = False


def decision_sod_violation(
    decision_type: str, human_id: str, prepared_by: Optional[str]
) -> Optional[str]:
    """Why ``human_id`` may not record ``decision_type``, or None."""
    if decision_type in PREPARER_EXCLUDED_DECISIONS and same_person(
        human_id, prepared_by
    ):
        return (
            f"Segregation of duties: '{human_id}' prepared this audit and cannot "
            f"record a '{decision_type}' decision on it. A reviewer must."
        )
    return None


def decision_status_violation(decision_type: str, status: str) -> Optional[str]:
    """Why a ``decision_type`` decision cannot be recorded at ``status``."""
    allowed = DECISION_RECORDABLE_STATUSES.get(decision_type)
    if allowed is None:
        return f"Unknown decision type '{decision_type}'."
    if status in allowed:
        return None
    return (
        f"A '{decision_type}' decision can only be recorded while the audit is "
        f"{' or '.join(sorted(allowed))}; it is {status}."
    )


def conclusion_change_allowed(draft: str, new: str) -> bool:
    """Whether a reviewer may change a ToD / ToE conclusion without rework."""
    if new == draft:
        return True
    return new == CONCLUSION_DOWNGRADE_TARGET and draft in CONCLUSION_DOWNGRADE_FROM


def finding_needs_review(result: Optional[str], key_control: Optional[bool]) -> bool:
    """Whether a finding needs a sign-off or challenge before Gate 2."""
    return (GATE_2_REVIEW_EXCEPTIONS and result == "Exception") or bool(
        GATE_2_REVIEW_KEY_CONTROLS and key_control
    )


def finding_needs_scope_limitation(
    effective_result: Optional[str], key_control: Optional[bool]
) -> bool:
    """Whether a finding needs a scope-limitation decision before Gate 3."""
    return bool(
        GATE_3_SCOPE_LIMITATION_FOR_UNTESTED_KEY_CONTROLS
        and key_control
        and effective_result == "Not tested"
    )


def classification_differs(draft: Mapping[str, Any], new: Mapping[str, Any]) -> bool:
    """Default 2: a classification needs a rationale when it differs from the
    AI draft in classification, likelihood or magnitude."""
    return any(
        str(new.get(k) or "") != str(draft.get(k) or "")
        for k in ("classification", "likelihood", "magnitude")
    )


def management_response_needs_rebuttal(agreement: str) -> bool:
    """Default 8: management disagreement needs the auditor's rebuttal."""
    return agreement == "disagree"


def management_response_needs_action_plan(agreement: str) -> bool:
    """An agreed (or partly agreed) response needs an owner and a date
    (GIAS 15.1: the person responsible and a planned completion date)."""
    return agreement in ("agree", "partial")

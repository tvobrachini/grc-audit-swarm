"""
Review policy: segregation of duties (SoD) between preparer and reviewers.

Identities here are *declared* by the caller (the API has one shared bearer
token, so it cannot tell users apart). These checks stop honest mistakes and
make a self-review visible; they do not stop someone who types a different
name. Adjust the policy by editing the constants below; every check goes
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

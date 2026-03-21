from enum import StrEnum
from typing import TypeAlias


class ExecutionStatus(StrEnum):
    PENDING = "pending"
    EXECUTING = "executing"
    AWAITING_REVIEW = "awaiting_review"
    CLEAN = "clean"
    FLAGGED = "flagged"
    BLOCKED_BY_GUARDRAIL = "blocked_by_guardrail"


class ReviewDecision(StrEnum):
    REVISE = "revise"
    EXECUTE = "execute"
    RERUN = "rerun"
    END = "end"
    PROCEED_TO_HUMAN = "proceed_to_human"


ExecutionStatusValue: TypeAlias = ExecutionStatus
ReviewDecisionValue: TypeAlias = ReviewDecision

from enum import StrEnum
from typing import TypeAlias


class WorkflowNode(StrEnum):
    ORCHESTRATOR = "orchestrator"
    RESEARCHER = "researcher"
    CONTROL_MAPPER = "control_mapper"
    DYNAMIC_SPECIALISTS = "dynamic_specialists"
    CHALLENGER = "challenger"
    HUMAN_REVIEW = "human_review"
    EVIDENCE_COLLECTOR = "evidence_collector"
    RUN_ALL_WORKERS = "run_all_workers"
    PHASE2_SPECIALIST = "phase2_specialist"
    PHASE2_RESEARCHER = "phase2_researcher"
    PHASE2_CHALLENGER = "phase2_challenger"
    CONCLUDER = "concluder"
    HUMAN_REVIEW_EXECUTION = "human_review_execution"


class ViewPhase(StrEnum):
    SCOPE_INPUT = "scope_input"
    PHASE1 = "phase1"
    PHASE1_REVIEW = "phase1_review"
    PHASE2 = "phase2"
    PHASE2_REVIEW = "phase2_review"
    COMPLETE = "complete"


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


WorkflowNodeValue: TypeAlias = WorkflowNode
ViewPhaseValue: TypeAlias = ViewPhase
ExecutionStatusValue: TypeAlias = ExecutionStatus
ReviewDecisionValue: TypeAlias = ReviewDecision

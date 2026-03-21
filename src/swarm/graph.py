import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from langgraph.graph import END, StateGraph

from swarm.agents.challenger import challenge_execution_findings, challenger_review
from swarm.agents.concluder import produce_executive_summary
from swarm.agents.evidence_collector import run_evidence_collector_sync
from swarm.agents.mapper import map_controls_and_design_tests
from swarm.agents.orchestrator import analyze_scope_and_themes
from swarm.agents.researcher import generate_risk_context, research_failed_controls
from swarm.agents.specialist import (
    annotate_findings_with_specialist,
    inject_specialist_tests,
)
from swarm.agents.worker import build_worker_adapter, run_control_test
from swarm.auth.permissions import PermissionDeniedError, validate_execution_permissions
from swarm.state.schema import AuditFinding, AuditState
from swarm.storage import get_checkpointer
from swarm.workflow_types import ExecutionStatus, ReviewDecision, WorkflowNode

logger = logging.getLogger(__name__)

# Maximum autonomous Challenger→Mapper revisions before escalating to human
MAX_REVISIONS = 2

workflow = StateGraph(AuditState)


# ── Phase 1: Planning Nodes ───────────────────────────────────────────────────
def orchestrator_node(state: AuditState) -> dict:
    return analyze_scope_and_themes(state)


def researcher_node(state: AuditState) -> dict:
    return generate_risk_context(state)


def control_mapper_node(state: AuditState) -> dict:
    return map_controls_and_design_tests(state)


def dynamic_specialist_node(state: AuditState) -> dict:
    return inject_specialist_tests(state)


def challenger_node(state: AuditState) -> dict:
    result = challenger_review(state)
    if result.get("challenger_feedback"):
        result["revision_count"] = state.revision_count + 1
    else:
        # Approved — reset counters so a future re-run starts fresh
        result["revision_count"] = 0
        result["challenger_feedback"] = ""
    return result


def human_review_node(state: AuditState) -> dict:
    """Phase 1 breakpoint: pauses for human review of the planning artifacts."""
    return {}


def should_revise(state: AuditState) -> str:
    """Route after Challenger: loop back to Mapper, or escalate to human."""
    if state.challenger_feedback and state.revision_count < MAX_REVISIONS:
        logger.info(
            "[Graph] Challenger requested revision (attempt %d/%d)",
            state.revision_count,
            MAX_REVISIONS,
        )
        return ReviewDecision.REVISE

    if state.revision_count >= MAX_REVISIONS and state.challenger_feedback:
        logger.info(
            "[Graph] Max revisions (%d) reached — escalating to human review.",
            MAX_REVISIONS,
        )
        return ReviewDecision.PROCEED_TO_HUMAN

    return ReviewDecision.PROCEED_TO_HUMAN


def human_should_approve_phase1(state: AuditState) -> str:
    """Route after Human Phase 1 review: re-run researcher on feedback, else execute."""
    if state.revision_feedback:
        return ReviewDecision.REVISE
    return ReviewDecision.EXECUTE


# ── Phase 2: Execution Nodes ──────────────────────────────────────────────────
def _execute_control(
    adapter, control, state: AuditState, human_ctx: str
) -> AuditFinding:
    """Run a single control test with permission guardrail. Thread-safe."""
    try:
        validate_execution_permissions({"role": "IT_AUDITOR"}, control.control_id)
    except PermissionDeniedError as exc:
        logger.warning("Permission blocked for %s: %s", control.control_id, exc)
        return AuditFinding(
            control_id=control.control_id,
            agent_role="Permission Guardrail",
            status="Fail",
            justification=f"Execution blocked by DDD Identity Context Guardrail: {exc}",
            evidence_extracted=[str(exc)],
            risk_rating="High",
            tod_result="Fail",
            toe_result="Fail",
            substantive_result="Fail",
        )
    return adapter.run(control, state, human_context=human_ctx)


def run_all_workers_node(state: AuditState) -> dict:
    """
    Executes audit tests for all controls in the matrix.
    Builds the worker adapter once, then runs controls in parallel
    using a thread pool (max 3 concurrent LLM calls).
    """
    logger.info(
        "[Execution] Running workers for %d controls...", len(state.control_matrix)
    )
    existing_findings = {f.control_id: f for f in state.testing_findings}
    status_map = dict(state.execution_status)

    # Separate controls that need re-testing from those that can be preserved
    to_run: list[tuple[int, object, str]] = []  # (original_index, control, human_ctx)
    preserved: dict[int, AuditFinding] = {}

    for idx, control in enumerate(state.control_matrix):
        cid = control.control_id
        human_ctx = state.control_feedback.get(cid, "").strip()
        needs_run = not state.testing_findings or bool(human_ctx)

        if not needs_run and cid in existing_findings:
            preserved[idx] = existing_findings[cid]
            status_map.setdefault(cid, ExecutionStatus.AWAITING_REVIEW)
            logger.info("  ↺ %s: preserved prior finding", cid)
        else:
            status_map[cid] = ExecutionStatus.EXECUTING
            to_run.append((idx, control, human_ctx))

    # Build adapter once — reused across all parallel workers
    adapter = build_worker_adapter(state)

    results: dict[int, AuditFinding] = dict(preserved)

    if to_run:
        max_workers = min(3, len(to_run))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_map = {
                pool.submit(_execute_control, adapter, control, state, human_ctx): (
                    idx,
                    control,
                )
                for idx, control, human_ctx in to_run
            }
            for future in as_completed(future_map):
                idx, control = future_map[future]
                try:
                    finding = future.result()
                except Exception as exc:
                    logger.error(
                        "[Execution] Worker raised for %s: %s", control.control_id, exc
                    )
                    finding = run_control_test(control, state)  # serial fallback
                results[idx] = finding
                status_map[control.control_id] = (
                    ExecutionStatus.BLOCKED_BY_GUARDRAIL
                    if finding.agent_role == "Permission Guardrail"
                    else ExecutionStatus.AWAITING_REVIEW
                )
                logger.info("  ✓ %s: %s", control.control_id, finding.status)

    # Reconstruct findings in original control_matrix order
    findings = [results[i] for i in range(len(state.control_matrix))]

    passes = sum(1 for f in findings if f.status == "Pass")
    exceptions = sum(1 for f in findings if f.status == "Exception")
    fails = sum(1 for f in findings if f.status == "Fail")

    return {
        "testing_findings": findings,
        "execution_status": status_map,
        "audit_trail": state.audit_trail
        + [
            {
                "agent_or_user_id": "Execution Engine",
                "action_taken": f"Completed {len(findings)} control tests.",
                "reasoning_snapshot": f"{passes} Pass / {exceptions} Exception / {fails} Fail",
                "approval_status": "Pending Human Review",
            }
        ],
    }


def evidence_collector_node(state: AuditState) -> dict:
    return run_evidence_collector_sync(state)


def phase2_specialist_node(state: AuditState) -> dict:
    return annotate_findings_with_specialist(state)


def phase2_researcher_node(state: AuditState) -> dict:
    return research_failed_controls(state)


def phase2_challenger_node(state: AuditState) -> dict:
    return challenge_execution_findings(state)


def concluder_node(state: AuditState) -> dict:
    return produce_executive_summary(state)


def human_review_execution_node(state: AuditState) -> dict:
    """Phase 2 breakpoint: pauses for human review of findings."""
    return {}


def human_should_approve_phase2(state: AuditState) -> str:
    has_feedback = any(v.strip() for v in state.control_feedback.values())
    if has_feedback:
        return ReviewDecision.RERUN
    return ReviewDecision.END


# ── Register nodes ─────────────────────────────────────────────────────────────
workflow.add_node(WorkflowNode.ORCHESTRATOR, orchestrator_node)
workflow.add_node(WorkflowNode.RESEARCHER, researcher_node)
workflow.add_node(WorkflowNode.CONTROL_MAPPER, control_mapper_node)
workflow.add_node(WorkflowNode.DYNAMIC_SPECIALISTS, dynamic_specialist_node)
workflow.add_node(WorkflowNode.CHALLENGER, challenger_node)
workflow.add_node(WorkflowNode.HUMAN_REVIEW, human_review_node)
workflow.add_node(WorkflowNode.EVIDENCE_COLLECTOR, evidence_collector_node)
workflow.add_node(WorkflowNode.RUN_ALL_WORKERS, run_all_workers_node)
workflow.add_node(WorkflowNode.PHASE2_SPECIALIST, phase2_specialist_node)
workflow.add_node(WorkflowNode.PHASE2_RESEARCHER, phase2_researcher_node)
workflow.add_node(WorkflowNode.PHASE2_CHALLENGER, phase2_challenger_node)
workflow.add_node(WorkflowNode.CONCLUDER, concluder_node)
workflow.add_node(WorkflowNode.HUMAN_REVIEW_EXECUTION, human_review_execution_node)

# ── Phase 1 Edges ─────────────────────────────────────────────────────────────
workflow.set_entry_point(WorkflowNode.ORCHESTRATOR)
workflow.add_edge(WorkflowNode.ORCHESTRATOR, WorkflowNode.RESEARCHER)
workflow.add_edge(WorkflowNode.RESEARCHER, WorkflowNode.CONTROL_MAPPER)
workflow.add_edge(WorkflowNode.CONTROL_MAPPER, WorkflowNode.DYNAMIC_SPECIALISTS)
workflow.add_edge(WorkflowNode.DYNAMIC_SPECIALISTS, WorkflowNode.CHALLENGER)

# Challenger → Mapper (on QA rejection) or → Human (approved / max revisions)
workflow.add_conditional_edges(
    WorkflowNode.CHALLENGER,
    should_revise,
    {
        ReviewDecision.REVISE: WorkflowNode.CONTROL_MAPPER,  # skip re-search; fix the matrix
        ReviewDecision.PROCEED_TO_HUMAN: WorkflowNode.HUMAN_REVIEW,
    },
)

# Human Phase 1 → Researcher (on substantive feedback) or → Evidence Collector (approved)
workflow.add_conditional_edges(
    WorkflowNode.HUMAN_REVIEW,
    human_should_approve_phase1,
    {
        ReviewDecision.REVISE: WorkflowNode.RESEARCHER,
        ReviewDecision.EXECUTE: WorkflowNode.EVIDENCE_COLLECTOR,
    },
)

# ── Phase 2 Edges ─────────────────────────────────────────────────────────────
workflow.add_edge(WorkflowNode.EVIDENCE_COLLECTOR, WorkflowNode.RUN_ALL_WORKERS)
workflow.add_edge(WorkflowNode.RUN_ALL_WORKERS, WorkflowNode.PHASE2_SPECIALIST)
workflow.add_edge(WorkflowNode.PHASE2_SPECIALIST, WorkflowNode.PHASE2_RESEARCHER)
workflow.add_edge(WorkflowNode.PHASE2_RESEARCHER, WorkflowNode.PHASE2_CHALLENGER)
workflow.add_edge(WorkflowNode.PHASE2_CHALLENGER, WorkflowNode.CONCLUDER)
workflow.add_edge(WorkflowNode.CONCLUDER, WorkflowNode.HUMAN_REVIEW_EXECUTION)
workflow.add_conditional_edges(
    WorkflowNode.HUMAN_REVIEW_EXECUTION,
    human_should_approve_phase2,
    {ReviewDecision.RERUN: WorkflowNode.RUN_ALL_WORKERS, ReviewDecision.END: END},
)

memory = get_checkpointer()

app = workflow.compile(
    checkpointer=memory,
    interrupt_before=[
        WorkflowNode.HUMAN_REVIEW,
        WorkflowNode.HUMAN_REVIEW_EXECUTION,
    ],
)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("--- Swarm Architecture Graph Compiled Successfully ---")

    initial_state = {
        "audit_scope_narrative": "We are migrating to AWS EKS and need an audit.",
        "audit_trail": [],
    }

    logger.info("--- Running Phase 1 ---")
    final_state = app.invoke(
        initial_state, config={"configurable": {"thread_id": "test_phase_1"}}
    )

    logger.info("--- Paused after Phase 1 ---")

    app.update_state(
        config={"configurable": {"thread_id": "test_phase_1"}},
        values={"revision_feedback": ""},
    )

    logger.info("--- Running Phase 2 ---")
    final_state_phase2 = app.invoke(
        None, config={"configurable": {"thread_id": "test_phase_1"}}
    )

    logger.info("--- Final Output State (Phase 2) ---")
    logger.info(final_state_phase2.get("testing_findings", "No findings generated."))

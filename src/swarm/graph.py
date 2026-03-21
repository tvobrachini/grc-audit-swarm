import logging

from langgraph.graph import StateGraph, END
from swarm.agents.challenger import challenger_review, challenge_execution_findings
from swarm.agents.concluder import produce_executive_summary
from swarm.agents.evidence_collector import run_evidence_collector_sync
from swarm.agents.mapper import map_controls_and_design_tests
from swarm.agents.orchestrator import analyze_scope_and_themes
from swarm.agents.researcher import generate_risk_context, research_failed_controls
from swarm.agents.specialist import (
    annotate_findings_with_specialist,
    inject_specialist_tests,
)
from swarm.agents.worker import run_control_test
from swarm.auth.permissions import (
    PermissionDeniedError,
    validate_execution_permissions,
)
from swarm.state.schema import AuditFinding, AuditState
from swarm.storage import get_checkpointer
from swarm.workflow_types import ExecutionStatus, ReviewDecision, WorkflowNode

logger = logging.getLogger(__name__)

# Maximum autonomous revisions before escalating to human review
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
    # If the challenger provides feedback, increment the revision counter
    if result.get("revision_feedback"):
        result["revision_count"] = state.revision_count + 1
    return result


def human_review_node(state: AuditState) -> dict:
    """Phase 1 breakpoint: pauses for human review of the planning artifacts."""
    return {}


def should_revise(state: AuditState) -> str:
    if state.revision_feedback != "" and state.revision_count < MAX_REVISIONS:
        logger.info(
            "[Graph] Challenger requested revision (Attempt %d/%d)",
            state.revision_count,
            MAX_REVISIONS,
        )
        return ReviewDecision.REVISE

    if state.revision_count >= MAX_REVISIONS and state.revision_feedback != "":
        logger.info(
            "[Graph] Max revisions (%d) reached. Proceeding to human review for final judgment.",
            MAX_REVISIONS,
        )
        # Clear feedback so human starts fresh
        return ReviewDecision.PROCEED_TO_HUMAN

    return ReviewDecision.PROCEED_TO_HUMAN


def human_should_approve_phase1(state: AuditState) -> str:
    if state.revision_feedback != "":
        return ReviewDecision.REVISE
    return ReviewDecision.EXECUTE


# ── Phase 2: Execution Nodes ──────────────────────────────────────────────────
def run_all_workers_node(state: AuditState) -> dict:
    """
    Executes audit tests for all controls in the matrix.
    Runs Workers sequentially — one per control.
    """
    logger.info(
        "[Execution] Running workers for %d controls...", len(state.control_matrix)
    )
    existing_findings = {
        finding.control_id: finding for finding in state.testing_findings
    }
    findings = []
    status_map = dict(state.execution_status)

    for control in state.control_matrix:
        cid = control.control_id
        human_ctx = state.control_feedback.get(cid, "").strip()
        should_rerun = not state.testing_findings or bool(human_ctx)

        if not should_rerun and cid in existing_findings:
            findings.append(existing_findings[cid])
            status_map.setdefault(cid, ExecutionStatus.AWAITING_REVIEW)
            logger.info("  ↺ %s: preserved prior finding", cid)
            continue

        status_map[cid] = ExecutionStatus.EXECUTING

        # DDD Identity/Permissions Guardrail Execution Check
        try:
            # We mock the user_context here as an active auditor.
            validate_execution_permissions({"role": "IT_AUDITOR"}, cid)
        except PermissionDeniedError as e:
            logger.warning("Permission blocked for control %s: %s", cid, e)
            findings.append(
                AuditFinding(
                    control_id=cid,
                    agent_role="Permission Guardrail",
                    status="Fail",
                    justification=f"Execution blocked by DDD Identity Context Guardrail: {e}",
                    evidence_extracted=[str(e)],
                    risk_rating="High",
                    tod_result="Fail",
                    toe_result="Fail",
                    substantive_result="Fail",
                )
            )
            status_map[cid] = ExecutionStatus.BLOCKED_BY_GUARDRAIL
            continue

        finding = run_control_test(control, state, human_context=human_ctx)
        findings.append(finding)
        status_map[cid] = ExecutionStatus.AWAITING_REVIEW
        logger.info("  ✓ %s: %s", cid, finding.status)

    return {
        "testing_findings": findings,
        "execution_status": status_map,
        "audit_trail": state.audit_trail
        + [
            {
                "agent_or_user_id": "Execution Engine",
                "action_taken": f"Completed {len(findings)} control tests.",
                "reasoning_snapshot": f"{sum(1 for f in findings if f.status == 'Pass')} Pass / {sum(1 for f in findings if f.status == 'Exception')} Exception / {sum(1 for f in findings if f.status == 'Fail')} Fail",
                "approval_status": "Pending Human Review",
            }
        ],
    }


def evidence_collector_node(state: AuditState) -> dict:
    return run_evidence_collector_sync(state)


def concluder_node(state: AuditState) -> dict:
    return produce_executive_summary(state)


def human_review_execution_node(state: AuditState) -> dict:
    """Phase 2 breakpoint: pauses for human review of findings."""
    return {}


def human_should_approve_phase2(state: AuditState) -> str:
    # Check if any control has feedback that needs re-running
    has_feedback = any(v.strip() for v in state.control_feedback.values())
    if has_feedback:
        return ReviewDecision.RERUN
    return ReviewDecision.END


# ── Add nodes ─────────────────────────────────────────────────────────────────
workflow.add_node(WorkflowNode.ORCHESTRATOR, orchestrator_node)
workflow.add_node(WorkflowNode.RESEARCHER, researcher_node)
workflow.add_node(WorkflowNode.CONTROL_MAPPER, control_mapper_node)
workflow.add_node(WorkflowNode.DYNAMIC_SPECIALISTS, dynamic_specialist_node)
workflow.add_node(WorkflowNode.CHALLENGER, challenger_node)
workflow.add_node(WorkflowNode.HUMAN_REVIEW, human_review_node)
workflow.add_node(WorkflowNode.EVIDENCE_COLLECTOR, evidence_collector_node)
workflow.add_node(WorkflowNode.RUN_ALL_WORKERS, run_all_workers_node)
# Phase 2 review pipeline nodes
workflow.add_node(
    WorkflowNode.PHASE2_SPECIALIST, lambda s: annotate_findings_with_specialist(s)
)
workflow.add_node(WorkflowNode.PHASE2_RESEARCHER, lambda s: research_failed_controls(s))
workflow.add_node(
    WorkflowNode.PHASE2_CHALLENGER, lambda s: challenge_execution_findings(s)
)
workflow.add_node(WorkflowNode.CONCLUDER, concluder_node)
workflow.add_node(WorkflowNode.HUMAN_REVIEW_EXECUTION, human_review_execution_node)

# ── Phase 1 Edges ─────────────────────────────────────────────────────────────
workflow.set_entry_point(WorkflowNode.ORCHESTRATOR)
workflow.add_edge(WorkflowNode.ORCHESTRATOR, WorkflowNode.RESEARCHER)
workflow.add_edge(WorkflowNode.RESEARCHER, WorkflowNode.CONTROL_MAPPER)
workflow.add_edge(WorkflowNode.CONTROL_MAPPER, WorkflowNode.DYNAMIC_SPECIALISTS)
workflow.add_edge(WorkflowNode.DYNAMIC_SPECIALISTS, WorkflowNode.CHALLENGER)
workflow.add_conditional_edges(
    WorkflowNode.CHALLENGER,
    should_revise,
    {
        ReviewDecision.REVISE: WorkflowNode.RESEARCHER,
        ReviewDecision.PROCEED_TO_HUMAN: WorkflowNode.HUMAN_REVIEW,
    },
)
workflow.add_conditional_edges(
    WorkflowNode.HUMAN_REVIEW,
    human_should_approve_phase1,
    {
        ReviewDecision.REVISE: WorkflowNode.RESEARCHER,
        ReviewDecision.EXECUTE: WorkflowNode.EVIDENCE_COLLECTOR,
    },
)

workflow.add_edge(WorkflowNode.EVIDENCE_COLLECTOR, WorkflowNode.RUN_ALL_WORKERS)

# ── Phase 2 Edges ─────────────────────────────────────────────────────────────
# Workers → Specialist annotation → Researcher breach context → Challenger QA → Concluder → Human
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

    # Test a simple invocation
    initial_state = {
        "audit_scope_narrative": "We are migrating to AWS EKS and need an audit.",
        "audit_trail": [],
    }

    # Run the graph (Phase 1)
    logger.info("--- Running Phase 1 ---")
    final_state = app.invoke(
        initial_state, config={"configurable": {"thread_id": "test_phase_1"}}
    )

    logger.info("--- Paused after Phase 1 ---")

    # Simulate human approval
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

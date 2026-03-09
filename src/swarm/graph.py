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
        return "revise"

    if state.revision_count >= MAX_REVISIONS and state.revision_feedback != "":
        logger.info(
            "[Graph] Max revisions (%d) reached. Proceeding to human review for final judgment.",
            MAX_REVISIONS,
        )
        # Clear feedback so human starts fresh
        return "proceed_to_human"

    return "proceed_to_human"


def human_should_approve_phase1(state: AuditState) -> str:
    if state.revision_feedback != "":
        return "revise"
    return "execute"


# ── Phase 2: Execution Nodes ──────────────────────────────────────────────────
def run_all_workers_node(state: AuditState) -> dict:
    """
    Executes audit tests for all controls in the matrix.
    Runs Workers sequentially — one per control.
    """
    logger.info(
        "[Execution] Running workers for %d controls...", len(state.control_matrix)
    )
    findings = []
    status_map = {}

    for control in state.control_matrix:
        cid = control.control_id
        status_map[cid] = "executing"

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
            status_map[cid] = "blocked_by_guardrail"
            continue

        # Check if human left feedback specifically for this control
        human_ctx = state.control_feedback.get(cid, "")
        finding = run_control_test(control, state, human_context=human_ctx)
        findings.append(finding)
        status_map[cid] = "awaiting_review"
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
        return "rerun"
    return "end"


# ── Add nodes ─────────────────────────────────────────────────────────────────
workflow.add_node("orchestrator", orchestrator_node)
workflow.add_node("researcher", researcher_node)
workflow.add_node("control_mapper", control_mapper_node)
workflow.add_node("dynamic_specialists", dynamic_specialist_node)
workflow.add_node("challenger", challenger_node)
workflow.add_node("human_review", human_review_node)
workflow.add_node("evidence_collector", evidence_collector_node)
workflow.add_node("run_all_workers", run_all_workers_node)
# Phase 2 review pipeline nodes
workflow.add_node("phase2_specialist", lambda s: annotate_findings_with_specialist(s))
workflow.add_node("phase2_researcher", lambda s: research_failed_controls(s))
workflow.add_node("phase2_challenger", lambda s: challenge_execution_findings(s))
workflow.add_node("concluder", concluder_node)
workflow.add_node("human_review_execution", human_review_execution_node)

# ── Phase 1 Edges ─────────────────────────────────────────────────────────────
workflow.set_entry_point("orchestrator")
workflow.add_edge("orchestrator", "researcher")
workflow.add_edge("researcher", "control_mapper")
workflow.add_edge("control_mapper", "dynamic_specialists")
workflow.add_edge("dynamic_specialists", "challenger")
workflow.add_conditional_edges(
    "challenger",
    should_revise,
    {"revise": "researcher", "proceed_to_human": "human_review"},
)
workflow.add_conditional_edges(
    "human_review",
    human_should_approve_phase1,
    {"revise": "researcher", "execute": "evidence_collector"},
)

workflow.add_edge("evidence_collector", "run_all_workers")

# ── Phase 2 Edges ─────────────────────────────────────────────────────────────
# Workers → Specialist annotation → Researcher breach context → Challenger QA → Concluder → Human
workflow.add_edge("run_all_workers", "phase2_specialist")
workflow.add_edge("phase2_specialist", "phase2_researcher")
workflow.add_edge("phase2_researcher", "phase2_challenger")
workflow.add_edge("phase2_challenger", "concluder")
workflow.add_edge("concluder", "human_review_execution")
workflow.add_conditional_edges(
    "human_review_execution",
    human_should_approve_phase2,
    {"rerun": "run_all_workers", "end": END},
)

memory = get_checkpointer()

app = workflow.compile(
    checkpointer=memory, interrupt_before=["human_review", "human_review_execution"]
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

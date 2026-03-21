from typing import Any

from swarm.workflow_types import ViewPhase, WorkflowNode


def derive_app_view_state(current_state: Any) -> dict[str, Any]:
    next_nodes = current_state.next
    state_vals = current_state.values or {}

    current_node = next_nodes[0] if next_nodes else None
    at_phase1_review = bool(current_node == WorkflowNode.HUMAN_REVIEW)
    at_phase2_review = bool(current_node == WorkflowNode.HUMAN_REVIEW_EXECUTION)
    is_fully_done = not next_nodes and bool(state_vals.get("testing_findings"))

    has_matrix = bool(state_vals.get("control_matrix"))
    has_findings = bool(state_vals.get("testing_findings"))
    is_phase1 = not has_matrix or (
        has_matrix and not has_findings and not at_phase2_review
    )

    should_stream = not at_phase1_review and not at_phase2_review and not is_fully_done
    if at_phase1_review:
        view_phase = ViewPhase.PHASE1_REVIEW
    elif at_phase2_review:
        view_phase = ViewPhase.PHASE2_REVIEW
    elif is_fully_done:
        view_phase = ViewPhase.COMPLETE
    elif is_phase1:
        view_phase = ViewPhase.PHASE1
    else:
        view_phase = ViewPhase.PHASE2

    return {
        "next_nodes": next_nodes,
        "current_node": current_node,
        "state_vals": state_vals,
        "view_phase": view_phase,
        "at_phase1_review": at_phase1_review,
        "at_phase2_review": at_phase2_review,
        "is_fully_done": is_fully_done,
        "is_phase1": is_phase1,
        "should_stream": should_stream,
    }

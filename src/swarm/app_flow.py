from typing import Any


def derive_app_view_state(current_state: Any) -> dict[str, Any]:
    next_nodes = current_state.next
    state_vals = current_state.values or {}

    at_phase1_review = bool(next_nodes and next_nodes[0] == "human_review")
    at_phase2_review = bool(next_nodes and next_nodes[0] == "human_review_execution")
    is_fully_done = not next_nodes and bool(state_vals.get("testing_findings"))

    has_matrix = bool(state_vals.get("control_matrix"))
    has_findings = bool(state_vals.get("testing_findings"))
    is_phase1 = not has_matrix or (
        has_matrix and not has_findings and not at_phase2_review
    )

    should_stream = not at_phase1_review and not at_phase2_review and not is_fully_done

    return {
        "next_nodes": next_nodes,
        "state_vals": state_vals,
        "at_phase1_review": at_phase1_review,
        "at_phase2_review": at_phase2_review,
        "is_fully_done": is_fully_done,
        "is_phase1": is_phase1,
        "should_stream": should_stream,
    }


def fresh_session_state(thread_id: str) -> dict[str, Any]:
    return {
        "thread_id": thread_id,
        "chat_history": [],
        "scope_submitted": False,
        "control_feedback": {},
    }

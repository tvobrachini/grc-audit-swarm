from typing import Any, Dict


def merge_state_map(
    current: Dict[str, Any] | None, updates: Dict[str, Any]
) -> Dict[str, Any]:
    merged = dict(current or {})
    merged.update(updates)
    return merged


def mark_control_clean(
    execution_status: Dict[str, str] | None, control_id: str
) -> Dict[str, Dict[str, str]]:
    return {
        "execution_status": merge_state_map(execution_status, {control_id: "clean"})
    }


def flag_control_for_finding(
    execution_status: Dict[str, str] | None, control_id: str
) -> Dict[str, Dict[str, str]]:
    return {
        "execution_status": merge_state_map(execution_status, {control_id: "flagged"})
    }


def request_control_rerun(
    control_feedback: Dict[str, str] | None, control_id: str, feedback: str
) -> Dict[str, Dict[str, str]]:
    return {
        "control_feedback": merge_state_map(control_feedback, {control_id: feedback})
    }


def submit_phase2_feedback(
    control_feedback: Dict[str, str],
) -> Dict[str, Dict[str, str]]:
    return {"control_feedback": dict(control_feedback)}

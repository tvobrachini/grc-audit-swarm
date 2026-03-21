import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm.review_actions import (
    build_phase1_review_patch,
    flag_control_for_finding,
    mark_control_clean,
    merge_state_map,
    request_control_rerun,
    submit_phase2_feedback,
)
from swarm.session_sync import append_chat_message, build_session_update


def test_merge_state_map_preserves_existing_entries():
    merged = merge_state_map({"AC-01": "clean"}, {"LOG-04": "flagged"})
    assert merged == {"AC-01": "clean", "LOG-04": "flagged"}


def test_mark_control_clean_returns_graph_patch():
    patch = mark_control_clean({"AC-01": "awaiting_review"}, "AC-01")
    assert patch == {"execution_status": {"AC-01": "clean"}}


def test_flag_control_for_finding_returns_graph_patch():
    patch = flag_control_for_finding({"AC-01": "clean"}, "VUL-02")
    assert patch == {"execution_status": {"AC-01": "clean", "VUL-02": "flagged"}}


def test_request_control_rerun_merges_feedback():
    patch = request_control_rerun({"AC-01": "existing"}, "LOG-04", "recheck logs")
    assert patch == {
        "control_feedback": {"AC-01": "existing", "LOG-04": "recheck logs"}
    }


def test_submit_phase2_feedback_clones_feedback_map():
    feedback = {"AC-01": "recheck users"}
    patch = submit_phase2_feedback(feedback)
    assert patch == {"control_feedback": {"AC-01": "recheck users"}}
    assert patch["control_feedback"] is not feedback


def test_build_phase1_review_patch_approves_known_inputs():
    assert build_phase1_review_patch("Approve to start execution") == {
        "revision_feedback": ""
    }
    assert build_phase1_review_patch(" LGTM ") == {"revision_feedback": ""}


def test_build_phase1_review_patch_preserves_revision_feedback():
    assert build_phase1_review_patch("Add one more logging control") == {
        "revision_feedback": "Add one more logging control"
    }


def test_append_chat_message_returns_new_history():
    history = [{"role": "user", "content": "start"}]
    updated = append_chat_message(history, "assistant", "done", reasoning="ok")
    assert history == [{"role": "user", "content": "start"}]
    assert updated[-1] == {
        "role": "assistant",
        "content": "done",
        "reasoning": "ok",
    }


def test_build_session_update_contains_scope_and_chat_history():
    payload = build_session_update("scope text", [{"role": "user", "content": "hi"}])
    assert payload == {
        "scope_text": "scope text",
        "chat_history": [{"role": "user", "content": "hi"}],
    }

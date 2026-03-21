import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ui.components.state import build_session_reset


def test_build_session_reset_restores_core_defaults():
    updates, clear_keys = build_session_reset("thread-123", [])

    assert updates == {
        "thread_id": "thread-123",
        "chat_history": [],
        "scope_submitted": False,
        "scope_text_cache": "",
        "control_feedback": {},
        "suggested_audit_name": "",
        "resume_swarm": False,
    }
    assert clear_keys == []


def test_build_session_reset_clears_transient_widget_keys():
    updates, clear_keys = build_session_reset(
        "thread-123",
        [
            "scope_up",
            "scope_lab",
            "scope_ta",
            "audit_name",
            "status_filter",
            "fb_AC-01",
            "fb_LOG-04",
            "thread_id",
        ],
    )

    assert updates["thread_id"] == "thread-123"
    assert sorted(clear_keys) == sorted(
        [
            "scope_up",
            "scope_lab",
            "scope_ta",
            "audit_name",
            "status_filter",
            "fb_AC-01",
            "fb_LOG-04",
        ]
    )

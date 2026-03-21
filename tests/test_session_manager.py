import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm import session_manager


def test_save_session_persists_scope_and_chat_history(tmp_path, monkeypatch):
    sessions_path = tmp_path / "audit_sessions.json"
    monkeypatch.setattr(session_manager, "SESSIONS_PATH", str(sessions_path))

    chat_history = [{"role": "user", "content": "Loaded scope"}]
    session_manager.save_session(
        "thread-1",
        "AWS Audit",
        "Full scope text for the audit.",
        chat_history,
    )

    saved = session_manager.get_session("thread-1")

    assert saved["name"] == "AWS Audit"
    assert saved["scope_text"] == "Full scope text for the audit."
    assert saved["scope_preview"] == "Full scope text for the audit."
    assert saved["chat_history"] == chat_history


def test_update_session_merges_fields_without_dropping_existing_data(
    tmp_path, monkeypatch
):
    sessions_path = tmp_path / "audit_sessions.json"
    monkeypatch.setattr(session_manager, "SESSIONS_PATH", str(sessions_path))

    session_manager.save_session("thread-1", "AWS Audit", "Scope text")
    session_manager.update_session(
        "thread-1",
        chat_history=[{"role": "assistant", "content": "Node complete"}],
    )

    saved = session_manager.get_session("thread-1")

    assert saved["name"] == "AWS Audit"
    assert saved["scope_text"] == "Scope text"
    assert saved["chat_history"] == [{"role": "assistant", "content": "Node complete"}]

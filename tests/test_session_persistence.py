"""
Tests for swarm.session_manager and FlowRepository persistence guarantees:
corrupt files are backed up (never silently wiped), writes are atomic,
saves preserve unrelated fields, and a save after delete never resurrects.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm import session_manager
from swarm.audit_flow import AuditFlow
from swarm.state.repository import FlowRepository


@pytest.fixture
def sessions_path(tmp_path, monkeypatch):
    path = tmp_path / "audit_sessions.json"
    monkeypatch.setattr(session_manager, "SESSIONS_PATH", str(path))
    return path


class TestCorruptFile:
    def test_corrupt_json_is_backed_up_not_wiped(self, sessions_path, caplog):
        original = '{"sess-1": {"name": "Important audit"'  # truncated JSON
        sessions_path.write_text(original, encoding="utf-8")

        with caplog.at_level("ERROR"):
            assert session_manager.list_sessions() == {}

        backups = list(sessions_path.parent.glob("audit_sessions.json.corrupt-*"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == original
        assert "corrupt" in caplog.text

        # The next save starts fresh without destroying the backup.
        session_manager.save_session("sess-2", "New audit", "scope")
        assert set(session_manager.list_sessions()) == {"sess-2"}
        assert backups[0].read_text(encoding="utf-8") == original

    def test_non_object_json_is_treated_as_corrupt(self, sessions_path):
        sessions_path.write_text("[1, 2, 3]", encoding="utf-8")
        assert session_manager.get_session("anything") is None
        assert list(sessions_path.parent.glob("audit_sessions.json.corrupt-*"))

    def test_missing_file_returns_empty_without_backup(self, sessions_path):
        assert session_manager.list_sessions() == {}
        assert not list(sessions_path.parent.glob("*.corrupt-*"))


class TestAtomicWrite:
    def test_failed_write_keeps_previous_file_and_no_temp_files(
        self, sessions_path, monkeypatch
    ):
        session_manager.save_session("sess-1", "Audit", "scope")
        before = sessions_path.read_text(encoding="utf-8")

        def boom(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(session_manager.os, "replace", boom)
        with pytest.raises(OSError):
            session_manager.update_session("sess-1", status="RUNNING_PHASE_2")

        assert sessions_path.read_text(encoding="utf-8") == before
        assert not list(sessions_path.parent.glob("*.tmp"))


class TestFieldPreservation:
    def test_save_session_preserves_status_ui_phase_and_snapshot(self, sessions_path):
        session_manager.save_session("sess-1", "Audit", "scope")
        session_manager.update_session(
            "sess-1",
            status="WAITING_HUMAN_GATE_1",
            ui_phase=1,
            state_snapshot={"status": "WAITING_HUMAN_GATE_1"},
        )

        session_manager.save_session("sess-1", "Renamed audit", "new scope")

        saved = session_manager.get_session("sess-1")
        assert saved["name"] == "Renamed audit"
        assert saved["scope_text"] == "new scope"
        assert saved["status"] == "WAITING_HUMAN_GATE_1"
        assert saved["ui_phase"] == 1
        assert saved["state_snapshot"] == {"status": "WAITING_HUMAN_GATE_1"}

    def test_save_session_accepts_extra_fields_in_one_write(self, sessions_path):
        session_manager.save_session(
            "sess-1", "Audit", "scope", status="RUNNING_PHASE_1", created_at="T0"
        )
        saved = session_manager.get_session("sess-1")
        assert saved["status"] == "RUNNING_PHASE_1"
        assert saved["created_at"] == "T0"

    def test_update_session_on_unknown_id_is_a_noop(self, sessions_path):
        assert session_manager.update_session("ghost", status="X") is False
        assert session_manager.get_session("ghost") is None


class TestFlowRepositorySave:
    def _flow(self) -> AuditFlow:
        flow = AuditFlow(initial_status="WAITING_HUMAN_GATE_1")
        flow.state.theme = "IAM"
        flow.state.business_context = "Fintech IAM review"
        return flow

    def test_save_writes_snapshot_and_status_preserving_metadata(
        self, sessions_path
    ):
        session_manager.save_session(
            "sess-1", "My audit", "old scope", created_at="2026-01-01T00:00:00"
        )
        session_manager.update_session("sess-1", ui_phase=1)

        assert FlowRepository().save("sess-1", self._flow()) is True

        saved = session_manager.get_session("sess-1")
        assert saved["name"] == "My audit"
        assert saved["created_at"] == "2026-01-01T00:00:00"
        assert saved["ui_phase"] == 1
        assert saved["status"] == "WAITING_HUMAN_GATE_1"
        assert saved["scope_text"] == "Fintech IAM review"
        assert saved["state_snapshot"]["status"] == "WAITING_HUMAN_GATE_1"
        # The file on disk is valid JSON (snapshot serialised in json mode).
        json.loads(sessions_path.read_text(encoding="utf-8"))

    def test_save_after_delete_does_not_resurrect_session(self, sessions_path):
        session_manager.save_session("sess-1", "My audit", "scope")
        session_manager.delete_session("sess-1")

        assert FlowRepository().save("sess-1", self._flow()) is False
        assert session_manager.get_session("sess-1") is None
        assert FlowRepository().load("sess-1") is None

    def test_round_trip_restores_machine_status(self, sessions_path):
        session_manager.save_session("sess-1", "My audit", "scope")
        FlowRepository().save("sess-1", self._flow())

        loaded = FlowRepository().load("sess-1")
        assert loaded is not None
        assert loaded.flow.machine.status.value == "WAITING_HUMAN_GATE_1"
        assert loaded.flow.state.status == "WAITING_HUMAN_GATE_1"
        assert loaded.flow.state.theme == "IAM"

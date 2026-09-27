"""Hash-chained approval trail: what verification detects and what it does not."""

import base64
import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm import session_manager
from swarm import trail as t

KEY = base64.urlsafe_b64encode(b"k" * 32).decode()
OTHER_KEY = base64.urlsafe_b64encode(b"z" * 32).decode()


@pytest.fixture(autouse=True)
def _no_key(monkeypatch):
    monkeypatch.delenv("VAULT_ENCRYPTION_KEY", raising=False)


def _entry(i: int) -> dict:
    return {
        "gate": f"Gate {i}",
        "human": f"person-{i}",
        "timestamp": f"2026-01-0{i}T00:00:00+00:00",
        "action": "gate_approval",
    }


def _chain(n: int = 4) -> list[dict]:
    trail: list[dict] = []
    for i in range(1, n + 1):
        t.append_entry(trail, _entry(i))
    return trail


def _recompute_unkeyed(trail: list[dict]) -> list[dict]:
    """What an editor without the key can do: rebuild a sha256 chain."""
    rebuilt: list[dict] = []
    for e in trail:
        t.append_entry(rebuilt, {k: v for k, v in e.items() if k not in t._HASH_FIELDS})
    return rebuilt


class TestUnkeyedChain:
    def test_intact_chain_verifies(self):
        trail = _chain()
        result = t.verify_trail(trail)
        assert result["ok"] is True
        assert result["status"] == "ok"
        assert result["entries"] == 4
        assert result["keyed"] is False
        assert trail[0]["prev_hash"] == t.GENESIS_HASH
        assert all(e["hash_alg"] == "sha256" for e in trail)
        assert trail[2]["prev_hash"] == trail[1]["entry_hash"]

    def test_empty_trail_is_ok(self):
        assert t.verify_trail([])["status"] == "ok"

    def test_edit_detected(self):
        trail = _chain()
        trail[1]["human"] = "someone-else"
        result = t.verify_trail(trail)
        assert result["ok"] is False
        assert result["status"] == "broken"
        assert result["first_broken_index"] == 1

    def test_added_field_detected(self):
        trail = _chain()
        trail[2]["reason"] = "added later"
        assert t.verify_trail(trail)["first_broken_index"] == 2

    def test_reorder_detected(self):
        trail = _chain()
        trail[1], trail[2] = trail[2], trail[1]
        result = t.verify_trail(trail)
        assert result["status"] == "broken"
        assert result["first_broken_index"] == 1

    def test_middle_deletion_detected(self):
        trail = _chain()
        del trail[1]
        result = t.verify_trail(trail)
        assert result["status"] == "broken"
        assert result["first_broken_index"] == 1

    def test_first_entry_deletion_detected(self):
        trail = _chain()
        del trail[0]
        assert t.verify_trail(trail)["first_broken_index"] == 0

    def test_inserted_unchained_entry_detected(self):
        trail = _chain()
        trail.insert(2, _entry(9))
        assert t.verify_trail(trail)["first_broken_index"] == 2

    def test_tail_truncation_not_detected_without_anchor(self):
        # Documented limitation: the remaining prefix is a valid chain.
        trail = _chain()
        del trail[-1]
        assert t.verify_trail(trail)["ok"] is True

    def test_full_recompute_not_detected_without_key(self):
        # Documented limitation of the unkeyed variant.
        trail = _chain()
        trail[1]["human"] = "forged"
        assert t.verify_trail(_recompute_unkeyed(trail))["ok"] is True

    def test_canonical_form_ignores_key_order(self):
        trail = _chain(2)
        trail[1] = dict(reversed(list(trail[1].items())))
        assert t.verify_trail(trail)["ok"] is True


class TestAnchor:
    def test_anchor_detects_tail_truncation(self):
        trail = _chain()
        count, head = t.head(trail)
        del trail[-1]
        result = t.verify_trail(trail, anchor={"count": count, "head_hash": head})
        assert result["status"] == "truncated"
        assert result["first_broken_index"] == 3

    def test_anchor_detects_rewritten_chain(self):
        trail = _chain()
        count, head = t.head(trail)
        trail[3]["human"] = "forged"
        rebuilt = _recompute_unkeyed(trail)
        result = t.verify_trail(rebuilt, anchor={"count": count, "head_hash": head})
        assert result["status"] == "broken"
        assert result["first_broken_index"] == 3

    def test_anchor_detects_stripped_hashes(self):
        trail = _chain()
        count, head = t.head(trail)
        stripped = [
            {k: v for k, v in e.items() if k not in t._HASH_FIELDS} for e in trail
        ]
        result = t.verify_trail(stripped, anchor={"count": count, "head_hash": head})
        assert result["ok"] is False
        assert result["status"] == "broken"

    def test_anchor_detects_emptied_trail(self):
        count, head = t.head(_chain())
        result = t.verify_trail([], anchor={"count": count, "head_hash": head})
        assert result["status"] == "truncated"

    def test_anchor_behind_head_is_a_checkpoint(self):
        trail = _chain(2)
        count, head = t.head(trail)
        t.append_entry(trail, _entry(3))
        result = t.verify_trail(trail, anchor={"count": count, "head_hash": head})
        assert result["ok"] is True
        assert result["anchored"] is True


class TestKeyedChain:
    def test_keyed_chain_verifies(self, monkeypatch):
        monkeypatch.setenv("VAULT_ENCRYPTION_KEY", KEY)
        trail = _chain()
        assert all(e["hash_alg"] == "hmac-sha256" for e in trail)
        result = t.verify_trail(trail)
        assert result["ok"] is True
        assert result["keyed"] is True

    def test_trail_key_differs_from_vault_key(self, monkeypatch):
        from swarm.evidence import _HMAC_KEY_LABEL, derive_labelled_key

        monkeypatch.setenv("VAULT_ENCRYPTION_KEY", KEY)
        assert derive_labelled_key(t.TRAIL_KEY_LABEL) != derive_labelled_key(
            _HMAC_KEY_LABEL
        )

    def test_edit_reorder_delete_detected(self, monkeypatch):
        monkeypatch.setenv("VAULT_ENCRYPTION_KEY", KEY)
        base = _chain()
        edited = copy.deepcopy(base)
        edited[0]["human"] = "x"
        reordered = copy.deepcopy(base)
        reordered[1], reordered[2] = reordered[2], reordered[1]
        deleted = copy.deepcopy(base)
        del deleted[2]
        assert t.verify_trail(edited)["first_broken_index"] == 0
        assert t.verify_trail(reordered)["first_broken_index"] == 1
        assert t.verify_trail(deleted)["first_broken_index"] == 2

    def test_recompute_without_key_detected(self, monkeypatch):
        # The editor has the file but not the key: the best they can do is a
        # sha256 chain, which is reported, not accepted.
        monkeypatch.setenv("VAULT_ENCRYPTION_KEY", KEY)
        trail = _chain()
        trail[1]["human"] = "forged"
        monkeypatch.delenv("VAULT_ENCRYPTION_KEY")
        rebuilt = _recompute_unkeyed(trail)
        monkeypatch.setenv("VAULT_ENCRYPTION_KEY", KEY)
        result = t.verify_trail(rebuilt)
        assert result["ok"] is False
        assert result["status"] == "unkeyed"

    def test_recompute_with_wrong_key_detected(self, monkeypatch):
        monkeypatch.setenv("VAULT_ENCRYPTION_KEY", KEY)
        trail = _chain()
        trail[1]["human"] = "forged"
        monkeypatch.setenv("VAULT_ENCRYPTION_KEY", OTHER_KEY)
        rebuilt = _recompute_unkeyed(trail)
        monkeypatch.setenv("VAULT_ENCRYPTION_KEY", KEY)
        result = t.verify_trail(rebuilt)
        assert result["status"] == "broken"
        assert result["first_broken_index"] == 0

    def test_forged_unkeyed_tail_entry_detected(self, monkeypatch):
        monkeypatch.setenv("VAULT_ENCRYPTION_KEY", KEY)
        trail = _chain()
        monkeypatch.delenv("VAULT_ENCRYPTION_KEY")
        t.append_entry(trail, _entry(5))  # appended without the key
        monkeypatch.setenv("VAULT_ENCRYPTION_KEY", KEY)
        assert t.verify_trail(trail)["status"] == "unkeyed"

    def test_unkeyed_prefix_sealed_by_keyed_entries(self, monkeypatch):
        trail = _chain(2)  # written before a key was configured
        monkeypatch.setenv("VAULT_ENCRYPTION_KEY", KEY)
        t.append_entry(trail, _entry(3))
        assert t.verify_trail(trail)["ok"] is True
        trail[0]["human"] = "forged"
        assert t.verify_trail(trail)["first_broken_index"] == 0

    def test_keyed_trail_without_key_is_unverifiable(self, monkeypatch):
        monkeypatch.setenv("VAULT_ENCRYPTION_KEY", KEY)
        trail = _chain()
        monkeypatch.delenv("VAULT_ENCRYPTION_KEY")
        result = t.verify_trail(trail)
        assert result["ok"] is False
        assert result["status"] == "key_unavailable"


class TestLegacyTrail:
    def test_unchained_trail_reports_legacy(self):
        legacy = [_entry(1), _entry(2)]
        result = t.verify_trail(legacy)
        assert result["ok"] is False
        assert result["status"] == "legacy_unchained"
        assert "legacy" in result["detail"].lower()
        assert result["first_broken_index"] is None

    def test_new_entries_seal_the_legacy_prefix(self):
        trail = [_entry(1), _entry(2)]
        t.append_entry(trail, _entry(3))
        assert trail[2]["prev_hash"] != t.GENESIS_HASH
        result = t.verify_trail(trail)
        assert result["ok"] is True
        assert result["legacy_entries"] == 2
        trail[1]["human"] = "forged"
        assert t.verify_trail(trail)["first_broken_index"] == 2


class TestArtifactDigest:
    def test_changed_artifact_reported(self):
        trail: list[dict] = []
        racm = {"theme": "S3", "risks": []}
        t.append_entry(
            trail,
            {
                **_entry(1),
                "gate": "Gate 1 (Planning)",
                "artifact": "racm_plan",
                "artifact_digest": t.artifact_digest(racm),
            },
        )
        ok = t.verify_trail(trail, artifacts={"racm_plan": racm})
        assert ok["status"] == "ok"
        changed = t.verify_trail(
            trail, artifacts={"racm_plan": {"theme": "S3 (edited)", "risks": []}}
        )
        assert changed["ok"] is False
        assert changed["status"] == "artifact_changed"
        assert changed["changed_since_approval"] == ["Gate 1 (Planning)"]
        missing = t.verify_trail(trail, artifacts={"racm_plan": None})
        assert missing["status"] == "artifact_changed"


class TestAnchorStore:
    @pytest.fixture(autouse=True)
    def _paths(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            session_manager, "SESSIONS_PATH", str(tmp_path / "sessions.json")
        )
        monkeypatch.delenv("TRAIL_ANCHORS_PATH", raising=False)

    def test_anchor_saved_next_to_sessions_file(self, tmp_path):
        assert session_manager.save_trail_anchor("s1", 2, "abc") is True
        assert (tmp_path / "trail_anchors.json").exists()
        assert session_manager.get_trail_anchor("s1")["count"] == 2

    def test_anchor_never_moves_backwards(self):
        session_manager.save_trail_anchor("s1", 3, "abc")
        assert session_manager.save_trail_anchor("s1", 2, "def") is False
        assert session_manager.get_trail_anchor("s1")["head_hash"] == "abc"

    def test_separate_path_and_delete(self, tmp_path, monkeypatch):
        path = tmp_path / "elsewhere" / "anchors.json"
        monkeypatch.setenv("TRAIL_ANCHORS_PATH", str(path))
        session_manager.save_session("s1", "n", "ctx")
        session_manager.save_trail_anchor("s1", 1, "abc")
        assert path.exists()
        session_manager.delete_session("s1")
        assert session_manager.get_trail_anchor("s1") is None

    def test_unreadable_anchor_file_reads_as_none(self, tmp_path):
        (tmp_path / "trail_anchors.json").write_text("{not json")
        assert session_manager.get_trail_anchor("s1") is None

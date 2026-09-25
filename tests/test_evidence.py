"""
Tests for EvidenceAssuranceProtocol and supporting utilities.
Covers: account ID redaction, configurable vault path, SHA-256 hashing,
        path-traversal protection, and verify_exact_quote.
"""

import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm.evidence import EvidenceAssuranceProtocol, _redact_account_ids


# ─── Account ID Redaction ─────────────────────────────────────────────────────


class TestAccountIdRedaction:
    def test_standalone_account_id_is_redacted(self):
        text = '{"Account": "123456789012"}'
        assert "123456789012" not in _redact_account_ids(text)
        assert "[REDACTED]" in _redact_account_ids(text)

    def test_multiple_account_ids_all_redacted(self):
        text = "owner: 123456789012 delegated: 987654321098"
        result = _redact_account_ids(text)
        assert "123456789012" not in result
        assert "987654321098" not in result
        assert result.count("[REDACTED]") == 2

    def test_non_twelve_digit_numbers_not_redacted(self):
        text = "port 8080 or id 1234567890123"  # 13-digit — too long
        result = _redact_account_ids(text)
        assert "8080" in result
        assert "1234567890123" in result

    def test_eleven_digit_number_not_redacted(self):
        result = _redact_account_ids("ref 12345678901")
        assert "12345678901" in result

    def test_account_id_embedded_in_arn_is_redacted(self):
        arn = "arn:aws:iam::123456789012:user/alice"
        result = _redact_account_ids(arn)
        assert "123456789012" not in result
        assert "[REDACTED]" in result

    def test_text_without_account_ids_unchanged(self):
        text = '{"PasswordPolicy": {"MinimumPasswordLength": 14}}'
        assert _redact_account_ids(text) == text


# ─── EvidenceAssuranceProtocol ───────────────────────────────────────────────


class TestEvidenceRegistration:
    def test_register_returns_vault_id_and_sha256(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        result = EvidenceAssuranceProtocol.register_evidence(
            '{"key": "value"}', "test.operation"
        )
        assert "vault_id" in result
        assert len(result["sha256"]) == 64

    def test_vault_file_written_to_custom_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        result = EvidenceAssuranceProtocol.register_evidence("payload", "op")
        expected = tmp_path / f"{result['vault_id']}.json"
        assert expected.exists()

    def test_vault_file_contains_correct_fields(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        result = EvidenceAssuranceProtocol.register_evidence("hello", "src.op")
        with open(tmp_path / f"{result['vault_id']}.json") as f:
            record = json.load(f)
        assert record["mcp_source"] == "src.op"
        assert record["raw_payload"] == "hello"
        assert record["sha256"] == result["sha256"]

    def test_account_ids_stripped_from_stored_payload(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        raw = '{"Account": "123456789012", "Region": "us-east-1"}'
        result = EvidenceAssuranceProtocol.register_evidence(raw, "op")
        with open(tmp_path / f"{result['vault_id']}.json") as f:
            record = json.load(f)
        assert "123456789012" not in record["raw_payload"]
        assert "[REDACTED]" in record["raw_payload"]

    def test_sha256_matches_sanitized_payload(self, tmp_path, monkeypatch):
        import hashlib

        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        raw = "Account: 123456789012 data here"
        result = EvidenceAssuranceProtocol.register_evidence(raw, "op")
        sanitized = raw.replace("123456789012", "[REDACTED]")
        expected_hash = hashlib.sha256(sanitized.encode()).hexdigest()
        assert result["sha256"] == expected_hash

    def test_each_registration_gets_unique_vault_id(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        r1 = EvidenceAssuranceProtocol.register_evidence("a", "op")
        r2 = EvidenceAssuranceProtocol.register_evidence("a", "op")
        assert r1["vault_id"] != r2["vault_id"]


class TestVerifyExactQuote:
    def test_exact_quote_present_returns_true(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        result = EvidenceAssuranceProtocol.register_evidence(
            "MinimumPasswordLength: 14", "op"
        )
        assert EvidenceAssuranceProtocol.verify_exact_quote(
            result["vault_id"], "MinimumPasswordLength: 14"
        )

    def test_missing_quote_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        result = EvidenceAssuranceProtocol.register_evidence("real payload", "op")
        assert not EvidenceAssuranceProtocol.verify_exact_quote(
            result["vault_id"], "hallucinated content"
        )

    def test_nonexistent_vault_id_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        assert not EvidenceAssuranceProtocol.verify_exact_quote(
            "00000000-0000-0000-0000-000000000000", "anything"
        )

    def test_path_traversal_attempt_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        assert not EvidenceAssuranceProtocol.verify_exact_quote(
            "../../../etc/passwd", "root"
        )

    def test_sibling_prefix_traversal_attempt_returns_false(
        self, tmp_path, monkeypatch
    ):
        vault = tmp_path / "vault"
        sibling = tmp_path / "vault_backup"
        vault.mkdir()
        sibling.mkdir()
        with open(sibling / "leaked.json", "w") as f:
            json.dump(
                {
                    "vault_id": "leaked",
                    "sha256": "unused",
                    "mcp_source": "test",
                    "timestamp": "2026-05-02T00:00:00Z",
                    "raw_payload": "secret marker",
                    "encrypted": False,
                },
                f,
            )

        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(vault))
        assert not EvidenceAssuranceProtocol.verify_exact_quote(
            "../vault_backup/leaked", "secret marker"
        )

    def test_encrypted_vault_verify_works_with_key(self, tmp_path, monkeypatch):
        import base64
        import os as _os
        from swarm.evidence import _build_fernet

        _build_fernet.cache_clear()
        key = base64.urlsafe_b64encode(_os.urandom(32)).decode()
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        monkeypatch.setenv("VAULT_ENCRYPTION_KEY", key)
        result = EvidenceAssuranceProtocol.register_evidence("secret payload", "op")
        # Vault file should NOT contain plaintext
        import json as _json

        with open(tmp_path / f"{result['vault_id']}.json") as f:
            record = _json.load(f)
        assert "secret payload" not in record["raw_payload"]
        assert record["encrypted"] is True
        # Verification still works
        assert EvidenceAssuranceProtocol.verify_exact_quote(
            result["vault_id"], "secret payload"
        )

    def test_encrypted_vault_verify_fails_without_key(self, tmp_path, monkeypatch):
        import base64
        import os as _os
        from swarm.evidence import _build_fernet

        _build_fernet.cache_clear()
        key = base64.urlsafe_b64encode(_os.urandom(32)).decode()
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        monkeypatch.setenv("VAULT_ENCRYPTION_KEY", key)
        result = EvidenceAssuranceProtocol.register_evidence("secret", "op")
        monkeypatch.delenv("VAULT_ENCRYPTION_KEY")
        _build_fernet.cache_clear()
        assert not EvidenceAssuranceProtocol.verify_exact_quote(
            result["vault_id"], "secret"
        )

    def test_partial_quote_returns_true(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        result = EvidenceAssuranceProtocol.register_evidence(
            '{"Users": [{"UserName": "alice", "MFA_Enabled": "No"}]}', "op"
        )
        assert EvidenceAssuranceProtocol.verify_exact_quote(
            result["vault_id"], '"UserName": "alice"'
        )


# ─── Wave 0 hardening: hyphenated account IDs + weak-quote rejection ─────────
# Appended in a self-contained block.


class TestHyphenatedAccountIdRedaction:
    def test_hyphenated_account_id_is_redacted(self):
        text = "Account: 1234-5678-9012"
        result = _redact_account_ids(text)
        assert "1234-5678-9012" not in result
        assert "[REDACTED]" in result

    def test_hyphenated_account_id_in_sentence_is_redacted(self):
        text = "The AWS account 1234-5678-9012 owns this resource."
        result = _redact_account_ids(text)
        assert "1234-5678-9012" not in result

    def test_bare_and_hyphenated_ids_both_redacted(self):
        text = "old: 123456789012 new: 1234-5678-9012"
        result = _redact_account_ids(text)
        assert result.count("[REDACTED]") == 2


class TestVerifyExactQuoteWeakInput:
    def test_empty_quote_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        result = EvidenceAssuranceProtocol.register_evidence(
            "some evidence payload here", "op"
        )
        assert not EvidenceAssuranceProtocol.verify_exact_quote(result["vault_id"], "")

    def test_whitespace_only_quote_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        result = EvidenceAssuranceProtocol.register_evidence(
            "some evidence payload here", "op"
        )
        assert not EvidenceAssuranceProtocol.verify_exact_quote(
            result["vault_id"], "    "
        )

    def test_too_short_quote_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        result = EvidenceAssuranceProtocol.register_evidence(
            "some evidence payload here", "op"
        )
        # "evidence" is 8 chars (the minimum); anything shorter is rejected
        # even though it is technically a substring of the payload.
        assert not EvidenceAssuranceProtocol.verify_exact_quote(
            result["vault_id"], "eviden"
        )

    def test_minimum_length_quote_still_works(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        result = EvidenceAssuranceProtocol.register_evidence(
            "some evidence payload here", "op"
        )
        assert EvidenceAssuranceProtocol.verify_exact_quote(
            result["vault_id"], "evidence"
        )


class TestEncryptedVaultKeyedDigest:
    """With encryption on, the stored digest must be keyed, so a guessable
    payload can't be confirmed from the vault file without the key."""

    @staticmethod
    def _enable_encryption(tmp_path, monkeypatch):
        import base64
        import os as _os

        from swarm.evidence import _build_fernet

        _build_fernet.cache_clear()
        key = base64.urlsafe_b64encode(_os.urandom(32)).decode()
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        monkeypatch.setenv("VAULT_ENCRYPTION_KEY", key)
        return key

    @staticmethod
    def _read(tmp_path, vault_id):
        with open(tmp_path / f"{vault_id}.json") as f:
            return json.load(f)

    def test_encrypted_record_stores_hmac_not_plain_sha256(self, tmp_path, monkeypatch):
        self._enable_encryption(tmp_path, monkeypatch)
        payload = '{"MinimumPasswordLength": 14}'
        result = EvidenceAssuranceProtocol.register_evidence(payload, "op")

        record = self._read(tmp_path, result["vault_id"])
        plain_hash = hashlib.sha256(payload.encode()).hexdigest()
        assert "sha256" not in record
        assert len(record["hmac_sha256"]) == 64
        assert plain_hash not in json.dumps(record)
        assert result["hmac_sha256"] == record["hmac_sha256"]

    def test_hmac_depends_on_key(self, tmp_path, monkeypatch):
        from swarm.evidence import _keyed_digest

        key_a = self._enable_encryption(tmp_path, monkeypatch)
        key_b = self._enable_encryption(tmp_path, monkeypatch)
        assert _keyed_digest("same payload", key_a) != _keyed_digest(
            "same payload", key_b
        )

    def test_encrypted_record_verifies(self, tmp_path, monkeypatch):
        self._enable_encryption(tmp_path, monkeypatch)
        result = EvidenceAssuranceProtocol.register_evidence(
            "MFA enabled for user alice", "op"
        )
        assert EvidenceAssuranceProtocol.verify_exact_quote(
            result["vault_id"], "MFA enabled for user alice"
        )

    def test_tampered_hmac_fails_verification(self, tmp_path, monkeypatch):
        self._enable_encryption(tmp_path, monkeypatch)
        result = EvidenceAssuranceProtocol.register_evidence(
            "MFA enabled for user alice", "op"
        )
        path = tmp_path / f"{result['vault_id']}.json"
        record = self._read(tmp_path, result["vault_id"])
        record["hmac_sha256"] = "0" * 64
        path.write_text(json.dumps(record))
        assert not EvidenceAssuranceProtocol.verify_exact_quote(
            result["vault_id"], "MFA enabled for user alice"
        )

    def test_legacy_encrypted_record_with_plain_sha256_still_verifies(
        self, tmp_path, monkeypatch
    ):
        """Records written before the keyed digest existed keep verifying."""
        import uuid

        from swarm.evidence import _get_fernet

        self._enable_encryption(tmp_path, monkeypatch)
        payload = "legacy evidence payload"
        vault_id = str(uuid.uuid4())
        (tmp_path / f"{vault_id}.json").write_text(
            json.dumps(
                {
                    "vault_id": vault_id,
                    "sha256": hashlib.sha256(payload.encode()).hexdigest(),
                    "mcp_source": "op",
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "raw_payload": _get_fernet()
                    .encrypt(payload.encode())
                    .decode("ascii"),
                    "encrypted": True,
                }
            )
        )
        assert EvidenceAssuranceProtocol.verify_exact_quote(vault_id, payload)

    def test_unencrypted_record_still_uses_plain_sha256(self, tmp_path, monkeypatch):
        monkeypatch.delenv("VAULT_ENCRYPTION_KEY", raising=False)
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        result = EvidenceAssuranceProtocol.register_evidence("plain payload", "op")
        record = self._read(tmp_path, result["vault_id"])
        assert "hmac_sha256" not in record
        assert record["sha256"] == hashlib.sha256("plain payload".encode()).hexdigest()


class TestMigrateLegacyDigests:
    """migrate_legacy_digests() re-seals old encrypted records with the HMAC."""

    @staticmethod
    def _enable_encryption(tmp_path, monkeypatch):
        import base64
        import os as _os

        from swarm.evidence import _build_fernet

        _build_fernet.cache_clear()
        key = base64.urlsafe_b64encode(_os.urandom(32)).decode()
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        monkeypatch.setenv("VAULT_ENCRYPTION_KEY", key)
        return key

    @staticmethod
    def _write_legacy_record(tmp_path, payload, stored_sha256=None):
        import uuid

        from swarm.evidence import _get_fernet

        vault_id = str(uuid.uuid4())
        (tmp_path / f"{vault_id}.json").write_text(
            json.dumps(
                {
                    "vault_id": vault_id,
                    "sha256": stored_sha256
                    or hashlib.sha256(payload.encode()).hexdigest(),
                    "mcp_source": "op",
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "raw_payload": _get_fernet()
                    .encrypt(payload.encode())
                    .decode("ascii"),
                    "encrypted": True,
                }
            )
        )
        return vault_id

    @staticmethod
    def _read(tmp_path, vault_id):
        with open(tmp_path / f"{vault_id}.json") as f:
            return json.load(f)

    def test_legacy_record_is_rekeyed_and_plain_hash_removed(
        self, tmp_path, monkeypatch
    ):
        from swarm.evidence import _keyed_digest

        key = self._enable_encryption(tmp_path, monkeypatch)
        payload = '{"MinimumPasswordLength": 14}'
        vault_id = self._write_legacy_record(tmp_path, payload)

        counts = EvidenceAssuranceProtocol.migrate_legacy_digests()

        record = self._read(tmp_path, vault_id)
        assert counts["migrated"] == 1
        assert "sha256" not in record
        assert record["hmac_sha256"] == _keyed_digest(payload, key)
        assert hashlib.sha256(payload.encode()).hexdigest() not in json.dumps(record)
        assert EvidenceAssuranceProtocol.verify_exact_quote(vault_id, payload)

    def test_keyed_and_unencrypted_records_are_left_alone(self, tmp_path, monkeypatch):
        self._enable_encryption(tmp_path, monkeypatch)
        keyed = EvidenceAssuranceProtocol.register_evidence("keyed payload", "op")
        monkeypatch.delenv("VAULT_ENCRYPTION_KEY")
        plain = EvidenceAssuranceProtocol.register_evidence("plain payload", "op")
        self._enable_encryption(tmp_path, monkeypatch)
        before_plain = (tmp_path / f"{plain['vault_id']}.json").read_text()

        counts = EvidenceAssuranceProtocol.migrate_legacy_digests()

        assert counts["migrated"] == 0
        assert counts["unencrypted"] == 1
        assert (tmp_path / f"{plain['vault_id']}.json").read_text() == before_plain
        # The keyed record was written under a different key, so it counts as
        # already keyed and is not touched.
        assert counts["already_keyed"] == 1
        assert "hmac_sha256" in self._read(tmp_path, keyed["vault_id"])

    def test_corrupted_record_is_not_resealed(self, tmp_path, monkeypatch):
        self._enable_encryption(tmp_path, monkeypatch)
        vault_id = self._write_legacy_record(
            tmp_path, "real payload", stored_sha256="0" * 64
        )
        before = (tmp_path / f"{vault_id}.json").read_text()

        counts = EvidenceAssuranceProtocol.migrate_legacy_digests()

        assert counts == {
            "migrated": 0,
            "already_keyed": 0,
            "unencrypted": 0,
            "failed": 1,
        }
        assert (tmp_path / f"{vault_id}.json").read_text() == before

    def test_migration_is_idempotent(self, tmp_path, monkeypatch):
        self._enable_encryption(tmp_path, monkeypatch)
        self._write_legacy_record(tmp_path, "payload one")

        first = EvidenceAssuranceProtocol.migrate_legacy_digests()
        second = EvidenceAssuranceProtocol.migrate_legacy_digests()

        assert first["migrated"] == 1
        assert second["migrated"] == 0
        assert second["already_keyed"] == 1
        assert not list(tmp_path.glob("*.tmp"))

    def test_requires_encryption_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        monkeypatch.delenv("VAULT_ENCRYPTION_KEY", raising=False)
        with pytest.raises(RuntimeError, match="VAULT_ENCRYPTION_KEY"):
            EvidenceAssuranceProtocol.migrate_legacy_digests()

    def test_cli_migrates_and_reports_counts(self, tmp_path, monkeypatch):
        import os as _os
        import subprocess

        self._enable_encryption(tmp_path, monkeypatch)
        self._write_legacy_record(tmp_path, "cli payload")
        src_dir = _os.path.join(_os.path.dirname(__file__), "..", "src")
        env = {**_os.environ, "PYTHONPATH": src_dir}

        proc = subprocess.run(  # noqa: S603 - fixed argv, test-only
            [sys.executable, "-m", "swarm.evidence", "migrate-digests"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout.strip().splitlines()[-1])["migrated"] == 1

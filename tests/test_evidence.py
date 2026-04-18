"""
Tests for EvidenceAssuranceProtocol and supporting utilities.
Covers: account ID redaction, configurable vault path, SHA-256 hashing,
        path-traversal protection, and verify_exact_quote.
"""

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

    def test_encrypted_vault_verify_works_with_key(self, tmp_path, monkeypatch):
        import base64, os as _os
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
        assert EvidenceAssuranceProtocol.verify_exact_quote(result["vault_id"], "secret payload")

    def test_encrypted_vault_verify_fails_without_key(self, tmp_path, monkeypatch):
        import base64, os as _os
        key = base64.urlsafe_b64encode(_os.urandom(32)).decode()
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        monkeypatch.setenv("VAULT_ENCRYPTION_KEY", key)
        result = EvidenceAssuranceProtocol.register_evidence("secret", "op")
        monkeypatch.delenv("VAULT_ENCRYPTION_KEY")
        assert not EvidenceAssuranceProtocol.verify_exact_quote(result["vault_id"], "secret")

    def test_partial_quote_returns_true(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        result = EvidenceAssuranceProtocol.register_evidence(
            '{"Users": [{"UserName": "alice", "MFA_Enabled": "No"}]}', "op"
        )
        assert EvidenceAssuranceProtocol.verify_exact_quote(
            result["vault_id"], '"UserName": "alice"'
        )

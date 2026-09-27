"""Tests for POST /api/sessions/{id}/imports/prowler (src/api/routers/imports.py).

Uses the real EvidenceAssuranceProtocol against the temp vault the autouse
conftest fixture points EVIDENCE_VAULT_PATH at, and a real session record in
a temp sessions file — no AuditFlow/crew is built, since this route never
touches flow state.
"""

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm import session_manager  # noqa: E402
from swarm.evidence import EvidenceAssuranceProtocol  # noqa: E402
from swarm.tools.findings_checks import MAX_PROWLER_FILE_BYTES  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "findings"
AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("API_AUTH_TOKEN", "test-token")
    monkeypatch.setattr(
        session_manager, "SESSIONS_PATH", str(tmp_path / "audit_sessions.json")
    )
    session_manager.save_session("sess-1", "Test audit")
    from api.main import app

    return TestClient(app)


def _upload(client, session_id, filename, content, headers=AUTH):
    return client.post(
        f"/api/sessions/{session_id}/imports/prowler",
        headers=headers,
        files={"file": (filename, content, "application/json")},
    )


class TestAuth:
    def test_requires_auth(self, client):
        content = (FIXTURES / "prowler_ocsf_sample.json").read_bytes()
        resp = _upload(client, "sess-1", "prowler.json", content, headers={})
        assert resp.status_code == 401


class TestUnknownSession:
    def test_404_for_unknown_session(self, client):
        content = (FIXTURES / "prowler_ocsf_sample.json").read_bytes()
        resp = _upload(client, "does-not-exist", "prowler.json", content)
        assert resp.status_code == 404


class TestSuccessfulImport:
    def test_registers_and_returns_summary(self, client):
        content = (FIXTURES / "prowler_ocsf_sample.json").read_bytes()
        resp = _upload(client, "sess-1", "prowler.json", content)
        assert resp.status_code == 201
        body = resp.json()
        assert "vault_id" in body
        assert "s3_bucket_level_public_access_block" in body["summary"]
        assert EvidenceAssuranceProtocol.verify_exact_quote(
            body["vault_id"], "s3_bucket_level_public_access_block"
        )

    def test_account_id_redacted(self, client):
        content = (FIXTURES / "prowler_ocsf_sample.json").read_bytes()
        resp = _upload(client, "sess-1", "prowler.json", content)
        assert "123456789012" not in resp.json()["summary"]
        assert "123456789012" not in json.dumps(resp.json())

    def test_metadata_records_session_id(self, client, tmp_path):
        content = (FIXTURES / "prowler_ocsf_sample.json").read_bytes()
        resp = _upload(client, "sess-1", "prowler.json", content)
        vault_id = resp.json()["vault_id"]
        vault_path = Path(os.environ["EVIDENCE_VAULT_PATH"]) / f"{vault_id}.json"
        record = json.loads(vault_path.read_text())
        assert record["metadata"]["parameters"]["session_id"] == "sess-1"
        assert record["metadata"]["parameters"]["source_filename"] == "prowler.json"


class TestRejections:
    def test_non_json_extension_rejected(self, client):
        resp = _upload(client, "sess-1", "prowler.txt", b"[]")
        assert resp.status_code == 422

    def test_oversized_upload_rejected(self, client):
        oversized = b"[" + b"1" * (MAX_PROWLER_FILE_BYTES + 10) + b"]"
        resp = _upload(client, "sess-1", "prowler.json", oversized)
        assert resp.status_code == 413

    def test_malformed_json_rejected(self, client):
        resp = _upload(client, "sess-1", "prowler.json", b"{ not json ]")
        assert resp.status_code == 422

    def test_non_utf8_rejected(self, client):
        resp = _upload(client, "sess-1", "prowler.json", b"\xff\xfe\x00\x01")
        assert resp.status_code == 422

    def test_declared_content_length_over_limit_rejected_before_body_read(self, client):
        # The middleware in api/main.py rejects on the declared Content-Length
        # before the multipart body is even parsed.
        huge_len = MAX_PROWLER_FILE_BYTES + (300 * 1024)
        resp = client.post(
            "/api/sessions/sess-1/imports/prowler",
            headers={**AUTH, "content-length": str(huge_len)},
            files={"file": ("prowler.json", b"[]", "application/json")},
        )
        assert resp.status_code == 413

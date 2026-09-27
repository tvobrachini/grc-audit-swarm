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


def _upload(
    client, session_id, filename, content, headers=AUTH, uploaded_by="J. Rivera"
):
    return client.post(
        f"/api/sessions/{session_id}/imports/prowler",
        headers=headers,
        data={"uploaded_by": uploaded_by} if uploaded_by is not None else {},
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


def _vault_metadata(vault_id: str) -> dict:
    vault_path = Path(os.environ["EVIDENCE_VAULT_PATH"]) / f"{vault_id}.json"
    return json.loads(vault_path.read_text())["metadata"]


class TestUploaderIdentity:
    """The uploader is required and recorded in the vault record."""

    def test_missing_uploaded_by_rejected(self, client):
        content = (FIXTURES / "prowler_ocsf_sample.json").read_bytes()
        resp = _upload(client, "sess-1", "prowler.json", content, uploaded_by=None)
        assert resp.status_code == 422
        resp = _upload(client, "sess-1", "prowler.json", content, uploaded_by="  ")
        assert resp.status_code == 422

    def test_declared_uploader_recorded(self, client):
        content = (FIXTURES / "prowler_ocsf_sample.json").read_bytes()
        resp = _upload(client, "sess-1", "prowler.json", content)
        assert resp.status_code == 201
        params = _vault_metadata(resp.json()["vault_id"])["parameters"]
        assert params["uploaded_by"] == "J. Rivera"
        assert params["uploaded_by_identity_source"] == "declared"

    def test_authenticated_reviewer_recorded(self, client, monkeypatch, tmp_path):
        from api import reviewer_tokens as rt

        path = tmp_path / "reviewer_tokens.json"
        token = rt.add_reviewer(path, "A. Chen")
        monkeypatch.setenv(rt.ENV_VAR, str(path))
        content = (FIXTURES / "prowler_ocsf_sample.json").read_bytes()

        # Token required once reviewer tokens are configured.
        resp = _upload(client, "sess-1", "prowler.json", content, uploaded_by=None)
        assert resp.status_code == 401

        headers = {**AUTH, rt.HEADER: token}
        resp = _upload(
            client, "sess-1", "prowler.json", content, headers, uploaded_by=None
        )
        assert resp.status_code == 201
        params = _vault_metadata(resp.json()["vault_id"])["parameters"]
        assert params["uploaded_by"] == "A. Chen"
        assert params["uploaded_by_identity_source"] == "authenticated"

        # A typed name naming someone else is refused, not silently replaced.
        resp = _upload(
            client, "sess-1", "prowler.json", content, headers, uploaded_by="Mallory"
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "reviewer_name_mismatch"


class TestNginxBodyLimit:
    """The compose nginx proxy must not reject an upload the API accepts."""

    def test_imports_location_allows_the_api_limit(self):
        import re

        template = (
            Path(__file__).parent.parent / "frontend" / "nginx.conf.template"
        ).read_text()
        match = re.search(
            r"location ~ \^/api/sessions/\[\^/\]\+/imports/ \{(.*?)\n    \}",
            template,
            re.S,
        )
        assert match, "no dedicated nginx location for /imports/"
        size = re.search(r"client_max_body_size (\d+)m;", match.group(1))
        assert size and int(size.group(1)) * 1024 * 1024 > MAX_PROWLER_FILE_BYTES
        # Everything else keeps the smaller cap.
        api_block = template.split("location /api/ {", 1)[1]
        assert "client_max_body_size 6m;" in api_block

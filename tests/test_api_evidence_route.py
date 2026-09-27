"""
Tests for POST /api/evidence/verify (src/api/routers/evidence.py).

Uses the real EvidenceAssuranceProtocol against the temp vault the autouse
conftest fixture points EVIDENCE_VAULT_PATH at, so these exercise the actual
router + swarm.evidence integration rather than a mocked verify call.
"""

import os
import sys
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm.evidence import EvidenceAssuranceProtocol

AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "test-token")
    from api.main import app

    return TestClient(app)


class TestVerifyAuth:
    def test_requires_auth(self, client):
        resp = client.post(
            "/api/evidence/verify",
            json={"vault_id": str(0), "exact_quote": "irrelevant text"},
        )
        assert resp.status_code == 401


class TestVerifyRealRecord:
    def test_verified_true_for_registered_record(self, client):
        registered = EvidenceAssuranceProtocol.register_evidence(
            "MinimumPasswordLength: 14", "aws.iam.get_account_password_policy"
        )
        resp = client.post(
            "/api/evidence/verify",
            headers=AUTH,
            json={
                "vault_id": registered["vault_id"],
                "exact_quote": "MinimumPasswordLength: 14",
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "vault_id": registered["vault_id"],
            "verified": True,
        }

    def test_verified_false_for_wrong_quote(self, client):
        registered = EvidenceAssuranceProtocol.register_evidence(
            "MFA enabled for user alice", "aws.iam.list_users"
        )
        resp = client.post(
            "/api/evidence/verify",
            headers=AUTH,
            json={
                "vault_id": registered["vault_id"],
                "exact_quote": "MFA disabled for user bob",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["verified"] is False

    def test_verified_false_for_too_short_quote(self, client):
        registered = EvidenceAssuranceProtocol.register_evidence(
            "some evidence payload here", "op"
        )
        resp = client.post(
            "/api/evidence/verify",
            headers=AUTH,
            json={"vault_id": registered["vault_id"], "exact_quote": "eviden"},
        )
        assert resp.status_code == 200
        assert resp.json()["verified"] is False

    def test_verified_false_for_unknown_vault_id(self, client):
        resp = client.post(
            "/api/evidence/verify",
            headers=AUTH,
            json={
                "vault_id": "00000000-0000-0000-0000-000000000000",
                "exact_quote": "anything at all",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["verified"] is False

    def test_verified_false_for_path_traversal_attempt(self, client):
        resp = client.post(
            "/api/evidence/verify",
            headers=AUTH,
            json={"vault_id": "../../../etc/passwd", "exact_quote": "root:x:0:0"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"vault_id": "../../../etc/passwd", "verified": False}

    def test_verified_false_for_sibling_directory_traversal(self, client, tmp_path):
        # Mirrors the swarm.evidence unit test: a vault_id that walks up and
        # back down into a sibling directory instead of straight out of it.
        resp = client.post(
            "/api/evidence/verify",
            headers=AUTH,
            json={
                "vault_id": "../vault_backup/leaked",
                "exact_quote": "secret marker",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["verified"] is False


class TestVerifyUnexpectedFailure:
    def test_internal_error_becomes_500_not_a_crash(self, client):
        with patch(
            "swarm.evidence.EvidenceAssuranceProtocol.verify_exact_quote",
            side_effect=RuntimeError("vault disk unavailable"),
        ):
            resp = client.post(
                "/api/evidence/verify",
                headers=AUTH,
                json={
                    "vault_id": "00000000-0000-0000-0000-000000000000",
                    "exact_quote": "some quote text",
                },
            )
        assert resp.status_code == 500
        assert "Internal server error" in resp.json()["detail"]


class TestVerifyRequestValidation:
    def test_missing_exact_quote_is_422(self, client):
        resp = client.post(
            "/api/evidence/verify",
            headers=AUTH,
            json={"vault_id": "00000000-0000-0000-0000-000000000000"},
        )
        assert resp.status_code == 422

    def test_missing_vault_id_is_422(self, client):
        resp = client.post(
            "/api/evidence/verify",
            headers=AUTH,
            json={"exact_quote": "some quote text"},
        )
        assert resp.status_code == 422

    def test_wrong_type_for_vault_id_is_422(self, client):
        resp = client.post(
            "/api/evidence/verify",
            headers=AUTH,
            json={"vault_id": 12345, "exact_quote": "some quote text"},
        )
        assert resp.status_code == 422

    def test_empty_body_is_422(self, client):
        resp = client.post("/api/evidence/verify", headers=AUTH, json={})
        assert resp.status_code == 422

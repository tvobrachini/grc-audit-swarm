"""Cross-site write refusal (``api.origin_check``) and the identity required
to delete a draft audit.

The compose nginx proxy adds the API token to every request it forwards, so
a page on another site could otherwise make a visitor's browser post an
upload or a delete through it: multipart POSTs need no CORS preflight.
"""

import logging
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from api.origin_check import (  # noqa: E402
    ORIGIN_NOT_ALLOWED,
    allowed_origins_from_env,
    is_request_allowed,
)
from swarm import session_manager  # noqa: E402

AUTH = {"Authorization": "Bearer test-token"}
FIXTURES = Path(__file__).parent / "fixtures" / "findings"
EVIL = "https://evil.example"
ALLOWED = ["http://localhost:5173"]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("API_AUTH_TOKEN", "test-token")
    monkeypatch.setattr(
        session_manager, "SESSIONS_PATH", str(tmp_path / "audit_sessions.json")
    )
    session_manager.save_session("sess-1", "Test audit")
    from api.main import app

    return TestClient(app)


def _import(client, headers):
    return client.post(
        "/api/sessions/sess-1/imports/prowler",
        headers={**AUTH, **headers},
        data={"uploaded_by": "J. Rivera"},
        files={
            "file": (
                "prowler.json",
                (FIXTURES / "prowler_ocsf_sample.json").read_bytes(),
                "application/json",
            )
        },
    )


class TestIsRequestAllowed:
    @pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
    def test_safe_methods_never_checked(self, method):
        assert is_request_allowed(method, {"origin": EVIL}, ALLOWED)

    def test_no_browser_headers_allowed(self):
        assert is_request_allowed("POST", {}, ALLOWED)

    @pytest.mark.parametrize(
        "headers",
        [
            {"origin": EVIL},
            {"origin": "null"},
            {"origin": EVIL, "sec-fetch-site": "cross-site"},
            {"sec-fetch-site": "cross-site"},
            {"sec-fetch-site": "same-site"},
            {"origin": "http://api.example:8000", "host": "api.example"},
        ],
    )
    def test_cross_site_refused(self, headers):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            assert not is_request_allowed(method, headers, ALLOWED)

    @pytest.mark.parametrize(
        "headers",
        [
            {"origin": "http://localhost:5173"},
            {"origin": "HTTP://LOCALHOST:5173/"},
            {"origin": "http://localhost:3000", "host": "localhost:3000"},
            {"origin": "http://localhost:3000", "sec-fetch-site": "same-origin"},
            {"sec-fetch-site": "same-origin"},
            {"sec-fetch-site": "none"},
        ],
    )
    def test_same_origin_or_allow_listed_allowed(self, headers):
        assert is_request_allowed("POST", headers, ALLOWED)

    def test_wildcard_ignored(self, monkeypatch):
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "*, https://audit.example ")
        assert allowed_origins_from_env() == ["https://audit.example"]


class TestMiddleware:
    def test_cross_site_prowler_import_refused(self, client):
        resp = _import(client, {"Origin": EVIL})
        assert resp.status_code == 403
        assert resp.json()["code"] == ORIGIN_NOT_ALLOWED

    def test_cross_site_fetch_metadata_refused(self, client):
        resp = _import(client, {"Sec-Fetch-Site": "cross-site"})
        assert resp.status_code == 403
        assert resp.json()["code"] == ORIGIN_NOT_ALLOWED

    def test_cross_site_session_with_document_refused(self, client):
        resp = client.post(
            "/api/sessions/with-document",
            headers={**AUTH, "Origin": EVIL},
            data={"theme": "S3", "prepared_by": "J. Rivera"},
            files={"document": ("scope.txt", b"scope", "text/plain")},
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == ORIGIN_NOT_ALLOWED

    def test_cross_site_delete_refused_and_session_kept(self, client):
        resp = client.delete(
            "/api/sessions/sess-1",
            headers={**AUTH, "Origin": EVIL},
            params={"deleted_by": "J. Rivera"},
        )
        assert resp.status_code == 403
        assert session_manager.get_session("sess-1")

    def test_allow_listed_origin_passes(self, client):
        assert _import(client, {"Origin": "http://localhost:5173"}).status_code == 201

    def test_same_host_origin_passes(self, client):
        # TestClient sends Host: testserver.
        assert _import(client, {"Origin": "http://testserver"}).status_code == 201

    def test_same_origin_fetch_metadata_passes(self, client):
        headers = {"Origin": "http://localhost:3000", "Sec-Fetch-Site": "same-origin"}
        assert _import(client, headers).status_code == 201

    def test_non_browser_client_passes(self, client):
        assert _import(client, {}).status_code == 201

    def test_cross_site_read_not_blocked(self, client):
        resp = client.get("/api/sessions", headers={**AUTH, "Origin": EVIL})
        assert resp.status_code == 200


class TestDeleteIdentity:
    def test_delete_requires_identity(self, client):
        resp = client.delete("/api/sessions/sess-1", headers=AUTH)
        assert resp.status_code == 422
        assert session_manager.get_session("sess-1")

    def test_delete_logs_declared_identity(self, client, caplog):
        with caplog.at_level(logging.INFO, logger="api.routers.sessions"):
            resp = client.delete(
                "/api/sessions/sess-1",
                headers=AUTH,
                params={"deleted_by": "J. Rivera"},
            )
        assert resp.status_code == 204
        assert not session_manager.get_session("sess-1")
        assert any(
            "sess-1" in r.getMessage()
            and "J. Rivera" in r.getMessage()
            and "declared" in r.getMessage()
            for r in caplog.records
        )

    def test_delete_uses_authenticated_reviewer(
        self, client, caplog, monkeypatch, tmp_path
    ):
        from api import reviewer_tokens as rt

        path = tmp_path / "reviewer_tokens.json"
        token = rt.add_reviewer(path, "A. Chen")
        monkeypatch.setenv(rt.ENV_VAR, str(path))

        assert client.delete("/api/sessions/sess-1", headers=AUTH).status_code == 401
        with caplog.at_level(logging.INFO, logger="api.routers.sessions"):
            resp = client.delete(
                "/api/sessions/sess-1", headers={**AUTH, rt.HEADER: token}
            )
        assert resp.status_code == 204
        assert any(
            "A. Chen" in r.getMessage() and "authenticated" in r.getMessage()
            for r in caplog.records
        )

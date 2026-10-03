"""Per-reviewer tokens (ADR-012): the tokens file, the CLI, the
X-Reviewer-Token contract on every reviewer action, identity_source on
decisions and trail entries, segregation of duties with authenticated
identities, and legacy compatibility.

Tokens are generated at run time by the CLI code; the only literal token
strings below are deliberately invalid.
"""

import io
import json
import logging
import os
import stat
import sys
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from review_helpers import record_demo_decisions  # type: ignore[import-not-found]
from api import reviewer_tokens as rt
from api.auth import ReviewerIdentity
from api.exports import (
    ExportContext,
    identity_note,
    working_papers_xlsx,
)
from api.job_store import get_flow, remove_flow
from swarm import session_manager
from swarm import trail as audit_trail
from swarm.audit_flow import AuditFlow, DecisionValidationError
from swarm.oscal_ar import _party_remarks
from swarm.schema import ReviewDecision

AUTH = {"Authorization": "Bearer test-token"}
PREPARER = "Pat Preparer"
IN_CHARGE = "Ivan In-Charge"
MANAGER = "Mona Manager"
BAD_TOKEN = "grcrt_this-is-not-a-valid-token"  # pragma: allowlist secret


# ── Tokens file and hashing ─────────────────────────────────────────────────


def _entry(name: str, token: str) -> dict:
    return {"name": name, "token_hash": rt.hash_token(token)}


def test_generated_tokens_are_random_and_prefixed():
    a, b = rt.generate_token(), rt.generate_token()
    assert a != b
    assert a.startswith(rt.TOKEN_PREFIX) and len(a) >= 40
    assert rt.hash_token(a).startswith("sha256:") and len(rt.hash_token(a)) == 71


def test_registry_authenticates_by_token_only():
    t1, t2 = rt.generate_token(), rt.generate_token()
    reg = rt.parse_registry(
        {"version": 1, "reviewers": [_entry("Ivan In-Charge", t1), _entry("Mona", t2)]}
    )
    assert reg.authenticate(t1) == "Ivan In-Charge"
    assert reg.authenticate(t2) == "Mona"
    assert reg.authenticate(BAD_TOKEN) is None
    assert reg.authenticate("") is None
    assert reg.authenticate(None) is None
    assert reg.authenticate(t1 + "x") is None
    assert reg.authenticate("x" * (rt.MAX_TOKEN_LENGTH + 1)) is None
    # Non-ASCII input is simply not a match (no exception).
    assert reg.authenticate("tökén") is None


def test_registry_compares_every_entry_in_constant_time():
    tokens = [rt.generate_token() for _ in range(4)]
    reg = rt.parse_registry(
        {
            "version": 1,
            "reviewers": [_entry(f"R{i}", t) for i, t in enumerate(tokens)],
        }
    )
    with patch(
        "api.reviewer_tokens.hmac.compare_digest",
        wraps=__import__("hmac").compare_digest,
    ) as cmp:
        assert reg.authenticate(tokens[0]) == "R0"
    # No early exit on the first match.
    assert cmp.call_count == 4


def test_registry_name_whitespace_is_normalised():
    t = rt.generate_token()
    reg = rt.parse_registry(
        {"version": 1, "reviewers": [_entry("  Ivan   In-Charge ", t)]}
    )
    assert reg.authenticate(t) == "Ivan In-Charge"
    assert reg.find("ivan in-charge") is not None


@pytest.mark.parametrize(
    "data, message",
    [
        ([], "JSON object"),
        ({"version": 2, "reviewers": []}, "version"),
        ({"version": 1}, "must be a list"),
        ({"version": 1, "reviewers": ["x"]}, "must be an object"),
        (
            {
                "version": 1,
                "reviewers": [{"name": " ", "token_hash": "sha256:" + "a" * 64}],
            },
            "no name",
        ),
        (
            {
                "version": 1,
                "reviewers": [{"name": "x" * 201, "token_hash": "sha256:" + "a" * 64}],
            },
            "too long",
        ),
        (
            {"version": 1, "reviewers": [{"name": "A", "token_hash": "a" * 64}]},
            "token_hash",
        ),
        (
            {
                "version": 1,
                "reviewers": [{"name": "A", "token_hash": "sha256:" + "A" * 64}],
            },
            "token_hash",
        ),
        ({"version": 1, "reviewers": [{"name": "A"}]}, "token_hash"),
        (
            {
                "version": 1,
                "reviewers": [
                    {"name": "Ivan", "token_hash": "sha256:" + "a" * 64},
                    {"name": " IVAN ", "token_hash": "sha256:" + "b" * 64},
                ],
            },
            "duplicate name",
        ),
        (
            {
                "version": 1,
                "reviewers": [
                    {"name": "Ivan", "token_hash": "sha256:" + "a" * 64},
                    {"name": "Mona", "token_hash": "sha256:" + "a" * 64},
                ],
            },
            "duplicate token_hash",
        ),
    ],
)
def test_parse_registry_rejects_bad_files(data, message):
    with pytest.raises(rt.ReviewerTokensError, match=message):
        rt.parse_registry(data)


def test_read_registry_missing_and_invalid_json(tmp_path):
    with pytest.raises(rt.ReviewerTokensError, match="cannot read"):
        rt.read_registry(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(rt.ReviewerTokensError, match="not valid JSON"):
        rt.read_registry(bad)


def test_load_registry_reloads_when_the_file_changes(tmp_path):
    path = tmp_path / "tokens.json"
    t1 = rt.add_reviewer(path, "Ivan")
    assert rt.load_registry(str(path)).authenticate(t1) == "Ivan"
    t2 = rt.add_reviewer(path, "Mona")
    reg = rt.load_registry(str(path))
    assert reg.authenticate(t2) == "Mona" and reg.authenticate(t1) == "Ivan"
    rt.remove_reviewer(path, "Ivan")
    assert rt.load_registry(str(path)).authenticate(t1) is None


def test_failure_limiter_blocks_then_expires():
    lim = rt.FailureLimiter(max_failures=3, window=10)
    for i in range(3):
        assert lim.retry_after("a", now=100 + i) is None
        lim.record_failure("a", now=100 + i)
    wait = lim.retry_after("a", now=103)
    assert wait is not None and 1 <= wait <= 11
    assert lim.retry_after("b", now=103) is None  # per client address
    assert lim.retry_after("a", now=111) is None  # window passed


# ── CLI ──────────────────────────────────────────────────────────────────────


def test_cli_add_prints_token_once_and_stores_only_its_hash(tmp_path, capsys):
    path = tmp_path / "tokens.json"
    assert rt.main(["--file", str(path), "add", "Ivan In-Charge"]) == 0
    out = capsys.readouterr().out.splitlines()
    token = out[-1]
    assert token.startswith(rt.TOKEN_PREFIX)
    text = path.read_text(encoding="utf-8")
    assert token not in text
    data = json.loads(text)
    assert data["version"] == 1
    assert data["reviewers"][0]["name"] == "Ivan In-Charge"
    assert data["reviewers"][0]["token_hash"] == rt.hash_token(token)
    assert rt.read_registry(path).authenticate(token) == "Ivan In-Charge"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_cli_add_existing_name_needs_replace(tmp_path, capsys):
    path = tmp_path / "tokens.json"
    rt.main(["--file", str(path), "add", "Ivan"])
    old = capsys.readouterr().out.splitlines()[-1]
    assert rt.main(["--file", str(path), "add", "ivan"]) == 2
    assert "already has a token" in capsys.readouterr().err
    assert rt.main(["--file", str(path), "add", "--replace", "IVAN"]) == 0
    new = capsys.readouterr().out.splitlines()[-1]
    reg = rt.read_registry(path)
    assert reg.authenticate(old) is None
    assert reg.authenticate(new) == "IVAN"
    assert len(reg.entries) == 1


def test_cli_remove_and_list(tmp_path, capsys, monkeypatch):
    path = tmp_path / "tokens.json"
    monkeypatch.setenv(rt.ENV_VAR, str(path))  # --file defaults to the env var
    rt.main(["add", "Ivan"])
    rt.main(["add", "Mona"])
    token_lines = capsys.readouterr().out
    assert rt.main(["list"]) == 0
    listed = capsys.readouterr().out
    assert "Ivan" in listed and "Mona" in listed
    assert rt.TOKEN_PREFIX not in listed and "sha256" not in listed
    assert rt.main(["remove", "ivan"]) == 0
    assert rt.main(["remove", "Nobody"]) == 1
    capsys.readouterr()
    rt.main(["list"])
    assert "Ivan" not in capsys.readouterr().out
    assert token_lines  # printed at add time only


def test_cli_rejects_blank_name_and_missing_file_setting(tmp_path, capsys):
    assert rt.main(["--file", str(tmp_path / "t.json"), "add", "   "]) == 2
    with pytest.raises(SystemExit):
        rt.main(["add", "Ivan"])  # no --file, no env var


def test_cli_refuses_to_edit_a_malformed_file(tmp_path, capsys):
    path = tmp_path / "tokens.json"
    path.write_text('{"version": 9, "reviewers": []}', encoding="utf-8")
    assert rt.main(["--file", str(path), "add", "Ivan"]) == 2
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 9


# ── Identity resolution ─────────────────────────────────────────────────────


def test_identity_resolution_declared_and_authenticated():
    assert ReviewerIdentity().resolve("Typed", "human_id") == ("Typed", "declared")
    assert ReviewerIdentity().resolve("", "human_id") == ("", "declared")
    who = ReviewerIdentity(name="Ivan In-Charge")
    assert who.resolve("", "human_id") == ("Ivan In-Charge", "authenticated")
    assert who.resolve(" ivan  in-charge ", "human_id") == (
        "Ivan In-Charge",
        "authenticated",
    )
    from api.auth import ReviewerAuthError

    with pytest.raises(ReviewerAuthError) as exc:
        who.resolve("Mona Manager", "human_id")
    assert exc.value.status_code == 403 and exc.value.code == "reviewer_name_mismatch"


# ── API ──────────────────────────────────────────────────────────────────────


class _InlineExecutor:
    def submit(self, session_id, fn, *args):
        fn(*args)


@pytest.fixture
def demo_env(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("DEMO_STEP_DELAY", "0")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("DEMO_QA_REJECT_PHASE", raising=False)
    monkeypatch.delenv("VAULT_ENCRYPTION_KEY", raising=False)


@pytest.fixture
def client(monkeypatch, tmp_path, demo_env):
    monkeypatch.setenv("API_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("TRAIL_ANCHORS_PATH", str(tmp_path / "anchors.json"))
    monkeypatch.setattr(
        session_manager, "SESSIONS_PATH", str(tmp_path / "audit_sessions.json")
    )
    from api.main import app

    with patch("api.routers.sessions.get_executor", return_value=_InlineExecutor()):
        yield TestClient(app)


@pytest.fixture
def tokens(monkeypatch, tmp_path):
    """Reviewer tokens on, one per person; returns name -> headers."""
    path = tmp_path / "reviewer_tokens.json"
    issued = {
        name: rt.add_reviewer(path, name) for name in (PREPARER, IN_CHARGE, MANAGER)
    }
    monkeypatch.setenv(rt.ENV_VAR, str(path))
    return {name: {**AUTH, rt.HEADER: tok} for name, tok in issued.items()}


def _create(c, headers, **body):
    body = {"theme": "S3 exposure", "business_context": "Fintech", **body}
    return c.post("/api/sessions", headers=headers, json=body)


def _approve(c, sid, gate, headers, human_id=None):
    body: dict = {"gate_number": gate}
    if human_id is not None:
        body["human_id"] = human_id
    return c.patch(f"/api/sessions/{sid}/approve", headers=headers, json=body)


def _trail(sid):
    flow = get_flow(sid)
    assert flow is not None
    return flow.state.approval_trail


def test_tokens_off_ignores_the_header_and_keeps_declared_identities(client):
    headers = {**AUTH, rt.HEADER: BAD_TOKEN}
    r = _create(client, headers, prepared_by=PREPARER)
    assert r.status_code == 201, r.text
    sid = r.json()["session_id"]
    try:
        assert _approve(client, sid, 1, headers).status_code == 422  # no name
        assert _approve(client, sid, 1, headers, human_id="  ").status_code == 422
        assert _approve(client, sid, 1, headers, human_id=IN_CHARGE).status_code == 200
        trail = _trail(sid)
        assert [e["identity_source"] for e in trail] == ["declared", "declared"]
        assert trail[1]["human"] == IN_CHARGE
        assert client.get("/api/reviewer", headers=AUTH).json() == {
            "name": None,
            "identity_source": "declared",
        }
    finally:
        remove_flow(sid)


def test_create_session_without_name_is_422_when_tokens_off(client):
    assert _create(client, AUTH).status_code == 422
    assert _create(client, AUTH, prepared_by="   ").status_code == 422


def test_reviewer_endpoint_and_config_with_tokens(client, tokens):
    assert client.get("/api/config", headers=AUTH).json()["reviewer_tokens"] is True
    r = client.get("/api/reviewer", headers=tokens[IN_CHARGE])
    assert r.json() == {"name": IN_CHARGE, "identity_source": "authenticated"}
    r = client.get("/api/reviewer", headers=AUTH)
    assert r.status_code == 401 and r.json()["code"] == "reviewer_token_missing"


def test_missing_invalid_and_mismatched_tokens(client, tokens):
    r = _create(client, tokens[PREPARER])
    assert r.status_code == 201, r.text
    sid = r.json()["session_id"]
    try:
        r = _approve(client, sid, 1, AUTH, human_id=IN_CHARGE)
        assert r.status_code == 401
        assert r.json() == {
            "detail": r.json()["detail"],
            "code": "reviewer_token_missing",
        }
        assert isinstance(r.json()["detail"], str)

        r = _approve(client, sid, 1, {**AUTH, rt.HEADER: BAD_TOKEN}, human_id=IN_CHARGE)
        assert r.status_code == 401 and r.json()["code"] == "reviewer_token_invalid"
        assert BAD_TOKEN not in r.text

        # A typed name that is someone else's: refused, not replaced.
        r = _approve(client, sid, 1, tokens[IN_CHARGE], human_id=MANAGER)
        assert r.status_code == 403 and r.json()["code"] == "reviewer_name_mismatch"
        assert get_flow(sid).state.status == "WAITING_HUMAN_GATE_1"

        # The shared API token is still required on top of the reviewer token.
        r = _approve(client, sid, 1, {rt.HEADER: tokens[IN_CHARGE][rt.HEADER]})
        assert r.status_code == 401 and "WWW-Authenticate" in r.headers

        # Matching typed name (any case): accepted, the token's spelling recorded.
        r = _approve(client, sid, 1, tokens[IN_CHARGE], human_id="ivan in-charge")
        assert r.status_code == 200, r.text
        entry = _trail(sid)[-1]
        assert entry["human"] == IN_CHARGE
        assert entry["identity_source"] == "authenticated"
    finally:
        remove_flow(sid)


def test_session_creation_takes_the_preparer_from_the_token(client, tokens):
    r = _create(client, tokens[PREPARER], prepared_by="Someone Else")
    assert r.status_code == 403 and r.json()["code"] == "reviewer_name_mismatch"
    r = _create(client, AUTH, prepared_by=PREPARER)
    assert r.status_code == 401 and r.json()["code"] == "reviewer_token_missing"
    r = _create(client, tokens[PREPARER])
    assert r.status_code == 201, r.text
    assert r.json()["prepared_by"] == PREPARER
    sid = r.json()["session_id"]
    try:
        first = _trail(sid)[0]
        assert first["action"] == "audit_created"
        assert first["human"] == PREPARER
        assert first["identity_source"] == "authenticated"
    finally:
        remove_flow(sid)


def test_session_with_document_takes_the_preparer_from_the_token(client, tokens):
    files = {"document": ("scope.txt", io.BytesIO(b"Scope: S3 buckets."), "text/plain")}
    r = client.post(
        "/api/sessions/with-document",
        headers=tokens[PREPARER],
        data={"theme": "S3 exposure"},
        files=files,
    )
    assert r.status_code == 201, r.text
    assert r.json()["prepared_by"] == PREPARER
    remove_flow(r.json()["session_id"])
    files = {"document": ("scope.txt", io.BytesIO(b"Scope."), "text/plain")}
    r = client.post(
        "/api/sessions/with-document",
        headers=tokens[PREPARER],
        data={"theme": "S3", "prepared_by": MANAGER},
        files=files,
    )
    assert r.status_code == 403


def test_misconfigured_tokens_file_fails_closed(client, monkeypatch, tmp_path, caplog):
    monkeypatch.setenv(rt.ENV_VAR, str(tmp_path / "does-not-exist.json"))
    r = _create(client, {**AUTH, rt.HEADER: BAD_TOKEN}, prepared_by=PREPARER)
    assert r.status_code == 503 and r.json()["code"] == "reviewer_tokens_unavailable"
    bad = tmp_path / "bad.json"
    bad.write_text('{"version": 1, "reviewers": "nope"}', encoding="utf-8")
    monkeypatch.setenv(rt.ENV_VAR, str(bad))
    r = _create(client, {**AUTH, rt.HEADER: BAD_TOKEN}, prepared_by=PREPARER)
    assert r.status_code == 503
    # Reads are unaffected: only reviewer actions need the token.
    assert client.get("/api/sessions", headers=AUTH).status_code == 200


def test_repeated_invalid_tokens_are_rate_limited(client, tokens, monkeypatch):
    monkeypatch.setattr(rt, "limiter", rt.FailureLimiter(max_failures=3, window=60))
    bad = {**AUTH, rt.HEADER: BAD_TOKEN}
    codes = [client.get("/api/reviewer", headers=bad).status_code for _ in range(3)]
    assert codes == [401, 401, 401]
    r = client.get("/api/reviewer", headers=bad)
    assert r.status_code == 429 and r.json()["code"] == "reviewer_token_rate_limited"
    assert int(r.headers["Retry-After"]) >= 1
    # While blocked, even a valid token waits (a lookup would be an oracle).
    assert client.get("/api/reviewer", headers=tokens[IN_CHARGE]).status_code == 429
    rt.limiter.reset()
    assert client.get("/api/reviewer", headers=tokens[IN_CHARGE]).status_code == 200


def test_tokens_are_never_logged(client, tokens, caplog):
    caplog.set_level(logging.DEBUG)
    good = tokens[IN_CHARGE][rt.HEADER]
    client.get("/api/reviewer", headers=tokens[IN_CHARGE])
    client.get("/api/reviewer", headers={**AUTH, rt.HEADER: BAD_TOKEN})
    assert good not in caplog.text and BAD_TOKEN not in caplog.text


def test_sod_with_authenticated_identities(client, tokens):
    sid = _create(client, tokens[PREPARER]).json()["session_id"]
    try:
        # The preparer cannot approve their own audit, token or not.
        r = _approve(client, sid, 1, tokens[PREPARER])
        assert r.status_code == 409 and "Segregation of duties" in r.json()["detail"]
        # Nor type another name while holding their own token.
        r = _approve(client, sid, 1, tokens[PREPARER], human_id=IN_CHARGE)
        assert r.status_code == 403
        assert _approve(client, sid, 1, tokens[IN_CHARGE]).status_code == 200

        # Preparer may not sign off; declared names are gone with tokens on.
        r = client.post(
            f"/api/sessions/{sid}/decisions",
            headers=tokens[PREPARER],
            json={"decision_type": "sign_off", "subject_id": "CTRL-01"},
        )
        assert r.status_code == 409 and "Segregation of duties" in r.json()["detail"]
        r = client.post(
            f"/api/sessions/{sid}/decisions",
            headers=tokens[PREPARER],
            json={
                "decision_type": "sign_off",
                "subject_id": "CTRL-01",
                "decided_by": IN_CHARGE,
            },
        )
        assert r.status_code == 403

        record_demo_decisions(client, sid, "gate2", IN_CHARGE, tokens[IN_CHARGE])
        assert _approve(client, sid, 2, tokens[IN_CHARGE]).status_code == 200
        record_demo_decisions(client, sid, "gate3", MANAGER, tokens[MANAGER])
        # Gate 3 approver must differ from the Gate 2 approver.
        r = _approve(client, sid, 3, tokens[IN_CHARGE])
        assert r.status_code == 409 and "Segregation of duties" in r.json()["detail"]
        assert _approve(client, sid, 3, tokens[MANAGER]).status_code == 200

        detail = client.get(f"/api/sessions/{sid}", headers=AUTH).json()
        assert detail["status"] == "COMPLETED"
        assert {d["identity_source"] for d in detail["review_decisions"]} == {
            "authenticated"
        }
        assert {d["decided_by"] for d in detail["review_decisions"]} == {
            IN_CHARGE,
            MANAGER,
        }
        assert {e["identity_source"] for e in detail["approval_trail"]} == {
            "authenticated"
        }
        assert detail["trail_verification"]["ok"] is True, detail["trail_verification"]

        # From disk, too.
        remove_flow(sid)
        verify = client.get(f"/api/sessions/{sid}/trail/verify", headers=AUTH).json()
        assert verify["ok"] is True, verify

        report = client.get(f"/api/sessions/{sid}/export/report.md", headers=AUTH).text
        assert "[authenticated reviewer token]" in report
        assert "declared, not authenticated" not in report
        oscal = client.get(
            f"/api/sessions/{sid}/export/oscal.json", headers=AUTH
        ).json()
        parties = oscal["assessment-results"]["metadata"]["parties"]
        assert all(
            "authenticated by a per-reviewer token" in p["remarks"] for p in parties
        )
    finally:
        remove_flow(sid)


def test_retry_override_and_return_record_authenticated_identity(
    client, tokens, monkeypatch
):
    monkeypatch.setenv("DEMO_QA_REJECT_PHASE", "1")
    sid = _create(client, tokens[PREPARER]).json()["session_id"]
    try:
        assert get_flow(sid).state.status == "QA_REJECTED_PHASE_1"
        r = client.post(
            f"/api/sessions/{sid}/qa-override",
            headers=tokens[IN_CHARGE],
            json={"phase": 1, "reason": "Accepted after review"},
        )
        assert r.status_code == 200, r.text
        r = client.post(
            f"/api/sessions/{sid}/return",
            headers=tokens[MANAGER],
            json={"phase": 1, "notes": "Add a control for logging"},
        )
        assert r.status_code == 200, r.text
        # Demo rejects phase 1 again on the re-run; retry as the preparer
        # (a retry is not a review, so the preparer may).
        assert get_flow(sid).state.status == "QA_REJECTED_PHASE_1"
        r = client.post(
            f"/api/sessions/{sid}/retry",
            headers=tokens[PREPARER],
            json={"phase": 1},
        )
        assert r.status_code == 200, r.text
        r = client.post(
            f"/api/sessions/{sid}/retry",
            headers=tokens[PREPARER],
            json={"phase": 1, "human_id": MANAGER},
        )
        assert r.status_code == 403
        by_action = {e["action"]: e for e in _trail(sid)}
        assert by_action["qa_override"]["human"] == IN_CHARGE
        assert by_action["return_for_rework"]["human"] == MANAGER
        assert by_action["retry"]["human"] == PREPARER
        assert all(e["identity_source"] == "authenticated" for e in _trail(sid))
        assert get_flow(sid).verify_trail()["ok"] is True
    finally:
        remove_flow(sid)


# ── Trail and decisions: the new field and legacy data ──────────────────────


def test_identity_source_is_covered_by_the_hash_chain():
    flow = AuditFlow(initial_status="WAITING_HUMAN_GATE_1")
    flow.record_preparer(PREPARER, identity_source="authenticated")
    trail = flow.state.approval_trail
    assert audit_trail.verify_trail(trail)["ok"] is True
    trail[0]["identity_source"] = "declared"
    result = audit_trail.verify_trail(trail)
    assert result["status"] == "broken" and result["first_broken_index"] == 0
    del trail[0]["identity_source"]
    assert audit_trail.verify_trail(trail)["status"] == "broken"


def test_legacy_trail_without_identity_source_still_verifies_and_extends():
    trail: list[dict] = []
    # Entries as written before ADR-012: no identity_source.
    audit_trail.append_entry(
        trail,
        {
            "gate": "Audit created",
            "human": PREPARER,
            "action": "audit_created",
            "timestamp": "t0",
        },
    )
    audit_trail.append_entry(
        trail,
        {
            "gate": "Gate 1 (Planning)",
            "human": IN_CHARGE,
            "action": "gate_approval",
            "timestamp": "t1",
        },
    )
    assert "identity_source" not in trail[0]
    assert audit_trail.verify_trail(trail)["ok"] is True
    flow = AuditFlow(initial_status="WAITING_HUMAN_GATE_1")
    flow.state.approval_trail = trail
    flow.state.prepared_by = PREPARER
    flow._stamp_trail("Note", MANAGER, "retry", identity_source="authenticated")
    assert audit_trail.verify_trail(flow.state.approval_trail)["ok"] is True
    assert flow.state.approval_trail[-1]["identity_source"] == "authenticated"


def test_legacy_decision_without_identity_source_loads_as_declared():
    raw = {
        "decision_id": "d1",
        "phase": 2,
        "artifact": "working_papers",
        "draft_digest": "0" * 64,
        "subject_type": "finding",
        "subject_id": "CTRL-01",
        "decision_type": "sign_off",
        "decided_by": IN_CHARGE,
        "decided_at": "2026-01-01T00:00:00+00:00",
    }
    assert ReviewDecision.model_validate(raw).identity_source == "declared"


def test_flow_rejects_unknown_identity_source():
    flow = AuditFlow(initial_status="WAITING_HUMAN_GATE_1")
    with pytest.raises(ValueError, match="identity_source"):
        flow.record_preparer(PREPARER, identity_source="sso")
    assert flow.state.approval_trail == [] and flow.state.prepared_by == ""
    with pytest.raises(ValueError, match="identity_source"):
        flow.begin_phase_2(IN_CHARGE, identity_source="sso")
    assert flow.state.status == "WAITING_HUMAN_GATE_1"


def test_record_decision_rejects_unknown_identity_source(demo_env):
    flow = AuditFlow()
    flow.state.theme = "S3 exposure"
    flow.state.business_context = "Fintech"
    flow.record_preparer(PREPARER, require_review_decisions=True)
    flow.begin_phase_1()
    flow.generate_planning()
    flow.begin_phase_2(IN_CHARGE)
    flow.generate_fieldwork()
    with pytest.raises(DecisionValidationError, match="identity_source"):
        flow.record_decision(
            decision_type="sign_off",
            subject_id="CTRL-01",
            decided_by=IN_CHARGE,
            identity_source="sso",
        )
    d = flow.record_decision(
        decision_type="sign_off",
        subject_id="CTRL-01",
        decided_by=IN_CHARGE,
        identity_source="authenticated",
    )
    assert d.identity_source == "authenticated"
    assert flow.state.approval_trail[-1]["identity_source"] == "authenticated"
    assert flow.verify_trail()["ok"] is True

    # Working-papers export: the header and note follow the identity source.
    ctx = ExportContext(session_id="s", session_name="n", status=flow.state.status)
    assert flow.state.working_papers is not None
    wb = load_workbook(
        io.BytesIO(
            working_papers_xlsx(flow.state.working_papers, ctx, flow.effective_view())
        )
    )
    header = [c.value for c in wb["Findings"][1]]
    assert "Reviewed By (authenticated)" in header


# ── Export wording ──────────────────────────────────────────────────────────


def test_identity_wording_is_conditional():
    assert identity_note(set()) == "Identities are declared, not authenticated."
    assert identity_note({"declared"}) == "Identities are declared, not authenticated."
    assert "authenticated with per-reviewer tokens" in identity_note({"authenticated"})
    assert "not single sign-on, no MFA" in identity_note({"authenticated"})
    assert "marked declared" in identity_note({"declared", "authenticated"})
    assert _party_remarks(set()).startswith("Declared identity")
    assert _party_remarks({"declared"}).startswith("Declared identity")
    assert "per-reviewer token" in _party_remarks({"authenticated"})
    assert "Some of this party's actions" in _party_remarks(
        {"declared", "authenticated"}
    )


# ── Mandatory reviewer tokens outside demo mode ─────────────────────────────


def test_reviewer_tokens_mandatory_flag(monkeypatch):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("REVIEWER_TOKENS_MANDATORY", raising=False)
    assert rt.reviewer_tokens_mandatory() is False

    monkeypatch.setenv("REVIEWER_TOKENS_MANDATORY", "1")
    assert rt.reviewer_tokens_mandatory() is True

    monkeypatch.delenv("REVIEWER_TOKENS_MANDATORY", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert rt.reviewer_tokens_mandatory() is True

    monkeypatch.setenv("ENVIRONMENT", "staging")
    assert rt.reviewer_tokens_mandatory() is True

    # In DEMO_MODE=1, reviewer tokens are not mandatory so demo mode works out of the box
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("REVIEWER_TOKENS_MANDATORY", "1")
    assert rt.reviewer_tokens_mandatory() is False


def test_reviewer_identity_refuses_when_tokens_mandatory_and_unset(monkeypatch):
    from fastapi import Request
    from api.auth import reviewer_identity, ReviewerAuthError

    monkeypatch.delenv("DEMO_MODE", raising=False)
    monkeypatch.setenv("REVIEWER_TOKENS_MANDATORY", "1")
    monkeypatch.delenv("REVIEWER_TOKENS_FILE", raising=False)

    req = Request({"type": "http", "headers": []})
    with pytest.raises(ReviewerAuthError) as exc_info:
        reviewer_identity(req, x_reviewer_token=None)
    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "reviewer_tokens_unavailable"


def test_api_startup_refuses_when_tokens_mandatory_and_unset(monkeypatch):
    from api.main import app

    monkeypatch.delenv("DEMO_MODE", raising=False)
    monkeypatch.setenv("REVIEWER_TOKENS_MANDATORY", "1")
    monkeypatch.delenv("REVIEWER_TOKENS_FILE", raising=False)
    monkeypatch.setenv("API_AUTH_TOKEN", "test-token")

    with pytest.raises(rt.ReviewerTokensError, match="REVIEWER_TOKENS_FILE must be configured"):
        with TestClient(app):
            pass

"""
Guardrails for the AWS MCP evidence collection path.

The default tests stay local and deterministic. A live integration check can be
enabled explicitly with RUN_AWS_MCP_INTEGRATION=1.
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm.agents.evidence_collector import _redact_aws_metadata, _store_tool_evidence


def test_store_tool_evidence_accumulates_multiple_tool_results():
    evidence_updates = {}

    _store_tool_evidence(
        evidence_updates,
        "AC",
        "get_iam_password_policy",
        {"AccountId": "123456789012"},
    )
    _store_tool_evidence(
        evidence_updates,
        "AC",
        "list_iam_users_with_mfa",
        [{"UserName": "alice", "AccountId": "123456789012"}],
    )

    assert set(evidence_updates["AC"]) == {
        "get_iam_password_policy",
        "list_iam_users_with_mfa",
    }
    assert (
        evidence_updates["AC"]["get_iam_password_policy"]["AccountId"] == "XXXXXXXXXXXX"
    )
    assert (
        evidence_updates["AC"]["list_iam_users_with_mfa"][0]["AccountId"]
        == "XXXXXXXXXXXX"
    )


def test_redact_aws_metadata_scrubs_nested_account_ids():
    payload = {
        "account": "123456789012",
        "nested": [{"value": "arn:aws:iam::123456789012:role/Admin"}],
    }

    redacted = _redact_aws_metadata(payload)

    assert redacted["account"] == "XXXXXXXXXXXX"
    assert "123456789012" not in redacted["nested"][0]["value"]


@pytest.mark.skipif(
    os.environ.get("RUN_AWS_MCP_INTEGRATION") != "1",
    reason="Set RUN_AWS_MCP_INTEGRATION=1 to enable live MCP smoke test.",
)
def test_aws_mcp_connection_smoke():
    command = ["npx", "-y", "@modelcontextprotocol/server-aws"]

    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
    )

    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        return

    assert process.returncode == 0, "AWS MCP server failed to start cleanly"


if __name__ == "__main__":
    test_aws_mcp_connection_smoke()

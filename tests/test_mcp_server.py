"""
Tests for MCP server AWS audit tools.
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm.mcp_server import (  # noqa: E402
    get_access_key_age,
    get_cloudtrail_trails,
    get_guardduty_detectors,
    get_iam_access_analyzer,
    get_iam_password_policy,
    get_kms_key_rotation,
    get_root_account_summary,
    list_iam_users_with_mfa,
    list_public_s3_buckets,
)

# ─── get_iam_password_policy ─────────────────────────────────────────────────


class TestGetIamPasswordPolicy:
    def test_returns_policy_on_success(self):
        policy = {"MinimumPasswordLength": 14, "RequireSymbols": True}
        mock_client = MagicMock()
        mock_client.get_account_password_policy.return_value = {
            "PasswordPolicy": policy
        }

        with patch("boto3.client", return_value=mock_client):
            result = get_iam_password_policy()

        data = json.loads(result)
        assert data["MinimumPasswordLength"] == 14
        assert data["RequireSymbols"] is True

    def test_no_policy_defined_returns_finding(self):
        mock_client = MagicMock()
        mock_client.get_account_password_policy.side_effect = ClientError(
            {"Error": {"Code": "NoSuchEntity", "Message": ""}},
            "GetAccountPasswordPolicy",
        )

        with patch("boto3.client", return_value=mock_client):
            result = get_iam_password_policy()

        assert result.startswith("Finding: No IAM account password policy is set")


# ─── list_iam_users_with_mfa ─────────────────────────────────────────────────


class TestListIamUsersWithMfa:
    def _make_client(self, users, mfa_map, mfa_error=None):
        mock_client = MagicMock()

        def get_paginator(name):
            paginator = MagicMock()
            if name == "list_users":
                paginator.paginate.return_value = [{"Users": users}]
            else:

                def paginate(UserName):
                    if mfa_error:
                        raise mfa_error
                    return [{"MFADevices": mfa_map.get(UserName, [])}]

                paginator.paginate.side_effect = paginate
            return paginator

        mock_client.get_paginator.side_effect = get_paginator
        return mock_client

    def test_user_with_mfa_marked_yes(self):
        users = [{"UserName": "alice"}]
        client = self._make_client(users, {"alice": [{"SerialNumber": "arn:..."}]})

        with patch("boto3.client", return_value=client):
            result = list_iam_users_with_mfa()

        data = json.loads(result)["Users"]
        assert data[0]["UserName"] == "alice"
        assert data[0]["MFA_Enabled"] == "Yes"

    def test_user_without_mfa_marked_no(self):
        users = [{"UserName": "bob"}]
        client = self._make_client(users, {"bob": []})

        with patch("boto3.client", return_value=client):
            result = list_iam_users_with_mfa()

        data = json.loads(result)["Users"]
        assert data[0]["UserName"] == "bob"
        assert data[0]["MFA_Enabled"] == "No"

    def test_client_error_marked_unknown(self):
        err = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": ""}}, "ListMFADevices"
        )
        client = self._make_client([{"UserName": "carol"}], {}, mfa_error=err)

        with patch("boto3.client", return_value=client):
            result = list_iam_users_with_mfa()

        data = json.loads(result)["Users"]
        assert data[0]["UserName"] == "carol"
        assert data[0]["MFA_Enabled"] == "Unknown"


# ─── list_public_s3_buckets ──────────────────────────────────────────────────


class TestListPublicS3Buckets:
    """The MCP server reuses swarm.tools.aws_checks; these tests only check the
    wiring. The decision logic is tested in test_aws_checks.py and tests/eval/."""

    def test_uses_shared_s3_logic(self):
        client = MagicMock()
        with (
            patch("boto3.client", return_value=client),
            patch(
                "swarm.mcp_server.collect_s3_public_access",
                return_value={"Buckets": [], "Summary": {"buckets": 0}},
            ) as collect,
        ):
            result = list_public_s3_buckets()

        collect.assert_called_once_with(client, client, client)
        assert json.loads(result)["Summary"] == {"buckets": 0}

    def test_list_error_is_reported(self):
        with (
            patch("boto3.client", return_value=MagicMock()),
            patch(
                "swarm.mcp_server.collect_s3_public_access",
                side_effect=ClientError(
                    {"Error": {"Code": "AccessDenied", "Message": ""}}, "ListBuckets"
                ),
            ),
        ):
            result = list_public_s3_buckets()

        assert result == "Error listing S3 buckets: AccessDenied"


# ─── Redaction & new MCP tools ───────────────────────────────────────────────


class TestMcpRedactionAndNewTools:
    def test_existing_tools_redact_account_ids(self):
        account_id = "123456789012"
        mock_client = MagicMock()
        mock_client.get_account_password_policy.return_value = {
            "PasswordPolicy": {"AccountId": account_id, "MinimumPasswordLength": 14}
        }
        with patch("boto3.client", return_value=mock_client):
            out = get_iam_password_policy()
        assert account_id not in out
        assert "[REDACTED]" in out

    def test_new_mcp_tools_wire_collectors_and_redact_account_ids(self):
        account_id = "123456789012"
        cases = [
            (
                get_root_account_summary,
                "swarm.mcp_server.collect_root_account_summary",
                {"Verdict": "PASS", "Arn": f"arn:aws:iam::{account_id}:root"},
            ),
            (
                get_access_key_age,
                "swarm.mcp_server.collect_access_key_age",
                {"Verdict": "PASS", "Owner": account_id},
            ),
            (
                get_cloudtrail_trails,
                "swarm.mcp_server.collect_cloudtrail_trails",
                {
                    "Verdict": "PASS",
                    "TrailARN": f"arn:aws:cloudtrail:us-east-1:{account_id}:trail/t",
                },
            ),
            (
                get_kms_key_rotation,
                "swarm.mcp_server.collect_kms_key_rotation",
                {
                    "Verdict": "PASS",
                    "Arn": f"arn:aws:kms:us-east-1:{account_id}:key/k1",
                },
            ),
            (
                get_guardduty_detectors,
                "swarm.mcp_server.collect_guardduty_detectors",
                {"Verdict": "PASS", "Account": account_id},
            ),
            (
                get_iam_access_analyzer,
                "swarm.mcp_server.collect_iam_access_analyzer",
                {
                    "Verdict": "PASS",
                    "Arn": f"arn:aws:access-analyzer:us-east-1:{account_id}:analyzer/a",
                },
            ),
        ]
        for fn, collector_path, payload in cases:
            with (
                patch("boto3.client", return_value=MagicMock()),
                patch(collector_path, return_value=payload),
            ):
                out = fn()
            assert account_id not in out
            assert "[REDACTED]" in out
            assert json.loads(out)["Verdict"] == "PASS"

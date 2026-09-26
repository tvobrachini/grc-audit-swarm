"""
Tests for the CrewAI AWS evidence tools (vault registration, redaction, errors).
The decision logic itself is tested in test_aws_checks.py and tests/eval/.
All AWS API calls are stubbed — no real credentials required.
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import boto3
from botocore.exceptions import ClientError, NoCredentialsError, NoRegionError
from botocore.stub import Stubber

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm.evidence import EvidenceAssuranceProtocol  # noqa: E402

ACCOUNT_ID = "123456789012"


def _client(service):
    return boto3.client(
        service,
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",  # pragma: allowlist secret
    )


def _split(result: str) -> tuple[str, str]:
    head, raw = result.split("\nRaw Output: ", 1)
    return head.removeprefix("Vault ID: "), raw


def _user(name):
    return {
        "UserName": name,
        "UserId": "AIDA" + "X" * 16,
        "Arn": f"arn:aws:iam::{ACCOUNT_ID}:user/{name}",
        "Path": "/",
        "CreateDate": "2024-01-01T00:00:00Z",
    }


# ─── get_iam_password_policy ─────────────────────────────────────────────────


class TestGetIamPasswordPolicy:
    def test_returns_vault_id_on_success(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        mock_client = MagicMock()
        mock_client.get_account_password_policy.return_value = {
            "PasswordPolicy": {"MinimumPasswordLength": 14, "RequireSymbols": True}
        }

        with patch("swarm.tools.aws_tools._boto_client", return_value=mock_client):
            from swarm.tools.aws_tools import get_iam_password_policy

            result = get_iam_password_policy.run("")

        vault_id, raw = _split(result)
        assert json.loads(raw)["MinimumPasswordLength"] == 14
        assert EvidenceAssuranceProtocol.verify_exact_quote(
            vault_id, '"MinimumPasswordLength": 14'
        )

    def test_no_policy_defined_returns_finding(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        iam = _client("iam")
        with Stubber(iam) as stub:
            stub.add_client_error(
                "get_account_password_policy",
                service_error_code="NoSuchEntity",
                service_message=f"The Password Policy with domain name {ACCOUNT_ID} cannot be found.",
                http_status_code=404,
            )
            with patch("swarm.tools.aws_tools._boto_client", return_value=iam):
                from swarm.tools.aws_tools import get_iam_password_policy

                result = get_iam_password_policy.run("")

        assert "Finding: No IAM account password policy is set" in result
        assert ACCOUNT_ID not in result

    def test_client_creation_failure_returns_clean_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        with patch(
            "swarm.tools.aws_tools._boto_client",
            side_effect=NoCredentialsError(),
        ):
            from swarm.tools.aws_tools import get_iam_password_policy

            result = get_iam_password_policy.run("")

        assert "Vault ID:" in result
        assert "Error fetching password policy" in result

    def test_account_id_not_in_output(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        mock_client = MagicMock()
        mock_client.get_account_password_policy.return_value = {
            "PasswordPolicy": {"AccountId": ACCOUNT_ID, "MinimumPasswordLength": 8}
        }

        with patch("swarm.tools.aws_tools._boto_client", return_value=mock_client):
            from swarm.tools.aws_tools import get_iam_password_policy

            result = get_iam_password_policy.run("")

        assert ACCOUNT_ID not in result


# ─── list_iam_users_with_mfa ─────────────────────────────────────────────────


class TestListIamUsersWithMfa:
    def test_user_with_and_without_mfa(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        iam = _client("iam")
        with Stubber(iam) as stub:
            stub.add_response(
                "list_users", {"Users": [_user("alice")], "IsTruncated": False}, {}
            )
            stub.add_response(
                "list_mfa_devices",
                {
                    "MFADevices": [
                        {
                            "UserName": "alice",
                            "SerialNumber": f"arn:aws:iam::{ACCOUNT_ID}:mfa/alice",
                            "EnableDate": "2024-01-01T00:00:00Z",
                        }
                    ],
                    "IsTruncated": False,
                },
                {"UserName": "alice"},
            )
            with patch("swarm.tools.aws_tools._boto_client", return_value=iam):
                from swarm.tools.aws_tools import list_iam_users_with_mfa

                result = list_iam_users_with_mfa.run("")

        vault_id, raw = _split(result)
        data = json.loads(raw)
        assert data["Users"] == [
            {"UserName": "alice", "MFA_Enabled": "Yes", "MFADeviceCount": 1}
        ]
        assert EvidenceAssuranceProtocol.verify_exact_quote(
            vault_id, '"MFA_Enabled": "Yes"'
        )

    def test_list_users_access_denied_is_error_and_redacted(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        iam = _client("iam")
        with Stubber(iam) as stub:
            stub.add_client_error(
                "list_users",
                service_error_code="AccessDenied",
                service_message=f"arn:aws:iam::{ACCOUNT_ID}:user/x is not authorized",
            )
            with patch("swarm.tools.aws_tools._boto_client", return_value=iam):
                from swarm.tools.aws_tools import list_iam_users_with_mfa

                result = list_iam_users_with_mfa.run("")

        assert "Error listing IAM users: AccessDenied" in result
        assert ACCOUNT_ID not in result


# ─── list_public_s3_buckets ──────────────────────────────────────────────────


class TestListPublicS3Buckets:
    def test_routes_each_service_to_its_client_and_registers(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        clients = {s: _client(s) for s in ("s3", "s3control", "sts")}
        stubs = {s: Stubber(c) for s, c in clients.items()}
        stubs["sts"].add_response("get_caller_identity", {"Account": ACCOUNT_ID}, {})
        stubs["s3control"].add_response(
            "get_public_access_block",
            {
                "PublicAccessBlockConfiguration": {
                    "BlockPublicAcls": True,
                    "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True,
                    "RestrictPublicBuckets": True,
                }
            },
            {"AccountId": ACCOUNT_ID},
        )
        stubs["s3"].add_response("list_buckets", {"Buckets": []})
        for s in stubs.values():
            s.activate()

        with patch("swarm.tools.aws_tools._boto_client", side_effect=clients.get):
            from swarm.tools.aws_tools import list_public_s3_buckets

            result = list_public_s3_buckets.run("")

        for s in stubs.values():
            s.assert_no_pending_responses()
        vault_id, raw = _split(result)
        data = json.loads(raw)
        assert data["AccountBlockPublicAccess"]["status"] == "configured"
        assert data["Summary"]["buckets"] == 0
        assert ACCOUNT_ID not in result
        record = json.loads((tmp_path / f"{vault_id}.json").read_text())
        assert record["mcp_source"] == "aws.s3.list_public_buckets"

    def test_list_buckets_denied_is_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        client = MagicMock()
        client.get_caller_identity.return_value = {"Account": ACCOUNT_ID}
        client.get_public_access_block.return_value = {
            "PublicAccessBlockConfiguration": {}
        }
        client.get_paginator.return_value.paginate.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": ""}}, "ListBuckets"
        )
        with patch("swarm.tools.aws_tools._boto_client", return_value=client):
            from swarm.tools.aws_tools import list_public_s3_buckets

            result = list_public_s3_buckets.run("")

        assert "Error listing S3 buckets: AccessDenied" in result

    def test_missing_region_is_reported_not_raised(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        with patch("swarm.tools.aws_tools._boto_client", side_effect=NoRegionError()):
            from swarm.tools.aws_tools import list_public_s3_buckets

            result = list_public_s3_buckets.run("")

        assert "Vault ID:" in result
        assert "Error listing S3 buckets: NoRegionError" in result

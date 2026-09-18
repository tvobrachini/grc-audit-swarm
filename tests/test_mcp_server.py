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
    get_iam_password_policy,
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

        assert "Finding: No account password policy defined." in result


# ─── list_iam_users_with_mfa ─────────────────────────────────────────────────


class TestListIamUsersWithMfa:
    def _make_client(self, users, mfa_map):
        mock_client = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value = [{"Users": users}]
        mock_client.get_paginator.return_value = paginator

        def list_mfa(UserName):
            devices = mfa_map.get(UserName, [])
            return {"MFADevices": devices}

        mock_client.list_mfa_devices.side_effect = list_mfa
        return mock_client

    def test_user_with_mfa_marked_yes(self):
        users = [{"UserName": "alice"}]
        client = self._make_client(users, {"alice": [{"SerialNumber": "arn:..."}]})

        with patch("boto3.client", return_value=client):
            result = list_iam_users_with_mfa()

        data = json.loads(result)
        assert data[0]["UserName"] == "alice"
        assert data[0]["MFA_Enabled"] == "Yes"

    def test_user_without_mfa_marked_no(self):
        users = [{"UserName": "bob"}]
        client = self._make_client(users, {"bob": []})

        with patch("boto3.client", return_value=client):
            result = list_iam_users_with_mfa()

        data = json.loads(result)
        assert data[0]["UserName"] == "bob"
        assert data[0]["MFA_Enabled"] == "No"

    def test_client_error_marked_unknown(self):
        users = [{"UserName": "carol"}]
        client = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value = [{"Users": users}]
        client.get_paginator.return_value = paginator
        client.list_mfa_devices.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": ""}},
            "ListMFADevices",
        )

        with patch("boto3.client", return_value=client):
            result = list_iam_users_with_mfa()

        data = json.loads(result)
        assert data[0]["UserName"] == "carol"
        assert data[0]["MFA_Enabled"] == "Unknown"


# ─── list_public_s3_buckets ──────────────────────────────────────────────────


class TestListPublicS3Buckets:
    def _make_s3_client(self, buckets, pab_configs, acl_grants=None):
        mock_client = MagicMock()
        mock_client.list_buckets.return_value = {"Buckets": buckets}

        def get_pab(Bucket):
            cfg = pab_configs.get(Bucket)
            if isinstance(cfg, Exception):
                raise cfg
            return {"PublicAccessBlockConfiguration": cfg}

        def get_acl(Bucket):
            grants = (acl_grants or {}).get(Bucket)
            if isinstance(grants, Exception):
                raise grants
            return {"Grants": grants or []}

        mock_client.get_public_access_block.side_effect = get_pab
        mock_client.get_bucket_acl.side_effect = get_acl
        return mock_client

    def _fully_blocked_pab(self):
        return {
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        }

    def test_fully_blocked_bucket_not_flagged_public(self):
        client = self._make_s3_client(
            [{"Name": "private-bucket"}],
            {"private-bucket": self._fully_blocked_pab()},
        )
        with patch("boto3.client", return_value=client):
            result = list_public_s3_buckets()

        data = json.loads(result)
        assert data[0]["Bucket"] == "private-bucket"
        assert data[0]["IsPublic"] is False

    def test_no_pab_config_flags_bucket_public(self):
        no_pab_error = ClientError(
            {"Error": {"Code": "NoSuchPublicAccessBlockConfiguration", "Message": ""}},
            "GetPublicAccessBlock",
        )
        client = self._make_s3_client(
            [{"Name": "exposed-bucket"}],
            {"exposed-bucket": no_pab_error},
        )
        with patch("boto3.client", return_value=client):
            result = list_public_s3_buckets()

        data = json.loads(result)
        assert data[0]["IsPublic"] is True

    def test_public_acl_grant_flags_bucket_public(self):
        acl_grants = {
            "acl-bucket": [
                {
                    "Grantee": {
                        "Type": "Group",
                        "URI": "http://acs.amazonaws.com/groups/global/AllUsers",
                    },
                }
            ]
        }
        client = self._make_s3_client(
            [{"Name": "acl-bucket"}],
            {"acl-bucket": self._fully_blocked_pab()},
            acl_grants=acl_grants,
        )
        with patch("boto3.client", return_value=client):
            result = list_public_s3_buckets()

        data = json.loads(result)
        assert data[0]["IsPublic"] is True

    def test_acl_client_error_silently_ignored(self):
        acl_error = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": ""}},
            "GetBucketAcl",
        )
        client = self._make_s3_client(
            [{"Name": "restricted-bucket"}],
            {"restricted-bucket": self._fully_blocked_pab()},
            acl_grants={"restricted-bucket": acl_error},
        )
        with patch("boto3.client", return_value=client):
            result = list_public_s3_buckets()

        data = json.loads(result)
        assert data[0]["Bucket"] == "restricted-bucket"
        assert data[0]["IsPublic"] is False

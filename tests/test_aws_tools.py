"""
Tests for boto3-based AWS audit tools.
All AWS API calls are mocked — no real credentials required.
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _vault_result(tmp_path):
    """Return a fake vault registration result."""
    return {"vault_id": "test-vault-id", "sha256": "a" * 64}


# ─── get_iam_password_policy ─────────────────────────────────────────────────


class TestGetIamPasswordPolicy:
    def test_returns_vault_id_on_success(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        policy = {"MinimumPasswordLength": 14, "RequireSymbols": True}
        mock_client = MagicMock()
        mock_client.get_account_password_policy.return_value = {
            "PasswordPolicy": policy
        }

        with patch("swarm.tools.aws_tools._boto_client", return_value=mock_client):
            from swarm.tools.aws_tools import get_iam_password_policy

            result = get_iam_password_policy.run("")

        assert "Vault ID:" in result
        assert "MinimumPasswordLength" in result

    def test_no_policy_defined_returns_finding(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        from botocore.exceptions import ClientError

        mock_client = MagicMock()
        mock_client.exceptions.NoSuchEntityException = ClientError
        mock_client.get_account_password_policy.side_effect = ClientError(
            {"Error": {"Code": "NoSuchEntity", "Message": ""}},
            "GetAccountPasswordPolicy",
        )

        with patch("swarm.tools.aws_tools._boto_client", return_value=mock_client):
            from swarm.tools.aws_tools import get_iam_password_policy

            result = get_iam_password_policy.run("")

        assert "Vault ID:" in result
        assert "No IAM password policy" in result

    def test_account_id_not_in_output(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        mock_client = MagicMock()
        mock_client.get_account_password_policy.return_value = {
            "PasswordPolicy": {"AccountId": "123456789012", "MinimumPasswordLength": 8}
        }

        with patch("swarm.tools.aws_tools._boto_client", return_value=mock_client):
            from swarm.tools.aws_tools import get_iam_password_policy

            result = get_iam_password_policy.run("")

        assert "123456789012" not in result


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

    def test_user_with_mfa_marked_yes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        users = [{"UserName": "alice"}]
        client = self._make_client(users, {"alice": [{"SerialNumber": "arn:..."}]})

        with patch("swarm.tools.aws_tools._boto_client", return_value=client):
            from swarm.tools.aws_tools import list_iam_users_with_mfa

            result = list_iam_users_with_mfa.run("")

        assert '"MFA_Enabled": "Yes"' in result

    def test_user_without_mfa_marked_no(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        users = [{"UserName": "bob"}]
        client = self._make_client(users, {"bob": []})

        with patch("swarm.tools.aws_tools._boto_client", return_value=client):
            from swarm.tools.aws_tools import list_iam_users_with_mfa

            result = list_iam_users_with_mfa.run("")

        assert '"MFA_Enabled": "No"' in result

    def test_multiple_users_all_reported(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        users = [{"UserName": "alice"}, {"UserName": "bob"}]
        client = self._make_client(users, {"alice": [{"SerialNumber": "x"}], "bob": []})

        with patch("swarm.tools.aws_tools._boto_client", return_value=client):
            from swarm.tools.aws_tools import list_iam_users_with_mfa

            result = list_iam_users_with_mfa.run("")

        assert "alice" in result
        assert "bob" in result


# ─── list_public_s3_buckets ──────────────────────────────────────────────────


class TestListPublicS3Buckets:
    def _make_s3_client(self, buckets, pab_configs, acl_grants=None):
        """
        buckets: list of {"Name": "..."} dicts
        pab_configs: dict of bucket_name → PublicAccessBlockConfiguration dict (or ClientError)
        acl_grants: dict of bucket_name → Grants list
        """
        from botocore.exceptions import ClientError

        mock_client = MagicMock()
        mock_client.list_buckets.return_value = {"Buckets": buckets}

        def get_pab(Bucket):
            cfg = pab_configs.get(Bucket, {})
            if isinstance(cfg, ClientError):
                raise cfg
            return {"PublicAccessBlockConfiguration": cfg}

        def get_acl(Bucket):
            grants = (acl_grants or {}).get(Bucket, [])
            return {"Grants": grants}

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

    def test_fully_blocked_bucket_not_flagged_public(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        client = self._make_s3_client(
            [{"Name": "private-bucket"}],
            {"private-bucket": self._fully_blocked_pab()},
        )
        with patch("swarm.tools.aws_tools._boto_client", return_value=client):
            from swarm.tools.aws_tools import list_public_s3_buckets

            result = list_public_s3_buckets.run("")

        data = json.loads(result.split("\nRaw Output: ", 1)[1])
        assert data[0]["IsPublic"] is False

    def test_no_pab_config_flags_bucket_public(self, tmp_path, monkeypatch):
        from botocore.exceptions import ClientError

        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        no_pab_error = ClientError(
            {"Error": {"Code": "NoSuchPublicAccessBlockConfiguration", "Message": ""}},
            "GetPublicAccessBlock",
        )
        client = self._make_s3_client(
            [{"Name": "exposed-bucket"}],
            {"exposed-bucket": no_pab_error},
        )
        with patch("swarm.tools.aws_tools._boto_client", return_value=client):
            from swarm.tools.aws_tools import list_public_s3_buckets

            result = list_public_s3_buckets.run("")

        data = json.loads(result.split("\nRaw Output: ", 1)[1])
        assert data[0]["IsPublic"] is True

    def test_public_acl_grant_flags_bucket_public(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        acl_grants = {
            "acl-bucket": [
                {
                    "Grantee": {
                        "Type": "Group",
                        "URI": "http://acs.amazonaws.com/groups/global/AllUsers",
                    },
                    "Permission": "READ",
                }
            ]
        }
        client = self._make_s3_client(
            [{"Name": "acl-bucket"}],
            {
                "acl-bucket": self._fully_blocked_pab()
            },  # PAB is on, but ACL has public grant
            acl_grants=acl_grants,
        )
        with patch("swarm.tools.aws_tools._boto_client", return_value=client):
            from swarm.tools.aws_tools import list_public_s3_buckets

            result = list_public_s3_buckets.run("")

        data = json.loads(result.split("\nRaw Output: ", 1)[1])
        assert data[0]["IsPublic"] is True
        assert data[0]["ACL"] == "Public"

    def test_multiple_buckets_mixed_visibility(self, tmp_path, monkeypatch):
        from botocore.exceptions import ClientError

        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        no_pab_error = ClientError(
            {"Error": {"Code": "NoSuchPublicAccessBlockConfiguration", "Message": ""}},
            "GetPublicAccessBlock",
        )
        client = self._make_s3_client(
            [{"Name": "private-one"}, {"Name": "public-one"}],
            {"private-one": self._fully_blocked_pab(), "public-one": no_pab_error},
        )
        with patch("swarm.tools.aws_tools._boto_client", return_value=client):
            from swarm.tools.aws_tools import list_public_s3_buckets

            result = list_public_s3_buckets.run("")

        data = json.loads(result.split("\nRaw Output: ", 1)[1])
        by_name = {d["Bucket"]: d for d in data}
        assert by_name["private-one"]["IsPublic"] is False
        assert by_name["public-one"]["IsPublic"] is True

    def test_vault_id_present_in_output(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        client = self._make_s3_client([], {})
        with patch("swarm.tools.aws_tools._boto_client", return_value=client):
            from swarm.tools.aws_tools import list_public_s3_buckets

            result = list_public_s3_buckets.run("")

        assert "Vault ID:" in result

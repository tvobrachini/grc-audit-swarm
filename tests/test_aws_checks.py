"""
Unit tests for swarm.tools.aws_checks using botocore's Stubber.

Stubber validates every request and response against the real AWS service
model, so these tests pin the exact API calls and parameters (including the
S3 Control AccountId and IAM pagination markers). They also cover the
branches moto does not implement faithfully (see tests/eval/):
GetBucketPolicyStatus, AccessDenied on individual reads, and IAM pagination.
"""

import os
import sys

import boto3
import pytest
from botocore.stub import Stubber

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm.tools.aws_checks import (  # noqa: E402
    NO_PASSWORD_POLICY_FINDING,
    check_bucket_public_access,
    collect_iam_users_mfa,
    collect_password_policy,
    collect_s3_public_access,
    describe_error,
    get_account_bpa,
)

ACCOUNT_ID = "111122223333"
ALL_USERS = "http://acs.amazonaws.com/groups/global/AllUsers"
AUTH_USERS = "http://acs.amazonaws.com/groups/global/AuthenticatedUsers"
LOG_DELIVERY = "http://acs.amazonaws.com/groups/s3/LogDelivery"
OWNER = {"ID": "a" * 64, "DisplayName": "owner"}


def _client(service):
    return boto3.client(
        service,
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",  # pragma: allowlist secret
    )


def _bpa(**flags):
    return {
        "PublicAccessBlockConfiguration": {
            "BlockPublicAcls": flags.get("BlockPublicAcls", False),
            "IgnorePublicAcls": flags.get("IgnorePublicAcls", False),
            "BlockPublicPolicy": flags.get("BlockPublicPolicy", False),
            "RestrictPublicBuckets": flags.get("RestrictPublicBuckets", False),
        }
    }


def _layer(**flags):
    return {"status": "configured", **_bpa(**flags)["PublicAccessBlockConfiguration"]}


NOT_CONFIGURED = {"status": "not configured"}
UNKNOWN_LAYER = {"status": "unknown", "error": "AccessDenied: denied"}


def _acl(*grants):
    return {
        "Owner": OWNER,
        "Grants": [
            {
                "Grantee": {"Type": "CanonicalUser", **OWNER},
                "Permission": "FULL_CONTROL",
            }
        ]
        + [
            {"Grantee": {"Type": "Group", "URI": uri}, "Permission": perm}
            for uri, perm in grants
        ],
    }


class _Bucket:
    """Queue the three per-bucket reads on an S3 Stubber, in call order."""

    def __init__(self, stub: Stubber, name: str = "bkt"):
        self.stub, self.name = stub, name

    def bpa(self, response=None, error=None):
        if error:
            self.stub.add_client_error(
                "get_public_access_block",
                service_error_code=error,
                expected_params={"Bucket": self.name},
            )
        else:
            self.stub.add_response(
                "get_public_access_block", response, {"Bucket": self.name}
            )
        return self

    def policy(self, is_public=None, error=None):
        if error:
            self.stub.add_client_error(
                "get_bucket_policy_status",
                service_error_code=error,
                expected_params={"Bucket": self.name},
            )
        else:
            self.stub.add_response(
                "get_bucket_policy_status",
                {"PolicyStatus": {"IsPublic": is_public}},
                {"Bucket": self.name},
            )
        return self

    def acl(self, response=None, error=None):
        if error:
            self.stub.add_client_error(
                "get_bucket_acl",
                service_error_code=error,
                expected_params={"Bucket": self.name},
            )
        else:
            self.stub.add_response("get_bucket_acl", response, {"Bucket": self.name})
        return self


def _evaluate(
    account_layer,
    *,
    bpa=None,
    bpa_error=None,
    policy=None,
    policy_error=None,
    acl=None,
    acl_error=None,
):
    s3 = _client("s3")
    with Stubber(s3) as stub:
        _Bucket(stub).bpa(bpa, bpa_error).policy(policy, policy_error).acl(
            acl, acl_error
        )
        result = check_bucket_public_access(s3, "bkt", account_layer)
        stub.assert_no_pending_responses()
    return result


NO_BPA = "NoSuchPublicAccessBlockConfiguration"


# ─── Bucket decision logic ───────────────────────────────────────────────────


class TestBucketVerdict:
    def test_nothing_public_no_bpa_is_not_public(self):
        r = _evaluate(
            NOT_CONFIGURED,
            bpa_error=NO_BPA,
            policy_error="NoSuchBucketPolicy",
            acl=_acl(),
        )
        assert r["Verdict"] == "NOT_PUBLIC"
        assert r["EffectivePublic"] is False
        assert r["Reasons"] == [] and r["Unknowns"] == []
        assert r["PolicyIsPublic"] is False

    def test_partial_bpa_with_nothing_public_is_not_public(self):
        """Regression: the old check flagged any partial BPA as public."""
        r = _evaluate(
            NOT_CONFIGURED,
            bpa=_bpa(BlockPublicAcls=True),
            policy_error="NoSuchBucketPolicy",
            acl=_acl(),
        )
        assert r["Verdict"] == "NOT_PUBLIC"
        assert r["BlockPublicAccess"]["Effective"]["BlockPublicAcls"] is True
        assert r["BlockPublicAccess"]["Effective"]["IgnorePublicAcls"] is False

    def test_public_policy_is_public(self):
        r = _evaluate(NOT_CONFIGURED, bpa_error=NO_BPA, policy=True, acl=_acl())
        assert r["Verdict"] == "PUBLIC"
        assert r["Reasons"] == ["public bucket policy"]

    def test_non_public_policy_is_not_public(self):
        r = _evaluate(NOT_CONFIGURED, bpa_error=NO_BPA, policy=False, acl=_acl())
        assert r["Verdict"] == "NOT_PUBLIC"
        assert r["PolicyIsPublic"] is False

    def test_public_policy_neutralised_by_bucket_restrict_public_buckets(self):
        r = _evaluate(
            NOT_CONFIGURED,
            bpa=_bpa(RestrictPublicBuckets=True),
            policy=True,
            acl=_acl(),
        )
        assert r["Verdict"] == "NOT_PUBLIC"
        assert "RestrictPublicBuckets is on at the bucket level" in r["Notes"][0]

    def test_public_policy_neutralised_by_account_restrict_public_buckets(self):
        r = _evaluate(
            _layer(RestrictPublicBuckets=True),
            bpa_error=NO_BPA,
            policy=True,
            acl=_acl(),
        )
        assert r["Verdict"] == "NOT_PUBLIC"
        assert "account level" in r["Notes"][0]

    def test_block_public_policy_alone_does_not_neutralise_existing_policy(self):
        """BlockPublicPolicy only rejects new public policies; it does not
        restrict access through a public policy already in place."""
        r = _evaluate(
            NOT_CONFIGURED,
            bpa=_bpa(BlockPublicPolicy=True, BlockPublicAcls=True),
            policy=True,
            acl=_acl(),
        )
        assert r["Verdict"] == "PUBLIC"

    @pytest.mark.parametrize(
        "uri,perm,reason",
        [
            (ALL_USERS, "READ", "ACL grants AllUsers READ"),
            (AUTH_USERS, "READ", "ACL grants AuthenticatedUsers READ"),
            (ALL_USERS, "WRITE", "ACL grants AllUsers WRITE"),
            (AUTH_USERS, "FULL_CONTROL", "ACL grants AuthenticatedUsers FULL_CONTROL"),
        ],
    )
    def test_public_acl_grants_are_public(self, uri, perm, reason):
        r = _evaluate(
            NOT_CONFIGURED,
            bpa_error=NO_BPA,
            policy_error="NoSuchBucketPolicy",
            acl=_acl((uri, perm)),
        )
        assert r["Verdict"] == "PUBLIC"
        assert r["Reasons"] == [reason]

    def test_log_delivery_group_is_not_public(self):
        r = _evaluate(
            NOT_CONFIGURED,
            bpa_error=NO_BPA,
            policy_error="NoSuchBucketPolicy",
            acl=_acl((LOG_DELIVERY, "WRITE")),
        )
        assert r["Verdict"] == "NOT_PUBLIC"
        assert r["PublicAclGrants"] == []

    def test_public_acl_ignored_by_account_ignore_public_acls(self):
        r = _evaluate(
            _layer(IgnorePublicAcls=True),
            bpa_error=NO_BPA,
            policy_error="NoSuchBucketPolicy",
            acl=_acl((ALL_USERS, "READ")),
        )
        assert r["Verdict"] == "NOT_PUBLIC"
        assert r["PublicAclGrants"] == ["AllUsers READ"]
        assert "IgnorePublicAcls is on at the account level" in r["Notes"][0]

    def test_block_public_acls_alone_does_not_neutralise_existing_acl(self):
        r = _evaluate(
            NOT_CONFIGURED,
            bpa=_bpa(BlockPublicAcls=True),
            policy_error="NoSuchBucketPolicy",
            acl=_acl((ALL_USERS, "READ")),
        )
        assert r["Verdict"] == "PUBLIC"

    def test_policy_and_acl_both_public_list_both_reasons(self):
        r = _evaluate(
            NOT_CONFIGURED, bpa_error=NO_BPA, policy=True, acl=_acl((ALL_USERS, "READ"))
        )
        assert r["Reasons"] == ["public bucket policy", "ACL grants AllUsers READ"]

    def test_effective_flags_are_account_or_bucket(self):
        r = _evaluate(
            _layer(IgnorePublicAcls=True),
            bpa=_bpa(RestrictPublicBuckets=True),
            policy=True,
            acl=_acl((ALL_USERS, "READ")),
        )
        eff = r["BlockPublicAccess"]["Effective"]
        assert eff == {
            "BlockPublicAcls": False,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": False,
            "RestrictPublicBuckets": True,
        }
        assert r["Verdict"] == "NOT_PUBLIC"


class TestUnknowns:
    def test_policy_status_access_denied_is_unknown_not_guessed(self):
        r = _evaluate(
            NOT_CONFIGURED, bpa_error=NO_BPA, policy_error="AccessDenied", acl=_acl()
        )
        assert r["Verdict"] == "UNKNOWN"
        assert r["EffectivePublic"] is None
        assert r["PolicyIsPublic"] is None
        assert "bucket policy status unknown (AccessDenied" in r["Unknowns"][0]

    def test_policy_status_unknown_but_restrict_public_buckets_on_is_not_public(self):
        r = _evaluate(
            NOT_CONFIGURED,
            bpa=_bpa(RestrictPublicBuckets=True),
            policy_error="AccessDenied",
            acl=_acl(),
        )
        assert r["Verdict"] == "NOT_PUBLIC"

    def test_failed_read_is_surfaced_even_when_verdict_is_decided(self):
        r = _evaluate(
            NOT_CONFIGURED,
            bpa=_bpa(RestrictPublicBuckets=True),
            policy_error="AccessDenied",
            acl=_acl(),
        )
        assert r["Verdict"] == "NOT_PUBLIC"
        assert r["Unknowns"] == ["bucket policy status unknown (AccessDenied)"]

    def test_acl_access_denied_is_unknown(self):
        r = _evaluate(
            NOT_CONFIGURED, bpa_error=NO_BPA, policy=False, acl_error="AccessDenied"
        )
        assert r["Verdict"] == "UNKNOWN"
        assert r["PublicAclGrants"] is None
        assert any("bucket ACL unknown" in u for u in r["Unknowns"])

    def test_known_public_wins_over_unknown(self):
        r = _evaluate(
            NOT_CONFIGURED, bpa_error=NO_BPA, policy=True, acl_error="AccessDenied"
        )
        assert r["Verdict"] == "PUBLIC"
        assert r["Unknowns"]

    def test_bucket_bpa_denied_with_public_acl_is_unknown(self):
        r = _evaluate(
            NOT_CONFIGURED,
            bpa_error="AccessDenied",
            policy_error="NoSuchBucketPolicy",
            acl=_acl((ALL_USERS, "READ")),
        )
        assert r["Verdict"] == "UNKNOWN"
        assert r["BlockPublicAccess"]["Bucket"]["status"] == "unknown"
        assert any(
            "IgnorePublicAcls could not be determined" in u for u in r["Unknowns"]
        )
        assert any(
            "bucket-level Block Public Access unknown" in u for u in r["Unknowns"]
        )

    def test_account_bpa_unknown_but_bucket_ignores_acls_is_not_public(self):
        r = _evaluate(
            UNKNOWN_LAYER,
            bpa=_bpa(IgnorePublicAcls=True),
            policy_error="NoSuchBucketPolicy",
            acl=_acl((ALL_USERS, "READ")),
        )
        assert r["Verdict"] == "NOT_PUBLIC"
        assert any(
            "account-level Block Public Access unknown" in u for u in r["Unknowns"]
        )

    def test_policy_status_without_is_public_is_unknown(self):
        s3 = _client("s3")
        with Stubber(s3) as stub:
            b = _Bucket(stub).bpa(error=NO_BPA)
            stub.add_response(
                "get_bucket_policy_status", {"PolicyStatus": {}}, {"Bucket": "bkt"}
            )
            b.acl(_acl())
            r = check_bucket_public_access(s3, "bkt", NOT_CONFIGURED)
        assert r["Verdict"] == "UNKNOWN"


# ─── Account-level Block Public Access ───────────────────────────────────────


class TestAccountBpa:
    def test_reads_account_bpa_with_caller_account_id(self):
        sts, s3control = _client("sts"), _client("s3control")
        with Stubber(sts) as sts_stub, Stubber(s3control) as ctl_stub:
            sts_stub.add_response(
                "get_caller_identity",
                {
                    "Account": ACCOUNT_ID,
                    "UserId": "AIDEXAMPLE",
                    "Arn": f"arn:aws:iam::{ACCOUNT_ID}:user/auditor",
                },
                {},
            )
            ctl_stub.add_response(
                "get_public_access_block",
                _bpa(IgnorePublicAcls=True, RestrictPublicBuckets=True),
                {"AccountId": ACCOUNT_ID},
            )
            layer = get_account_bpa(sts, s3control)
        assert layer["status"] == "configured"
        assert layer["IgnorePublicAcls"] is True
        assert layer["BlockPublicAcls"] is False
        assert ACCOUNT_ID not in repr(layer)

    def test_not_configured(self):
        sts, s3control = _client("sts"), _client("s3control")
        with Stubber(sts) as sts_stub, Stubber(s3control) as ctl_stub:
            sts_stub.add_response("get_caller_identity", {"Account": ACCOUNT_ID}, {})
            ctl_stub.add_client_error(
                "get_public_access_block",
                service_error_code=NO_BPA,
                expected_params={"AccountId": ACCOUNT_ID},
            )
            layer = get_account_bpa(sts, s3control)
        assert layer == {"status": "not configured"}

    def test_access_denied_is_unknown_and_redacted(self, caplog):
        sts, s3control = _client("sts"), _client("s3control")
        message = f"User: arn:aws:iam::{ACCOUNT_ID}:user/auditor is not authorized"
        with Stubber(sts) as sts_stub, Stubber(s3control) as ctl_stub:
            sts_stub.add_response("get_caller_identity", {"Account": ACCOUNT_ID}, {})
            ctl_stub.add_client_error(
                "get_public_access_block",
                service_error_code="AccessDenied",
                service_message=message,
                expected_params={"AccountId": ACCOUNT_ID},
            )
            with caplog.at_level("WARNING"):
                layer = get_account_bpa(sts, s3control)
        assert layer["status"] == "unknown"
        assert "AccessDenied" in layer["error"]
        assert ACCOUNT_ID not in layer["error"]
        assert ACCOUNT_ID not in caplog.text

    def test_sts_failure_is_unknown(self):
        sts, s3control = _client("sts"), _client("s3control")
        with Stubber(sts) as sts_stub:
            sts_stub.add_client_error(
                "get_caller_identity", service_error_code="ExpiredToken"
            )
            layer = get_account_bpa(sts, s3control)
        assert layer["status"] == "unknown"
        assert "ExpiredToken" in layer["error"]


class TestCollectS3:
    def test_end_to_end_single_bucket_never_outputs_account_id(self):
        sts, s3control, s3 = _client("sts"), _client("s3control"), _client("s3")
        with (
            Stubber(sts) as sts_stub,
            Stubber(s3control) as ctl_stub,
            Stubber(s3) as s3_stub,
        ):
            sts_stub.add_response("get_caller_identity", {"Account": ACCOUNT_ID}, {})
            ctl_stub.add_client_error(
                "get_public_access_block", service_error_code=NO_BPA
            )
            s3_stub.add_response(
                "list_buckets", {"Buckets": [{"Name": "bkt"}], "Owner": OWNER}
            )
            _Bucket(s3_stub).bpa(error=NO_BPA).policy(True).acl(_acl())
            result = collect_s3_public_access(s3, s3control, sts)
        assert result["Summary"] == {
            "buckets": 1,
            "public": 1,
            "not_public": 0,
            "unknown": 0,
        }
        assert result["Buckets"][0]["Verdict"] == "PUBLIC"
        assert ACCOUNT_ID not in repr(result)


# ─── IAM ─────────────────────────────────────────────────────────────────────


class TestIam:
    def test_no_password_policy_is_a_finding_not_an_error(self):
        iam = _client("iam")
        with Stubber(iam) as stub:
            stub.add_client_error(
                "get_account_password_policy",
                service_error_code="NoSuchEntity",
                http_status_code=404,
            )
            out = collect_password_policy(iam)
        assert out == NO_PASSWORD_POLICY_FINDING
        assert not out.startswith("Error")

    def test_password_policy_access_denied_is_an_error(self):
        iam = _client("iam")
        with Stubber(iam) as stub:
            stub.add_client_error(
                "get_account_password_policy", service_error_code="AccessDenied"
            )
            out = collect_password_policy(iam)
        assert out.startswith("Error fetching password policy: AccessDenied")

    def test_list_users_follows_pagination_marker(self):
        iam = _client("iam")
        created = "2024-01-01T00:00:00Z"

        def user(name):
            return {
                "UserName": name,
                "UserId": "AIDA" + "X" * 16,
                "Arn": f"arn:aws:iam::{ACCOUNT_ID}:user/{name}",
                "Path": "/",
                "CreateDate": created,
            }

        with Stubber(iam) as stub:
            stub.add_response(
                "list_users",
                {
                    "Users": [user("u1"), user("u2")],
                    "IsTruncated": True,
                    "Marker": "page2",
                },
                {},
            )
            stub.add_response(
                "list_users",
                {"Users": [user("u3")], "IsTruncated": False},
                {"Marker": "page2"},
            )
            for _ in range(3):
                stub.add_response(
                    "list_mfa_devices", {"MFADevices": [], "IsTruncated": False}
                )
            result = collect_iam_users_mfa(iam)
            stub.assert_no_pending_responses()
        assert sorted(u["UserName"] for u in result["Users"]) == ["u1", "u2", "u3"]
        assert result["Summary"]["without_mfa"] == 3

    def test_list_mfa_devices_follows_pagination_marker(self):
        iam = _client("iam")
        device = {
            "UserName": "u1",
            "SerialNumber": "arn:aws:iam::" + ACCOUNT_ID + ":mfa/u1",
            "EnableDate": "2024-01-01T00:00:00Z",
        }
        with Stubber(iam) as stub:
            stub.add_response(
                "list_users",
                {
                    "Users": [
                        {
                            "UserName": "u1",
                            "UserId": "AIDA" + "X" * 16,
                            "Arn": "arn:aws:iam::" + ACCOUNT_ID + ":user/u1",
                            "Path": "/",
                            "CreateDate": "2024-01-01T00:00:00Z",
                        }
                    ],
                    "IsTruncated": False,
                },
                {},
            )
            stub.add_response(
                "list_mfa_devices",
                {"MFADevices": [], "IsTruncated": True, "Marker": "m2"},
                {"UserName": "u1"},
            )
            stub.add_response(
                "list_mfa_devices",
                {"MFADevices": [device], "IsTruncated": False},
                {"UserName": "u1", "Marker": "m2"},
            )
            result = collect_iam_users_mfa(iam)
        assert result["Users"] == [
            {"UserName": "u1", "MFA_Enabled": "Yes", "MFADeviceCount": 1}
        ]

    def test_mfa_access_denied_is_unknown(self):
        iam = _client("iam")
        with Stubber(iam) as stub:
            stub.add_response(
                "list_users",
                {
                    "Users": [
                        {
                            "UserName": "u1",
                            "UserId": "AIDA" + "X" * 16,
                            "Arn": "arn:aws:iam::" + ACCOUNT_ID + ":user/u1",
                            "Path": "/",
                            "CreateDate": "2024-01-01T00:00:00Z",
                        }
                    ],
                    "IsTruncated": False,
                },
                {},
            )
            stub.add_client_error("list_mfa_devices", service_error_code="AccessDenied")
            result = collect_iam_users_mfa(iam)
        assert result["Users"][0]["MFA_Enabled"] == "Unknown"
        assert result["Summary"]["unknown"] == 1

    def test_notes_state_root_user_is_not_covered(self):
        iam = _client("iam")
        with Stubber(iam) as stub:
            stub.add_response("list_users", {"Users": [], "IsTruncated": False}, {})
            result = collect_iam_users_mfa(iam)
        assert any(
            "root user is not returned by iam:ListUsers" in n for n in result["Notes"]
        )


def test_describe_error_redacts_account_ids():
    from botocore.exceptions import ClientError

    err = ClientError(
        {
            "Error": {
                "Code": "AccessDenied",
                "Message": f"arn:aws:iam::{ACCOUNT_ID}:user/x",
            }
        },
        "Op",
    )
    assert describe_error(err) == "AccessDenied: arn:aws:iam::[REDACTED]:user/x"

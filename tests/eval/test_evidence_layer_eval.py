"""
Evidence-layer evaluation: planted misconfigurations in an in-memory AWS account.

Scope. This evaluates the deterministic evidence collection only: the CrewAI
tool functions in swarm.tools.aws_tools, run against a moto account seeded with
known misconfigurations and known-good controls. Each scenario declares the
evidence it expects, and the test checks the tool output, that the output is
account-ID-redacted, and that it was registered in the evidence vault (the
record exists and an exact quote from it verifies). It does NOT evaluate the
LLM agents' judgement or the quality of the report.

Each scenario runs in its own fresh moto account, because account-level Block
Public Access applies to every bucket in an account.

Where moto is not faithful to AWS (checked against moto 5.x), these tests do
not assert moto's behaviour; the branch is covered with botocore Stubber in
tests/test_aws_checks.py instead:

- GetBucketPolicyStatus: moto returns an empty PolicyStatus (no IsPublic) for
  every bucket, and does not raise NoSuchBucketPolicy for a bucket without a
  policy. Here, the S3 client is wrapped so GetBucketPolicyStatus answers from
  moto's stored policy (NoSuchBucketPolicy if there is none) and, if there is
  one, the IsPublic value the scenario declares AWS would return for the
  planted policy. The planted policies are unambiguous (Principal "*" with no
  condition is public per AWS's documented rules).
- Block Public Access enforcement: moto accepts a public ACL or policy even
  when BlockPublicAcls/BlockPublicPolicy is on, where AWS would reject it.
  Scenarios therefore plant the ACL or policy before turning on BPA, which is
  the order that also works on real AWS.
- Canned ACL "public-read-write": moto's GetBucketAcl returns only the
  AllUsers READ grant; AWS returns READ and WRITE. That scenario sets the two
  grants explicitly (GrantRead/GrantWrite), which moto returns faithfully.
- IAM pagination: moto ignores MaxItems and returns every user in one page, so
  the 60-user scenario below does not exercise pagination; the Marker
  handling is covered by Stubber tests.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Callable
from unittest.mock import patch

import boto3
import pytest
from botocore.exceptions import ClientError

from moto import mock_aws

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from swarm.evidence import EvidenceAssuranceProtocol  # noqa: E402

# Any 12-digit run (bare or 4-4-4) that could be an AWS account ID.
_ACCOUNT_ID_LIKE = re.compile(r"(?<!\d)\d{12}(?!\d)|(?<!\d)\d{4}-\d{4}-\d{4}(?!\d)")
_ALL_USERS = 'uri="http://acs.amazonaws.com/groups/global/AllUsers"'
_BLOCK_ALL = {
    "BlockPublicAcls": True,
    "IgnorePublicAcls": True,
    "BlockPublicPolicy": True,
    "RestrictPublicBuckets": True,
}


def _public_read_policy(bucket: str) -> str:
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "PlantedPublicRead",
                    "Effect": "Allow",
                    "Principal": "*",
                    "Action": "s3:GetObject",
                    "Resource": f"arn:aws:s3:::{bucket}/*",
                }
            ],
        }
    )


def _own_account_policy(bucket: str, account_id: str) -> str:
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": f"arn:aws:iam::{account_id}:root"},
                    "Action": "s3:GetObject",
                    "Resource": f"arn:aws:s3:::{bucket}/*",
                }
            ],
        }
    )


class _PolicyStatusFromScenario:
    """S3 client proxy that answers GetBucketPolicyStatus faithfully (see module
    docstring): NoSuchBucketPolicy from moto when no policy exists, otherwise
    the IsPublic value the scenario declares for the planted policy."""

    def __init__(self, client, declared: dict[str, bool]):
        self._client = client
        self._declared = declared

    def __getattr__(self, name):
        return getattr(self._client, name)

    def get_bucket_policy_status(self, Bucket: str) -> dict:
        self._client.get_bucket_policy(Bucket=Bucket)  # raises NoSuchBucketPolicy
        return {"PolicyStatus": {"IsPublic": self._declared[Bucket]}}


@dataclass
class Scenario:
    id: str
    tool: str
    plant: Callable[[dict[str, Any]], None]
    expected: Any
    # For S3: bucket -> IsPublic that AWS GetBucketPolicyStatus returns for the
    # planted policy (only consulted when the bucket has a policy).
    policy_is_public: dict[str, bool] = field(default_factory=dict)
    quote: str = ""


# ─── Planting helpers ────────────────────────────────────────────────────────


def _s3(env):
    return env["s3"]


def _bucket(
    env,
    name,
    acl=None,
    grants=None,
    policy=None,
    bucket_bpa=None,
    account_bpa=None,
):
    s3 = _s3(env)
    s3.create_bucket(Bucket=name)
    if acl:
        s3.put_bucket_acl(Bucket=name, ACL=acl)
    if grants:
        owner = s3.get_bucket_acl(Bucket=name)["Owner"]["ID"]
        s3.put_bucket_acl(Bucket=name, GrantFullControl=f'id="{owner}"', **grants)
    if policy:
        s3.put_bucket_policy(Bucket=name, Policy=policy)
    # BPA last: on real AWS, BlockPublicAcls/BlockPublicPolicy would reject the
    # public ACL/policy above if it were already on.
    if bucket_bpa:
        s3.put_public_access_block(
            Bucket=name, PublicAccessBlockConfiguration=bucket_bpa
        )
    if account_bpa:
        env["s3control"].put_public_access_block(
            AccountId=env["account_id"], PublicAccessBlockConfiguration=account_bpa
        )


def _password_policy(**policy):
    def plant(env):
        env["iam"].update_account_password_policy(**policy)

    return plant


def _plant_users(env):
    iam = env["iam"]
    for i in range(60):
        name = f"user-{i:02d}"
        iam.create_user(UserName=name)
        if i % 3 == 0:
            serial = iam.create_virtual_mfa_device(VirtualMFADeviceName=name)[
                "VirtualMFADevice"
            ]["SerialNumber"]
            iam.enable_mfa_device(
                UserName=name,
                SerialNumber=serial,
                AuthenticationCode1="123456",
                AuthenticationCode2="654321",
            )


# ─── Scenarios ───────────────────────────────────────────────────────────────

# Expected S3 evidence: bucket -> (Verdict, Reasons).
SCENARIOS = [
    Scenario(
        "iam-no-password-policy",
        "get_iam_password_policy",
        lambda env: None,
        {"finding": "No IAM account password policy is set"},
        quote="No IAM account password policy is set",
    ),
    Scenario(
        "iam-weak-password-policy",
        "get_iam_password_policy",
        _password_policy(
            MinimumPasswordLength=6,
            RequireSymbols=False,
            RequireNumbers=True,
            RequireUppercaseCharacters=False,
            RequireLowercaseCharacters=True,
        ),
        {
            "MinimumPasswordLength": 6,
            "RequireSymbols": False,
            "RequireUppercaseCharacters": False,
        },
        quote='"MinimumPasswordLength": 6',
    ),
    Scenario(
        "iam-strong-password-policy",
        "get_iam_password_policy",
        _password_policy(
            MinimumPasswordLength=14,
            RequireSymbols=True,
            RequireNumbers=True,
            RequireUppercaseCharacters=True,
            RequireLowercaseCharacters=True,
            MaxPasswordAge=90,
            PasswordReusePrevention=24,
        ),
        {
            "MinimumPasswordLength": 14,
            "RequireSymbols": True,
            "RequireNumbers": True,
            "RequireUppercaseCharacters": True,
            "RequireLowercaseCharacters": True,
            "MaxPasswordAge": 90,
            "PasswordReusePrevention": 24,
        },
        quote='"MinimumPasswordLength": 14',
    ),
    Scenario(
        "iam-users-mixed-mfa",
        "list_iam_users_with_mfa",
        _plant_users,
        {f"user-{i:02d}": ("Yes" if i % 3 == 0 else "No") for i in range(60)},
        quote='"UserName": "user-01",\n      "MFA_Enabled": "No"',
    ),
    Scenario(
        "s3-private",
        "list_public_s3_buckets",
        lambda env: _bucket(env, "private-data"),
        {"private-data": ("NOT_PUBLIC", [])},
        quote='"Verdict": "NOT_PUBLIC"',
    ),
    Scenario(
        "s3-private-policy-own-account",
        "list_public_s3_buckets",
        lambda env: _bucket(
            env,
            "shared-internal",
            policy=_own_account_policy("shared-internal", env["account_id"]),
        ),
        {"shared-internal": ("NOT_PUBLIC", [])},
        policy_is_public={"shared-internal": False},
        quote='"PolicyIsPublic": false',
    ),
    Scenario(
        "s3-acl-allusers",
        "list_public_s3_buckets",
        lambda env: _bucket(env, "acl-public-read", acl="public-read"),
        {"acl-public-read": ("PUBLIC", ["ACL grants AllUsers READ"])},
        quote="ACL grants AllUsers READ",
    ),
    Scenario(
        "s3-acl-allusers-read-write",
        "list_public_s3_buckets",
        # Explicit grants rather than the "public-read-write" canned ACL: moto
        # returns only the READ grant for that canned ACL (AWS returns both).
        lambda env: _bucket(
            env,
            "acl-public-rw",
            grants={"GrantRead": _ALL_USERS, "GrantWrite": _ALL_USERS},
        ),
        {
            "acl-public-rw": (
                "PUBLIC",
                ["ACL grants AllUsers READ", "ACL grants AllUsers WRITE"],
            )
        },
        quote="ACL grants AllUsers WRITE",
    ),
    Scenario(
        "s3-acl-authenticated-users",
        "list_public_s3_buckets",
        lambda env: _bucket(env, "acl-any-aws-account", acl="authenticated-read"),
        {"acl-any-aws-account": ("PUBLIC", ["ACL grants AuthenticatedUsers READ"])},
        quote="ACL grants AuthenticatedUsers READ",
    ),
    Scenario(
        "s3-policy-principal-star",
        "list_public_s3_buckets",
        lambda env: _bucket(
            env, "policy-public", policy=_public_read_policy("policy-public")
        ),
        {"policy-public": ("PUBLIC", ["public bucket policy"])},
        policy_is_public={"policy-public": True},
        quote="public bucket policy",
    ),
    Scenario(
        "s3-public-policy-bucket-restrict-public-buckets",
        "list_public_s3_buckets",
        lambda env: _bucket(
            env,
            "policy-restricted",
            policy=_public_read_policy("policy-restricted"),
            bucket_bpa={"RestrictPublicBuckets": True},
        ),
        {"policy-restricted": ("NOT_PUBLIC", [])},
        policy_is_public={"policy-restricted": True},
        quote="RestrictPublicBuckets is on at the bucket level",
    ),
    Scenario(
        "s3-public-acl-account-ignore-public-acls",
        "list_public_s3_buckets",
        lambda env: _bucket(
            env,
            "acl-ignored-by-account",
            acl="public-read",
            account_bpa={"IgnorePublicAcls": True, "BlockPublicAcls": True},
        ),
        {"acl-ignored-by-account": ("NOT_PUBLIC", [])},
        quote="IgnorePublicAcls is on at the account level",
    ),
    Scenario(
        "s3-public-acl-bucket-block-all",
        "list_public_s3_buckets",
        lambda env: _bucket(
            env, "acl-ignored-by-bucket", acl="public-read", bucket_bpa=_BLOCK_ALL
        ),
        {"acl-ignored-by-bucket": ("NOT_PUBLIC", [])},
        quote="IgnorePublicAcls is on at the bucket level",
    ),
    Scenario(
        # The old check reported any partial bucket-level BPA as public.
        "s3-partial-bpa-nothing-public",
        "list_public_s3_buckets",
        lambda env: _bucket(
            env,
            "partial-bpa",
            bucket_bpa={"BlockPublicAcls": True, "BlockPublicPolicy": True},
        ),
        {"partial-bpa": ("NOT_PUBLIC", [])},
        quote='"BlockPublicPolicy": true',
    ),
    Scenario(
        # BlockPublicAcls only stops new public ACLs; an existing one still
        # applies unless IgnorePublicAcls is on.
        "s3-public-acl-block-public-acls-only",
        "list_public_s3_buckets",
        lambda env: _bucket(
            env,
            "acl-block-only",
            acl="public-read",
            bucket_bpa={"BlockPublicAcls": True},
        ),
        {"acl-block-only": ("PUBLIC", ["ACL grants AllUsers READ"])},
        quote="ACL grants AllUsers READ",
    ),
    Scenario(
        # Bucket names often embed the account ID; evidence must not leak it.
        "s3-account-id-in-bucket-name",
        "list_public_s3_buckets",
        lambda env: _bucket(env, f"access-logs-{env['account_id']}"),
        {"access-logs-[REDACTED]": ("NOT_PUBLIC", [])},
        quote='"Bucket": "access-logs-[REDACTED]"',
    ),
]

_SCORECARD: list[tuple[str, str, str, bool]] = []


@pytest.fixture(scope="module", autouse=True)
def _print_scorecard():
    yield
    if not _SCORECARD:
        return
    width = max(len(r[0]) for r in _SCORECARD)
    lines = ["", "Evidence-layer evaluation scorecard", "-" * 34]
    for sid, expected, actual, ok in _SCORECARD:
        lines.append(
            f"{'PASS' if ok else 'FAIL'}  {sid:<{width}}  expected: {expected}"
            + ("" if ok else f"\n      {'':<{width}}  actual:   {actual}")
        )
    passed = sum(1 for r in _SCORECARD if r[3])
    lines.append(f"{passed}/{len(_SCORECARD)} scenarios matched")
    print("\n".join(lines))


@pytest.fixture
def aws_env(tmp_path, monkeypatch):
    monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.delenv("VAULT_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with mock_aws():
        env = {s: boto3.client(s) for s in ("iam", "s3", "s3control", "sts")}
        env["account_id"] = env["sts"].get_caller_identity()["Account"]
        env["vault"] = tmp_path / "vault"
        yield env


def _run_tool(scenario: Scenario, env) -> str:
    from swarm.tools import aws_tools

    def client_for(service):
        client = boto3.client(service)
        if service == "s3":
            return _PolicyStatusFromScenario(client, scenario.policy_is_public)
        return client

    with patch.object(aws_tools, "_boto_client", side_effect=client_for):
        return getattr(aws_tools, scenario.tool).run("")


def _actual(scenario: Scenario, raw: str) -> Any:
    if scenario.tool == "get_iam_password_policy":
        if raw.startswith("Finding:"):
            want = scenario.expected.get("finding", "")
            return {"finding": want if want and want in raw else raw}
        policy = json.loads(raw)
        return {k: policy.get(k) for k in scenario.expected}
    data = json.loads(raw)
    if scenario.tool == "list_iam_users_with_mfa":
        return {u["UserName"]: u["MFA_Enabled"] for u in data["Users"]}
    return {b["Bucket"]: (b["Verdict"], b["Reasons"]) for b in data["Buckets"]}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_planted_scenario(scenario: Scenario, aws_env):
    scenario.plant(aws_env)
    output = _run_tool(scenario, aws_env)

    vault_id, raw = output.removeprefix("Vault ID: ").split("\nRaw Output: ", 1)
    actual = _actual(scenario, raw)
    ok = actual == scenario.expected
    _SCORECARD.append(
        (scenario.id, _short(scenario.expected), _short(actual), bool(ok))
    )

    # 1. Evidence verdict matches what was planted.
    assert actual == scenario.expected

    # 2. Output is redacted: no account ID (or anything shaped like one).
    assert aws_env["account_id"] not in output
    # (Checked on the payload only: the vault ID and the stored hex digest
    # can contain 12 consecutive digits by chance.)
    assert not _ACCOUNT_ID_LIKE.search(raw)

    # 3. Output is registered in the vault, redacted there too, and an exact
    #    quote from it verifies while a fabricated one does not.
    record_path = aws_env["vault"] / f"{vault_id}.json"
    assert record_path.exists()
    record = json.loads(record_path.read_text())
    assert record["raw_payload"] == raw
    assert aws_env["account_id"] not in record_path.read_text()
    assert scenario.quote in raw
    assert EvidenceAssuranceProtocol.verify_exact_quote(vault_id, scenario.quote)
    assert not EvidenceAssuranceProtocol.verify_exact_quote(
        vault_id, scenario.quote + " (fabricated)"
    )


def test_s3_account_with_mixed_buckets(aws_env):
    """All S3 scenarios that can share one account, evaluated in a single call:
    the summary counts must match the planted buckets."""
    policy_public = {"policy-public": True, "policy-restricted": True}
    _bucket(aws_env, "private-data")
    _bucket(aws_env, "acl-public-read", acl="public-read")
    _bucket(aws_env, "acl-any-aws-account", acl="authenticated-read")
    _bucket(aws_env, "policy-public", policy=_public_read_policy("policy-public"))
    _bucket(
        aws_env,
        "policy-restricted",
        policy=_public_read_policy("policy-restricted"),
        bucket_bpa={"RestrictPublicBuckets": True},
    )
    _bucket(aws_env, "partial-bpa", bucket_bpa={"BlockPublicAcls": True})
    scenario = Scenario(
        "mixed", "list_public_s3_buckets", lambda env: None, None, policy_public
    )
    _, raw = _run_tool(scenario, aws_env).split("\nRaw Output: ", 1)
    data = json.loads(raw)

    assert data["AccountBlockPublicAccess"] == {"status": "not configured"}
    assert data["Summary"] == {
        "buckets": 6,
        "public": 3,
        "not_public": 3,
        "unknown": 0,
    }
    public = sorted(b["Bucket"] for b in data["Buckets"] if b["Verdict"] == "PUBLIC")
    assert public == ["acl-any-aws-account", "acl-public-read", "policy-public"]


def test_policy_status_proxy_raises_like_aws_without_policy(aws_env):
    """Guard for the moto workaround: without a policy the proxy must raise
    NoSuchBucketPolicy, as AWS does, rather than return a status."""
    aws_env["s3"].create_bucket(Bucket="no-policy")
    proxy = _PolicyStatusFromScenario(aws_env["s3"], {})
    with pytest.raises(ClientError) as exc:
        proxy.get_bucket_policy_status(Bucket="no-policy")
    assert exc.value.response["Error"]["Code"] == "NoSuchBucketPolicy"


def _short(value: Any) -> str:
    if isinstance(value, dict) and len(value) > 4:
        counts: dict[str, int] = {}
        for v in value.values():
            counts[str(v)] = counts.get(str(v), 0) + 1
        return f"{len(value)} entries " + json.dumps(counts, sort_keys=True)
    return json.dumps(value, default=str)

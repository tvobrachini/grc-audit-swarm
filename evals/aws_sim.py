"""Simulated AWS account (moto) seeded from a scenario's ``aws`` block.

Planting follows tests/eval/test_evidence_layer_eval.py, including its moto
workarounds (see that module's docstring for the details):

* moto's GetBucketPolicyStatus returns no IsPublic value, so the S3 client is
  wrapped to answer it from moto's stored policy plus the ``policy_is_public``
  value the scenario declares AWS would return;
* moto accepts public ACLs / policies that Block Public Access would reject,
  so BPA is applied after the ACLs and policies (the order that also works on
  real AWS).

The context manager also pins fake AWS credentials and removes AWS_PROFILE,
so an evaluation run can never reach a real AWS account.
"""

from __future__ import annotations

import contextlib
import json
import os
from typing import Any, Iterator
from unittest.mock import patch

import boto3

import evals  # noqa: F401  (puts src/ on sys.path)

_ALL_USERS = 'uri="http://acs.amazonaws.com/groups/global/AllUsers"'

_FAKE_AWS_ENV = {
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",  # pragma: allowlist secret (moto placeholder)
    "AWS_SESSION_TOKEN": "testing",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
}


def public_read_policy(bucket: str) -> str:
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


def own_account_policy(bucket: str, account_id: str) -> str:
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


class PolicyStatusProxy:
    """S3 client proxy answering GetBucketPolicyStatus as AWS would."""

    def __init__(self, client: Any, declared: dict[str, bool]):
        self._client = client
        self._declared = declared

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def get_bucket_policy_status(self, Bucket: str) -> dict:
        self._client.get_bucket_policy(Bucket=Bucket)  # raises NoSuchBucketPolicy
        return {"PolicyStatus": {"IsPublic": bool(self._declared.get(Bucket, False))}}


def mfa_less_indexes(count: int, without_mfa: int) -> set[int]:
    """Indexes of the users planted without MFA, spread evenly."""
    if without_mfa <= 0:
        return set()
    if without_mfa >= count:
        return set(range(count))
    return {i * count // without_mfa for i in range(without_mfa)}


def _plant_users(iam: Any, spec: dict[str, Any]) -> None:
    count = int(spec.get("count", 0))
    no_mfa = mfa_less_indexes(count, int(spec.get("without_mfa", 0)))
    for i in range(count):
        name = f"user-{i:02d}"
        iam.create_user(UserName=name)
        if i in no_mfa:
            continue
        serial = iam.create_virtual_mfa_device(VirtualMFADeviceName=name)[
            "VirtualMFADevice"
        ]["SerialNumber"]
        iam.enable_mfa_device(
            UserName=name,
            SerialNumber=serial,
            AuthenticationCode1="123456",
            AuthenticationCode2="654321",
        )


def _plant_bucket(env: dict[str, Any], spec: dict[str, Any]) -> None:
    s3 = env["s3"]
    name = spec["name"]
    s3.create_bucket(Bucket=name)
    if spec.get("acl"):
        s3.put_bucket_acl(Bucket=name, ACL=spec["acl"])
    if spec.get("grants"):
        owner = s3.get_bucket_acl(Bucket=name)["Owner"]["ID"]
        grants = {
            k: (_ALL_USERS if v == "AllUsers" else v)
            for k, v in dict(spec["grants"]).items()
        }
        s3.put_bucket_acl(Bucket=name, GrantFullControl=f'id="{owner}"', **grants)
    policy = spec.get("policy")
    if policy == "public_read":
        s3.put_bucket_policy(Bucket=name, Policy=public_read_policy(name))
    elif policy == "own_account":
        s3.put_bucket_policy(
            Bucket=name, Policy=own_account_policy(name, env["account_id"])
        )
    elif policy:
        raise ValueError(f"unknown planted policy {policy!r}")
    if spec.get("bucket_bpa"):
        s3.put_public_access_block(
            Bucket=name, PublicAccessBlockConfiguration=dict(spec["bucket_bpa"])
        )


def plant(env: dict[str, Any], aws: dict[str, Any]) -> dict[str, bool]:
    """Seed the account; return bucket -> IsPublic for GetBucketPolicyStatus."""
    unknown = set(aws) - {"password_policy", "users", "buckets", "account_bpa"}
    if unknown:
        raise ValueError(f"unknown aws plant keys: {sorted(unknown)}")
    if aws.get("password_policy"):
        env["iam"].update_account_password_policy(**dict(aws["password_policy"]))
    if aws.get("users"):
        _plant_users(env["iam"], dict(aws["users"]))
    declared: dict[str, bool] = {}
    for spec in aws.get("buckets") or []:
        _plant_bucket(env, dict(spec))
        if spec.get("policy"):
            declared[spec["name"]] = bool(spec.get("policy_is_public", False))
    if aws.get("account_bpa"):
        env["s3control"].put_public_access_block(
            AccountId=env["account_id"],
            PublicAccessBlockConfiguration=dict(aws["account_bpa"]),
        )
    return declared


@contextlib.contextmanager
def simulated_account(aws: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """A fresh moto account seeded from ``aws``, with the evidence tools
    (swarm.tools.aws_tools) wired to it for the duration of the block."""
    try:
        from moto import mock_aws
    except ImportError as exc:  # pragma: no cover - moto is a dev dependency
        raise RuntimeError(
            "moto is required for the evaluation harness: run `uv sync` "
            "(it is in the dev dependency group)."
        ) from exc
    from swarm.tools import aws_tools

    saved = {k: os.environ.get(k) for k in [*_FAKE_AWS_ENV, "AWS_PROFILE"]}
    os.environ.update(_FAKE_AWS_ENV)
    os.environ.pop("AWS_PROFILE", None)
    try:
        with mock_aws():
            env: dict[str, Any] = {
                s: boto3.client(s) for s in ("iam", "s3", "s3control", "sts")
            }
            env["account_id"] = env["sts"].get_caller_identity()["Account"]
            declared = plant(env, aws)

            def client_for(service: str) -> Any:
                client = boto3.client(service)
                if service == "s3":
                    return PolicyStatusProxy(client, declared)
                return client

            with patch.object(aws_tools, "_boto_client", side_effect=client_for):
                yield env
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

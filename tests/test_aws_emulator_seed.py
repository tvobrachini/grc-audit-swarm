"""Tests for scripts/seed_aws_emulator.py against in-process moto (@mock_aws)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from swarm.tools.aws_checks import (  # noqa: E402
    collect_access_key_age,
    collect_iam_users_mfa,
    collect_password_policy,
    collect_s3_public_access,
)

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "seed_aws_emulator.py"
)


def _load_seed_module() -> Any:
    spec = importlib.util.spec_from_file_location("seed_aws_emulator", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed_aws_emulator = _load_seed_module()


@pytest.fixture(autouse=True)
def _fake_aws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)


@mock_aws
def test_seed_misconfigured_profile_and_idempotency() -> None:
    first = seed_aws_emulator.seed_emulator(
        endpoint_url=None, region="us-east-1", profile="misconfigured"
    )
    second = seed_aws_emulator.seed_emulator(
        endpoint_url=None, region="us-east-1", profile="misconfigured"
    )

    assert first["profile"] == "misconfigured"
    assert second["profile"] == "misconfigured"
    assert (
        first["expected_verdicts"]["iam_password_policy"]
        == "FAIL"  # pragma: allowlist secret
    )
    assert first["expected_verdicts"]["iam_users_mfa"] == "FAIL"
    assert first["expected_verdicts"]["s3_public_access"] == "FAIL"

    iam = boto3.client("iam", region_name="us-east-1")
    s3 = boto3.client("s3", region_name="us-east-1")
    s3control = boto3.client("s3control", region_name="us-east-1")
    sts = boto3.client("sts", region_name="us-east-1")

    policy = json.loads(collect_password_policy(iam))
    assert policy["MinimumPasswordLength"] == 6
    assert policy["RequireSymbols"] is False

    mfa = collect_iam_users_mfa(iam)
    assert mfa["Summary"]["users"] == 4
    assert mfa["Summary"]["with_mfa"] == 2
    assert mfa["Summary"]["without_mfa"] == 2

    keys = collect_access_key_age(iam)
    # Idempotency check: re-running must not duplicate access keys.
    assert keys["Summary"]["total_keys"] == 2
    assert keys["Summary"]["active_keys"] == 2

    s3_report = collect_s3_public_access(s3, s3control, sts)
    by_bucket = {b["Bucket"]: b for b in s3_report["Buckets"]}
    assert by_bucket[seed_aws_emulator.PUBLIC_BUCKET_NAME]["Verdict"] == "PUBLIC"
    assert by_bucket[seed_aws_emulator.PRIVATE_BUCKET_NAME]["Verdict"] == "NOT_PUBLIC"
    assert s3_report["AccountBlockPublicAccess"]["status"] == "not configured"


@mock_aws
def test_seed_hardened_profile_and_idempotency() -> None:
    first = seed_aws_emulator.seed_emulator(
        endpoint_url=None, region="us-east-1", profile="hardened"
    )
    second = seed_aws_emulator.seed_emulator(
        endpoint_url=None, region="us-east-1", profile="hardened"
    )

    assert first["profile"] == "hardened"
    assert second["profile"] == "hardened"
    assert (
        first["expected_verdicts"]["iam_password_policy"]
        == "PASS"  # pragma: allowlist secret
    )
    assert first["expected_verdicts"]["iam_users_mfa"] == "PASS"
    assert first["expected_verdicts"]["s3_public_access"] == "PASS"

    iam = boto3.client("iam", region_name="us-east-1")
    s3 = boto3.client("s3", region_name="us-east-1")
    s3control = boto3.client("s3control", region_name="us-east-1")
    sts = boto3.client("sts", region_name="us-east-1")

    policy = json.loads(collect_password_policy(iam))
    assert policy["MinimumPasswordLength"] == 14
    assert policy["RequireSymbols"] is True
    assert policy["RequireNumbers"] is True
    assert policy["RequireUppercaseCharacters"] is True
    assert policy["RequireLowercaseCharacters"] is True

    mfa = collect_iam_users_mfa(iam)
    assert mfa["Summary"]["users"] == 4
    assert mfa["Summary"]["with_mfa"] == 4
    assert mfa["Summary"]["without_mfa"] == 0

    keys = collect_access_key_age(iam)
    assert keys["Summary"]["total_keys"] == 1

    s3_report = collect_s3_public_access(s3, s3control, sts)
    assert s3_report["AccountBlockPublicAccess"]["status"] == "configured"
    assert s3_report["AccountBlockPublicAccess"]["BlockPublicAcls"] is True
    assert s3_report["AccountBlockPublicAccess"]["RestrictPublicBuckets"] is True
    assert s3_report["Summary"]["public"] == 0
    assert s3_report["Summary"]["not_public"] >= 2


def test_seed_emulator_rejects_unknown_profile() -> None:
    with pytest.raises(ValueError, match="Unsupported profile"):
        seed_aws_emulator.seed_emulator(profile="nonexistent")

#!/usr/bin/env python3
"""Seed a local AWS emulator (moto_server or in-process @mock_aws) with
deterministic IAM, S3, CloudTrail, KMS, and GuardDuty resources.

Supports two profiles:
  - ``misconfigured`` (default): plants weak password policy, IAM users without
    MFA, active access keys, a publicly readable S3 bucket, a non-compliant
    CloudTrail trail, a KMS CMK without rotation, and disabled GuardDuty.
  - ``hardened``: plants a strong password policy, MFA on all IAM users, private
    S3 buckets with account + bucket Block Public Access, a multi-region
    validated CloudTrail trail, a KMS CMK with rotation enabled, and an enabled
    GuardDuty detector.

Safe to re-run (idempotent) against an already-seeded emulator.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

DEFAULT_ENDPOINT_URL = "http://127.0.0.1:5000"
DEFAULT_REGION = "us-east-1"
VALID_PROFILES = ("misconfigured", "hardened")

_ALL_BPA_ON = {
    "BlockPublicAcls": True,
    "IgnorePublicAcls": True,
    "BlockPublicPolicy": True,
    "RestrictPublicBuckets": True,
}

_WEAK_ACCOUNT_POLICY: dict[str, Any] = {
    "MinimumPasswordLength": 6,
    "RequireSymbols": False,
    "RequireNumbers": False,
    "RequireUppercaseCharacters": False,
    "RequireLowercaseCharacters": False,
    "AllowUsersToChangePassword": True,
    "MaxPasswordAge": 365,
    "PasswordReusePrevention": 1,
    "HardExpiry": False,
}

_STRONG_ACCOUNT_POLICY: dict[str, Any] = {
    "MinimumPasswordLength": 14,
    "RequireSymbols": True,
    "RequireNumbers": True,
    "RequireUppercaseCharacters": True,
    "RequireLowercaseCharacters": True,
    "AllowUsersToChangePassword": True,
    "MaxPasswordAge": 90,
    "PasswordReusePrevention": 24,
    "HardExpiry": False,
}

_USERS_SPEC: dict[str, list[tuple[str, bool, bool]]] = {
    # (username, want_mfa, want_access_key)
    "misconfigured": [
        ("alice-admin", True, True),
        ("bob-developer", False, True),
        ("carol-ops", False, False),
        ("dave-auditor", True, False),
    ],
    "hardened": [
        ("alice-admin", True, True),
        ("bob-developer", True, False),
        ("carol-ops", True, False),
        ("dave-auditor", True, False),
    ],
}

PUBLIC_BUCKET_NAME = "acme-audit-public-assets"
PRIVATE_BUCKET_NAME = "acme-audit-private-records"
APP_DATA_BUCKET_NAME = "acme-audit-app-data"
CLOUDTRAIL_BUCKET_NAME = "acme-audit-cloudtrail-logs"
CLOUDTRAIL_NAME = "acme-audit-trail"
KMS_ALIAS_NAME = "alias/acme-audit-cmk"


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


def _ensure_dummy_credentials() -> None:
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault(
        "AWS_SECRET_ACCESS_KEY",
        "testing",  # pragma: allowlist secret (moto placeholder)
    )


def _client(service: str, *, endpoint_url: str | None, region: str) -> Any:
    kwargs: dict[str, Any] = {"region_name": region}
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    return boto3.client(service, **kwargs)


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


def _ensure_bucket(s3: Any, bucket: str, region: str) -> None:
    kwargs: dict[str, Any] = {"Bucket": bucket}
    if region != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    try:
        s3.create_bucket(**kwargs)
    except ClientError as exc:
        if _error_code(exc) not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            raise


def _delete_bucket_bpa_if_present(s3: Any, bucket: str) -> None:
    try:
        s3.delete_public_access_block(Bucket=bucket)
    except ClientError as exc:
        if _error_code(exc) not in (
            "NoSuchPublicAccessBlockConfiguration",
            "NoSuchBucket",
        ):
            raise


def _delete_bucket_policy_if_present(s3: Any, bucket: str) -> None:
    try:
        s3.delete_bucket_policy(Bucket=bucket)
    except ClientError as exc:
        if _error_code(exc) not in ("NoSuchBucketPolicy", "NoSuchBucket"):
            raise


def _ensure_user_mfa(iam: Any, username: str, want_mfa: bool) -> bool:
    existing = iam.list_mfa_devices(UserName=username).get("MFADevices", [])
    if want_mfa:
        if existing:
            return True
        try:
            serial = iam.create_virtual_mfa_device(VirtualMFADeviceName=username)[
                "VirtualMFADevice"
            ]["SerialNumber"]
        except ClientError as exc:
            if _error_code(exc) != "EntityAlreadyExists":
                raise
            serial = None
            for dev in iam.list_virtual_mfa_devices().get("VirtualMFADevices", []):
                if str(dev.get("SerialNumber", "")).endswith(f":mfa/{username}"):
                    serial = dev["SerialNumber"]
                    break
            if serial is None:
                raise
        iam.enable_mfa_device(
            UserName=username,
            SerialNumber=serial,
            AuthenticationCode1="123456",
            AuthenticationCode2="654321",
        )
        return True

    for dev in existing:
        iam.deactivate_mfa_device(
            UserName=username,
            SerialNumber=dev["SerialNumber"],
        )
    return False


def _ensure_access_key(iam: Any, username: str) -> str:
    keys = iam.list_access_keys(UserName=username).get("AccessKeyMetadata", [])
    if keys:
        return str(keys[0]["AccessKeyId"])
    created = iam.create_access_key(UserName=username)["AccessKey"]
    return str(created["AccessKeyId"])


def _seed_iam(iam: Any, profile: str) -> dict[str, Any]:
    policy = (
        _WEAK_ACCOUNT_POLICY if profile == "misconfigured" else _STRONG_ACCOUNT_POLICY
    )
    iam.update_account_password_policy(**policy)

    users_summary: list[dict[str, Any]] = []
    for username, want_mfa, want_key in _USERS_SPEC[profile]:
        try:
            iam.create_user(UserName=username)
        except ClientError as exc:
            if _error_code(exc) != "EntityAlreadyExists":
                raise
        mfa_enabled = _ensure_user_mfa(iam, username, want_mfa)
        if want_key:
            _ensure_access_key(iam, username)
        users_summary.append(
            {
                "username": username,
                "mfa_enabled": mfa_enabled,
                "has_access_key": want_key,
            }
        )

    min_length = 6 if profile == "misconfigured" else 14
    return {
        "account_policy_min_length": min_length,
        "users": users_summary,
    }


def _seed_s3(
    s3: Any,
    s3control: Any,
    account_id: str,
    region: str,
    profile: str,
) -> dict[str, Any]:
    buckets_summary: list[dict[str, Any]] = []

    if profile == "misconfigured":
        try:
            s3control.delete_public_access_block(AccountId=account_id)
        except ClientError as exc:
            if _error_code(exc) != "NoSuchPublicAccessBlockConfiguration":
                raise

        _ensure_bucket(s3, PUBLIC_BUCKET_NAME, region)
        _delete_bucket_bpa_if_present(s3, PUBLIC_BUCKET_NAME)
        s3.put_bucket_acl(Bucket=PUBLIC_BUCKET_NAME, ACL="public-read")
        s3.put_bucket_policy(
            Bucket=PUBLIC_BUCKET_NAME,
            Policy=_public_read_policy(PUBLIC_BUCKET_NAME),
        )
        buckets_summary.append(
            {
                "name": PUBLIC_BUCKET_NAME,
                "public": True,
                "acl": "public-read",
                "policy": "public_read",
                "bucket_bpa": False,
            }
        )

        _ensure_bucket(s3, PRIVATE_BUCKET_NAME, region)
        s3.put_bucket_acl(Bucket=PRIVATE_BUCKET_NAME, ACL="private")
        _delete_bucket_policy_if_present(s3, PRIVATE_BUCKET_NAME)
        s3.put_public_access_block(
            Bucket=PRIVATE_BUCKET_NAME,
            PublicAccessBlockConfiguration=dict(_ALL_BPA_ON),
        )
        buckets_summary.append(
            {
                "name": PRIVATE_BUCKET_NAME,
                "public": False,
                "acl": "private",
                "policy": None,
                "bucket_bpa": True,
            }
        )
        account_bpa = False
    else:
        for name in (PRIVATE_BUCKET_NAME, APP_DATA_BUCKET_NAME):
            _ensure_bucket(s3, name, region)
            s3.put_bucket_acl(Bucket=name, ACL="private")
            _delete_bucket_policy_if_present(s3, name)
            s3.put_public_access_block(
                Bucket=name,
                PublicAccessBlockConfiguration=dict(_ALL_BPA_ON),
            )
            buckets_summary.append(
                {
                    "name": name,
                    "public": False,
                    "acl": "private",
                    "policy": None,
                    "bucket_bpa": True,
                }
            )
        s3control.put_public_access_block(
            AccountId=account_id,
            PublicAccessBlockConfiguration=dict(_ALL_BPA_ON),
        )
        account_bpa = True

    return {
        "account_bpa_enabled": account_bpa,
        "buckets": buckets_summary,
    }


def _seed_cloudtrail(
    s3: Any,
    *,
    endpoint_url: str | None,
    region: str,
    profile: str,
) -> dict[str, Any]:
    try:
        ct = _client("cloudtrail", endpoint_url=endpoint_url, region=region)
        _ensure_bucket(s3, CLOUDTRAIL_BUCKET_NAME, region)
        s3.put_bucket_acl(Bucket=CLOUDTRAIL_BUCKET_NAME, ACL="private")
        s3.put_public_access_block(
            Bucket=CLOUDTRAIL_BUCKET_NAME,
            PublicAccessBlockConfiguration=dict(_ALL_BPA_ON),
        )

        is_multi_region = profile == "hardened"
        log_validation = profile == "hardened"

        try:
            ct.create_trail(
                Name=CLOUDTRAIL_NAME,
                S3BucketName=CLOUDTRAIL_BUCKET_NAME,
                IsMultiRegionTrail=is_multi_region,
                EnableLogFileValidation=log_validation,
            )
        except ClientError as exc:
            if _error_code(exc) != "TrailAlreadyExistsException":
                raise
            ct.update_trail(
                Name=CLOUDTRAIL_NAME,
                S3BucketName=CLOUDTRAIL_BUCKET_NAME,
                IsMultiRegionTrail=is_multi_region,
                EnableLogFileValidation=log_validation,
            )

        if profile == "hardened":
            ct.start_logging(Name=CLOUDTRAIL_NAME)
        else:
            ct.stop_logging(Name=CLOUDTRAIL_NAME)

        return {
            "status": "seeded",
            "trail_name": CLOUDTRAIL_NAME,
            "s3_bucket": CLOUDTRAIL_BUCKET_NAME,
            "multi_region": is_multi_region,
            "log_file_validation": log_validation,
            "logging": profile == "hardened",
        }
    except (ClientError, BotoCoreError, NotImplementedError, ImportError) as exc:
        return {"status": "skipped", "reason": f"{type(exc).__name__}: {exc}"}


def _seed_kms(
    *,
    endpoint_url: str | None,
    region: str,
    profile: str,
) -> dict[str, Any]:
    try:
        kms = _client("kms", endpoint_url=endpoint_url, region=region)
        key_id: str | None = None
        for alias in kms.list_aliases().get("Aliases", []):
            if alias.get("AliasName") == KMS_ALIAS_NAME and alias.get("TargetKeyId"):
                key_id = str(alias["TargetKeyId"])
                break

        if key_id is None:
            key_id = str(
                kms.create_key(
                    Description="ACME GRC audit emulator CMK",
                    KeyUsage="ENCRYPT_DECRYPT",
                    KeySpec="SYMMETRIC_DEFAULT",
                )["KeyMetadata"]["KeyId"]
            )
            kms.create_alias(AliasName=KMS_ALIAS_NAME, TargetKeyId=key_id)

        if profile == "hardened":
            kms.enable_key_rotation(KeyId=key_id)
        else:
            kms.disable_key_rotation(KeyId=key_id)

        return {
            "status": "seeded",
            "alias": KMS_ALIAS_NAME,
            "key_id": key_id,
            "rotation_enabled": profile == "hardened",
        }
    except (ClientError, BotoCoreError, NotImplementedError, ImportError) as exc:
        return {"status": "skipped", "reason": f"{type(exc).__name__}: {exc}"}


def _seed_guardduty(
    *,
    endpoint_url: str | None,
    region: str,
    profile: str,
) -> dict[str, Any]:
    try:
        gd = _client("guardduty", endpoint_url=endpoint_url, region=region)
        detector_ids = list(gd.list_detectors().get("DetectorIds", []))

        if profile == "hardened":
            if detector_ids:
                detector_id = str(detector_ids[0])
                gd.update_detector(DetectorId=detector_id, Enable=True)
            else:
                detector_id = str(
                    gd.create_detector(
                        Enable=True,
                        FindingPublishingFrequency="FIFTEEN_MINUTES",
                    )["DetectorId"]
                )
            return {
                "status": "seeded",
                "detector_id": detector_id,
                "enabled": True,
            }

        for det_id in detector_ids:
            gd.update_detector(DetectorId=det_id, Enable=False)
        return {
            "status": "seeded",
            "detector_id": str(detector_ids[0]) if detector_ids else None,
            "enabled": False,
        }
    except (ClientError, BotoCoreError, NotImplementedError, ImportError) as exc:
        return {"status": "skipped", "reason": f"{type(exc).__name__}: {exc}"}


def seed_emulator(
    *,
    endpoint_url: str | None = None,
    region: str = DEFAULT_REGION,
    profile: str = "misconfigured",
) -> dict[str, Any]:
    """Seed an AWS emulator endpoint (or active in-process ``@mock_aws``) and
    return a structured summary of planted resources and expected verdicts.
    """
    if profile not in VALID_PROFILES:
        raise ValueError(
            f"Unsupported profile {profile!r}; expected one of {VALID_PROFILES}"
        )

    _ensure_dummy_credentials()

    sts = _client("sts", endpoint_url=endpoint_url, region=region)
    iam = _client("iam", endpoint_url=endpoint_url, region=region)
    s3 = _client("s3", endpoint_url=endpoint_url, region=region)
    s3control = _client("s3control", endpoint_url=endpoint_url, region=region)

    account_id = str(sts.get_caller_identity()["Account"])

    iam_summary = _seed_iam(iam, profile)
    s3_summary = _seed_s3(s3, s3control, account_id, region, profile)
    cloudtrail_summary = _seed_cloudtrail(
        s3, endpoint_url=endpoint_url, region=region, profile=profile
    )
    kms_summary = _seed_kms(endpoint_url=endpoint_url, region=region, profile=profile)
    guardduty_summary = _seed_guardduty(
        endpoint_url=endpoint_url, region=region, profile=profile
    )

    is_hardened = profile == "hardened"
    base_verdict = "PASS" if is_hardened else "FAIL"
    expected_verdicts = {
        "iam_password_policy": base_verdict,
        "iam_users_mfa": base_verdict,
        "s3_public_access": base_verdict,
        "cloudtrail": (
            "SKIPPED"
            if cloudtrail_summary["status"] != "seeded"
            else ("PASS" if is_hardened else "FAIL")
        ),
        "kms_key_rotation": (
            "SKIPPED"
            if kms_summary["status"] != "seeded"
            else ("PASS" if is_hardened else "FAIL")
        ),
        "guardduty": (
            "SKIPPED"
            if guardduty_summary["status"] != "seeded"
            else ("PASS" if is_hardened else "FAIL")
        ),
    }

    return {
        "endpoint_url": endpoint_url or "in-process-mock",
        "region": region,
        "profile": profile,
        "account_id": account_id,
        "resources": {
            "iam": iam_summary,
            "s3": s3_summary,
            "cloudtrail": cloudtrail_summary,
            "kms": kms_summary,
            "guardduty": guardduty_summary,
        },
        "expected_verdicts": expected_verdicts,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed a local AWS emulator (moto_server) with GRC audit resources."
    )
    parser.add_argument(
        "--endpoint-url",
        default=os.environ.get("AWS_ENDPOINT_URL") or DEFAULT_ENDPOINT_URL,
        help=f"AWS emulator endpoint URL (default: $AWS_ENDPOINT_URL or {DEFAULT_ENDPOINT_URL})",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_DEFAULT_REGION") or DEFAULT_REGION,
        help=f"AWS region (default: $AWS_DEFAULT_REGION or {DEFAULT_REGION})",
    )
    parser.add_argument(
        "--profile",
        choices=VALID_PROFILES,
        default="misconfigured",
        help="Resource posture to plant (default: misconfigured)",
    )
    return parser.parse_args(argv)


def _print_report(summary: dict[str, Any]) -> None:
    """Print a fixed-label seed report without echoing raw resource data."""
    print(f"Seeded profile: {summary['profile']}")
    print(f"Endpoint: {summary['endpoint_url']}")
    print(f"Region: {summary['region']}")
    print("Expected verdicts:")
    for check, verdict in sorted(summary["expected_verdicts"].items()):
        print(f"  {check.replace('_', ' ')}: {verdict}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    summary = seed_emulator(
        endpoint_url=args.endpoint_url,
        region=args.region,
        profile=args.profile,
    )
    _print_report(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

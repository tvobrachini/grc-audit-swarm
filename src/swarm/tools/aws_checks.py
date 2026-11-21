"""Read-only AWS evidence collection, shared by the agent tools and the MCP server.

Every function here takes already-built boto3 clients and returns plain Python
data. Nothing in this module writes to AWS, touches the evidence vault, or
imports an agent framework, so the same logic backs ``swarm.tools.aws_tools`` (which adds
redaction and vault registration) and ``swarm.mcp_server``.

S3 public-access semantics (see DECISIONS.md, ADR-003):

- Block Public Access (BPA) exists at the account level (S3 Control
  ``GetPublicAccessBlock``) and the bucket level (S3 ``GetPublicAccessBlock``).
  The effective value of each of the four flags is account OR bucket.
- ``BlockPublicAcls`` and ``BlockPublicPolicy`` only reject *new* public ACLs
  or policies. They do not change access granted by an ACL or policy that is
  already in place, so they do not enter the verdict.
- ``IgnorePublicAcls`` makes S3 ignore public ACL grants: an ACL-public bucket
  is effectively public only if IgnorePublicAcls is off at both levels.
- ``RestrictPublicBuckets`` restricts a bucket with a public policy to AWS
  service principals and the bucket owner's account: a policy-public bucket is
  effectively public only if RestrictPublicBuckets is off at both levels.
- Whether a bucket policy is public is AWS's own evaluation, read from
  ``GetBucketPolicyStatus`` (``PolicyStatus.IsPublic``); this module does not
  parse policies itself.
- An ACL grant to the ``AllUsers`` (anyone) or ``AuthenticatedUsers`` (any AWS
  account) group is a public grant.
- A call that fails (for example AccessDenied) makes that input "unknown". The
  verdict is then UNKNOWN unless the known inputs already decide it; the tool
  never guesses public or not public.
"""

from __future__ import annotations

import concurrent.futures
import datetime
import json
import logging
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError, OperationNotPageableError

from swarm.evidence import _redact_account_ids

logger = logging.getLogger(__name__)

BPA_FLAGS = (
    "BlockPublicAcls",
    "IgnorePublicAcls",
    "BlockPublicPolicy",
    "RestrictPublicBuckets",
)

# ACL grantee groups that make a grant public. AllUsers is anyone on the
# internet; AuthenticatedUsers is any AWS account (not just this one).
_PUBLIC_ACL_GROUPS = {
    "/groups/global/AllUsers": "AllUsers",
    "/groups/global/AuthenticatedUsers": "AuthenticatedUsers",
}

_NO_BPA = "NoSuchPublicAccessBlockConfiguration"

ROOT_USER_NOTE = (
    "The AWS account root user is not returned by iam:ListUsers, so root user "
    "MFA is not covered by this evidence."
)
CONSOLE_ACCESS_NOTE = (
    "MFA status is listed for every IAM user, including users with no console "
    "password (for example access-key-only service users); console access is "
    "not checked."
)
S3_SCOPE_NOTE = (
    "Covers bucket policies (as evaluated by GetBucketPolicyStatus), bucket "
    "ACLs and account- and bucket-level Block Public Access. Not covered: "
    "access point policies, object-level ACLs, and cross-account access "
    "granted by a policy that AWS does not classify as public."
)


def describe_error(exc: BaseException) -> str:
    """Short, account-ID-redacted description of an AWS client error.

    AWS error messages often embed ARNs (and therefore account IDs), so the
    message is redacted before it reaches output or logs.
    """
    if isinstance(exc, ClientError):
        err = exc.response.get("Error", {})
        code = err.get("Code", "Unknown")
        message = err.get("Message", "")
        text = f"{code}: {message}" if message else code
    else:
        text = f"{type(exc).__name__}: {exc}"
    return _redact_account_ids(text)


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


# ─── IAM ──────────────────────────────────────────────────────────────────────


NO_PASSWORD_POLICY_FINDING = (
    "Finding: No IAM account password policy is set for this account "
    "(GetAccountPasswordPolicy returned NoSuchEntity). IAM user passwords "
    "fall back to the AWS default password requirements."
)


def collect_password_policy(iam) -> str:
    """Return the account password policy as JSON text, or a finding if none is set.

    "No policy set" is a finding about the account, not a collection error.
    Any other failure is returned as an error string.
    """
    try:
        response = iam.get_account_password_policy()
    except ClientError as e:
        if _error_code(e) == "NoSuchEntity":
            return NO_PASSWORD_POLICY_FINDING
        logger.warning("GetAccountPasswordPolicy failed: %s", describe_error(e))
        return f"Error fetching password policy: {describe_error(e)}"
    except BotoCoreError as e:
        return f"Error fetching password policy: {describe_error(e)}"
    return json.dumps(response.get("PasswordPolicy", {}), indent=2, default=str)


def _user_mfa_status(iam, name: str) -> dict:
    try:
        devices = []
        for page in iam.get_paginator("list_mfa_devices").paginate(UserName=name):
            devices.extend(page.get("MFADevices", []))
    except (ClientError, BotoCoreError) as e:
        logger.warning("ListMFADevices failed for an IAM user: %s", describe_error(e))
        return {"UserName": name, "MFA_Enabled": "Unknown", "Error": describe_error(e)}
    return {
        "UserName": name,
        "MFA_Enabled": "Yes" if devices else "No",
        "MFADeviceCount": len(devices),
    }


def collect_iam_users_mfa(iam) -> dict:
    """List every IAM user (paginated) with whether any MFA device is assigned.

    Raises ClientError/BotoCoreError if the user list itself cannot be read;
    a per-user MFA failure is reported as "Unknown" for that user.
    """
    users: list[str] = []
    for page in iam.get_paginator("list_users").paginate():
        users.extend(u["UserName"] for u in page.get("Users", []))

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        report = list(executor.map(lambda n: _user_mfa_status(iam, n), users))

    summary = {
        "users": len(report),
        "with_mfa": sum(1 for r in report if r["MFA_Enabled"] == "Yes"),
        "without_mfa": sum(1 for r in report if r["MFA_Enabled"] == "No"),
        "unknown": sum(1 for r in report if r["MFA_Enabled"] == "Unknown"),
    }
    return {
        "Users": report,
        "Summary": summary,
        "Notes": [ROOT_USER_NOTE, CONSOLE_ACCESS_NOTE],
    }


def collect_root_account_summary(iam) -> dict:
    """Inspect AWS root account MFA and root access key status via ``iam:GetAccountSummary``.

    Addresses CIS AWS Foundations Benchmark v3.0/v4.0 controls 1.4 (no root access
    keys) and 1.5 (MFA enabled for root account) and NIST SP 800-53 IA-2(1).
    """
    summary_map = iam.get_account_summary().get("SummaryMap", {})
    mfa_enabled = int(summary_map.get("AccountMFAEnabled", 0))
    keys_present = int(summary_map.get("AccountAccessKeysPresent", 0))
    reasons: list[str] = []
    if mfa_enabled != 1:
        reasons.append("Root account MFA is not enabled (AccountMFAEnabled=0)")
    if keys_present > 0:
        reasons.append(
            f"Root account has active access keys (AccountAccessKeysPresent={keys_present})"
        )
    verdict = "PASS" if (mfa_enabled == 1 and keys_present == 0) else "FAIL"
    return {
        "AccountMFAEnabled": mfa_enabled,
        "AccountAccessKeysPresent": keys_present,
        "RootMFAStatus": "Enabled" if mfa_enabled == 1 else "Disabled",
        "Verdict": verdict,
        "Reasons": reasons,
    }


def collect_access_key_age(
    iam,
    *,
    max_age_days: int = 90,
    reference_time: datetime.datetime | None = None,
) -> dict:
    """List IAM user access keys and evaluate key rotation age against ``max_age_days``.

    Addresses CIS AWS Foundations Benchmark v3.0/v4.0 control 1.14 and NIST SP 800-53
    IA-5(1) / AC-2.
    """
    now = reference_time or datetime.datetime.now(datetime.UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.UTC)

    users: list[str] = []
    for page in iam.get_paginator("list_users").paginate():
        users.extend(u["UserName"] for u in page.get("Users", []))

    keys_report: list[dict[str, Any]] = []
    for user in sorted(users):
        try:
            resp = iam.list_access_keys(UserName=user)
            metadata_list = resp.get("AccessKeyMetadata", [])
        except (ClientError, BotoCoreError) as exc:
            keys_report.append(
                {
                    "UserName": user,
                    "Status": "Unknown",
                    "Error": describe_error(exc),
                }
            )
            continue

        for meta in metadata_list:
            key_id = str(meta.get("AccessKeyId", ""))
            status = str(meta.get("Status", "Unknown"))
            created = meta.get("CreateDate")
            age_days: int | None = None
            if isinstance(created, datetime.datetime):
                created_utc = (
                    created
                    if created.tzinfo is not None
                    else created.replace(tzinfo=datetime.UTC)
                )
                age_days = max(0, (now - created_utc).days)
            elif isinstance(created, str):
                try:
                    parsed_dt = datetime.datetime.fromisoformat(
                        created.replace("Z", "+00:00")
                    )
                    if parsed_dt.tzinfo is None:
                        parsed_dt = parsed_dt.replace(tzinfo=datetime.UTC)
                    age_days = max(0, (now - parsed_dt).days)
                except ValueError:
                    age_days = None

            exceeds_max_age = (
                status == "Active" and age_days is not None and age_days > max_age_days
            )
            keys_report.append(
                {
                    "UserName": user,
                    "AccessKeyIdSuffix": key_id[-4:] if len(key_id) >= 4 else key_id,
                    "Status": status,
                    "AgeDays": age_days,
                    "ExceedsMaxAge": exceeds_max_age,
                }
            )

    active_keys = [k for k in keys_report if k.get("Status") == "Active"]
    expired_keys = [k for k in active_keys if k.get("ExceedsMaxAge") is True]
    unknown_keys = [
        k
        for k in keys_report
        if k.get("Status") == "Unknown" or k.get("AgeDays") is None
    ]
    verdict = "FAIL" if expired_keys else ("UNKNOWN" if unknown_keys else "PASS")
    return {
        "MaxAgeThresholdDays": max_age_days,
        "AccessKeys": keys_report,
        "Summary": {
            "total_keys": len(keys_report),
            "active_keys": len(active_keys),
            "exceeding_max_age": len(expired_keys),
            "unknown_keys": len(unknown_keys),
        },
        "Verdict": verdict,
    }


# ─── S3 ───────────────────────────────────────────────────────────────────────


def _bpa_layer(settings: dict | None = None, *, error: str | None = None) -> dict:
    """A Block Public Access layer: configured, not configured, or unknown."""
    if error is not None:
        return {"status": "unknown", "error": error}
    if settings is None:
        return {"status": "not configured"}
    return {
        "status": "configured",
        **{flag: bool(settings.get(flag, False)) for flag in BPA_FLAGS},
    }


def get_caller_identity(sts) -> tuple[str | None, str | None]:
    """(account_id, arn) from sts:GetCallerIdentity (needs no IAM permission).

    ``(None, None)`` if the call fails (logged, redacted). The arn has its
    account id redacted before it is returned, so it can be recorded as
    evidence-collection provenance (see ``swarm.evidence.register_evidence``)
    without that id ever leaving this function; the raw ``account_id`` is
    used only for the S3 Control BPA call in :func:`get_account_bpa` and is
    never itself returned to a caller outside this module's collection path.
    """
    try:
        identity = sts.get_caller_identity()
    except (ClientError, BotoCoreError) as e:
        logger.warning("GetCallerIdentity failed: %s", describe_error(e))
        return None, None
    account_id = identity.get("Account")
    arn = identity.get("Arn")
    return account_id, (_redact_account_ids(arn) if isinstance(arn, str) else None)


def get_account_bpa(account_id: str | None, s3control) -> dict:
    """Read account-level Block Public Access for ``account_id``.

    ``account_id`` comes from :func:`get_caller_identity`; pass ``None`` when
    that call failed (reported as an unknown layer here, not raised).
    """
    if account_id is None:
        return _bpa_layer(
            error="GetCallerIdentity failed (no account id available for the "
            "S3 Control call)"
        )
    try:
        resp = s3control.get_public_access_block(AccountId=account_id)
    except ClientError as e:
        if _error_code(e) == _NO_BPA:
            return _bpa_layer()
        logger.warning("GetAccountPublicAccessBlock failed: %s", describe_error(e))
        return _bpa_layer(error=describe_error(e))
    except BotoCoreError as e:
        return _bpa_layer(error=describe_error(e))
    return _bpa_layer(resp.get("PublicAccessBlockConfiguration", {}))


def _get_bucket_bpa(s3, name: str) -> dict:
    try:
        resp = s3.get_public_access_block(Bucket=name)
    except ClientError as e:
        if _error_code(e) == _NO_BPA:
            return _bpa_layer()
        return _bpa_layer(error=describe_error(e))
    except BotoCoreError as e:
        return _bpa_layer(error=describe_error(e))
    return _bpa_layer(resp.get("PublicAccessBlockConfiguration", {}))


def _layer_flag(layer: dict, flag: str) -> bool | None:
    if layer["status"] == "unknown":
        return None
    if layer["status"] == "not configured":
        return False
    return bool(layer[flag])


def _either(a: bool | None, b: bool | None) -> bool | None:
    """Three-valued OR: True wins, then unknown, then False."""
    if a is True or b is True:
        return True
    if a is None or b is None:
        return None
    return False


def _get_policy_public(s3, name: str) -> tuple[bool | None, str | None]:
    """(is_public, error). No bucket policy means not public by policy."""
    try:
        resp = s3.get_bucket_policy_status(Bucket=name)
    except ClientError as e:
        if _error_code(e) == "NoSuchBucketPolicy":
            return False, None
        return None, describe_error(e)
    except BotoCoreError as e:
        return None, describe_error(e)
    is_public = resp.get("PolicyStatus", {}).get("IsPublic")
    if not isinstance(is_public, bool):
        return None, "GetBucketPolicyStatus returned no IsPublic value"
    return is_public, None


def _get_public_acl_grants(s3, name: str) -> tuple[list[str] | None, str | None]:
    """(public grants like "AllUsers READ", error)."""
    try:
        acl = s3.get_bucket_acl(Bucket=name)
    except (ClientError, BotoCoreError) as e:
        return None, describe_error(e)
    grants = []
    for grant in acl.get("Grants", []):
        uri = grant.get("Grantee", {}).get("URI", "")
        for suffix, group in _PUBLIC_ACL_GROUPS.items():
            if uri.endswith(suffix):
                grants.append(f"{group} {grant.get('Permission', 'UNKNOWN')}")
    return sorted(set(grants)), None


def _layers_with(flag: str, account: dict, bucket: dict) -> str:
    on = [
        name
        for name, layer in (("account", account), ("bucket", bucket))
        if _layer_flag(layer, flag) is True
    ]
    return " and ".join(on) + (" level" if len(on) == 1 else " levels")


def check_bucket_public_access(s3, name: str, account_bpa: dict) -> dict:
    """Evaluate one bucket's effective public access. See module docstring."""
    bucket_bpa = _get_bucket_bpa(s3, name)
    policy_public, policy_error = _get_policy_public(s3, name)
    acl_grants, acl_error = _get_public_acl_grants(s3, name)

    effective = {
        flag: _either(_layer_flag(account_bpa, flag), _layer_flag(bucket_bpa, flag))
        for flag in BPA_FLAGS
    }
    restrict = effective["RestrictPublicBuckets"]
    ignore_acls = effective["IgnorePublicAcls"]

    reasons: list[str] = []
    notes: list[str] = []
    # Every failed read is surfaced, even when the other inputs still decide
    # the verdict.
    unknowns: list[str] = []
    if policy_error is not None:
        unknowns.append(f"bucket policy status unknown ({policy_error})")
    if acl_error is not None:
        unknowns.append(f"bucket ACL unknown ({acl_error})")
    for layer_name, layer in (("account", account_bpa), ("bucket", bucket_bpa)):
        if layer["status"] == "unknown":
            unknowns.append(
                f"{layer_name}-level Block Public Access unknown ({layer['error']})"
            )

    # Policy path.
    if policy_public is False:
        policy_effective: bool | None = False
    elif restrict is True:
        policy_effective = False
        if policy_public is True:
            notes.append(
                "Bucket policy is public, but RestrictPublicBuckets is on at the "
                f"{_layers_with('RestrictPublicBuckets', account_bpa, bucket_bpa)}, "
                "so access is limited to AWS service principals and the bucket "
                "owner's account."
            )
    elif policy_public is True and restrict is False:
        policy_effective = True
        reasons.append("public bucket policy")
    else:
        policy_effective = None
        if policy_public is True:
            unknowns.append(
                "bucket policy is public but RestrictPublicBuckets could not be "
                "determined"
            )

    # ACL path.
    if acl_grants == []:
        acl_effective: bool | None = False
    elif ignore_acls is True:
        acl_effective = False
        if acl_grants:
            notes.append(
                f"ACL has public grants ({', '.join(acl_grants)}), but "
                "IgnorePublicAcls is on at the "
                f"{_layers_with('IgnorePublicAcls', account_bpa, bucket_bpa)}, "
                "so S3 ignores them."
            )
    elif acl_grants and ignore_acls is False:
        acl_effective = True
        reasons.extend(f"ACL grants {g}" for g in acl_grants)
    else:
        acl_effective = None
        if acl_grants:
            unknowns.append(
                f"ACL has public grants ({', '.join(acl_grants)}) but "
                "IgnorePublicAcls could not be determined"
            )

    effective_public = _either(policy_effective, acl_effective)
    verdict = {True: "PUBLIC", False: "NOT_PUBLIC", None: "UNKNOWN"}[effective_public]

    return {
        "Bucket": name,
        "Verdict": verdict,
        "EffectivePublic": effective_public,
        "Reasons": reasons,
        "Unknowns": unknowns,
        "Notes": notes,
        "BlockPublicAccess": {
            "Account": account_bpa,
            "Bucket": bucket_bpa,
            "Effective": effective,
        },
        "PolicyIsPublic": policy_public,
        "PublicAclGrants": acl_grants,
    }


def _list_bucket_names(s3) -> list[str]:
    try:
        buckets: list[dict[str, Any]] = []
        for page in s3.get_paginator("list_buckets").paginate():
            buckets.extend(page.get("Buckets", []))
    except OperationNotPageableError:
        # Older botocore versions don't support ListBuckets pagination.
        buckets = s3.list_buckets().get("Buckets", [])
    return [b["Name"] for b in buckets]


def collect_s3_public_access(s3, s3control, sts) -> dict:
    """Evaluate every bucket in the account. Raises if buckets cannot be listed.

    ``CallerIdentityArn`` (account id already redacted, or ``None`` if
    GetCallerIdentity failed) is included for evidence provenance — see
    :func:`get_caller_identity`.
    """
    account_id, caller_arn = get_caller_identity(sts)
    account_bpa = get_account_bpa(account_id, s3control)
    names = _list_bucket_names(s3)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = list(
            executor.map(
                lambda n: check_bucket_public_access(s3, n, account_bpa), names
            )
        )
    results.sort(key=lambda r: r["Bucket"])

    return {
        "AccountBlockPublicAccess": account_bpa,
        "Buckets": results,
        "Summary": {
            "buckets": len(results),
            "public": sum(1 for r in results if r["Verdict"] == "PUBLIC"),
            "not_public": sum(1 for r in results if r["Verdict"] == "NOT_PUBLIC"),
            "unknown": sum(1 for r in results if r["Verdict"] == "UNKNOWN"),
        },
        "Notes": [S3_SCOPE_NOTE],
        "CallerIdentityArn": caller_arn,
    }


# ─── CloudTrail, KMS, GuardDuty & IAM Access Analyzer ────────────────────────


def collect_cloudtrail_trails(ct_client: Any = None) -> dict[str, Any]:
    """Inspect CloudTrail trails for multi-region logging and log file validation.

    Addresses CIS AWS Foundations Benchmark v3.0/v4.0 controls 3.1 / 3.2 and
    NIST SP 800-53 AU-2 / AU-9 / AU-12. Fails closed with ``Verdict: "UNKNOWN"``
    when the AWS API cannot be read.
    """
    try:
        client = ct_client if ct_client is not None else boto3.client("cloudtrail")
        resp = client.describe_trails(includeShadowTrails=False)
    except (ClientError, BotoCoreError) as exc:
        err = describe_error(exc)
        logger.warning("DescribeTrails failed: %s", err)
        return {
            "Trails": [],
            "Summary": {
                "total_trails": 0,
                "multi_region_trails": 0,
                "logging_trails": 0,
                "validated_trails": 0,
                "compliant_trails": 0,
                "unknown_status_trails": 0,
            },
            "Verdict": "UNKNOWN",
            "Reasons": [f"Unable to describe CloudTrail trails: {err}"],
            "Error": err,
        }

    trails_report: list[dict[str, Any]] = []
    for trail in resp.get("trailList", []):
        name = str(trail.get("Name", ""))
        raw_arn = str(trail.get("TrailARN", ""))
        is_multi_region = bool(trail.get("IsMultiRegionTrail", False))
        log_file_validation = bool(trail.get("LogFileValidationEnabled", False))

        is_logging: bool | None = None
        status_error: str | None = None
        try:
            status_resp = client.get_trail_status(Name=raw_arn or name)
            is_logging = bool(status_resp.get("IsLogging", False))
        except (ClientError, BotoCoreError) as exc:
            status_error = describe_error(exc)
            logger.warning("GetTrailStatus failed for trail %s: %s", name, status_error)

        entry: dict[str, Any] = {
            "Name": name,
            "TrailARN": _redact_account_ids(raw_arn),
            "HomeRegion": trail.get("HomeRegion"),
            "IsMultiRegionTrail": is_multi_region,
            "IsLogging": is_logging,
            "LogFileValidationEnabled": log_file_validation,
        }
        if status_error is not None:
            entry["StatusError"] = status_error
        trails_report.append(entry)

    multi_region = [t for t in trails_report if t["IsMultiRegionTrail"]]
    logging_trails = [t for t in trails_report if t["IsLogging"] is True]
    validated_trails = [t for t in trails_report if t["LogFileValidationEnabled"]]
    unknown_status = [t for t in trails_report if t["IsLogging"] is None]
    compliant = [
        t
        for t in trails_report
        if t["IsMultiRegionTrail"]
        and t["IsLogging"] is True
        and t["LogFileValidationEnabled"]
    ]
    unknown_candidates = [
        t
        for t in trails_report
        if t["IsMultiRegionTrail"]
        and t["LogFileValidationEnabled"]
        and t["IsLogging"] is None
    ]

    reasons: list[str] = []
    if compliant:
        verdict = "PASS"
    elif unknown_candidates:
        verdict = "UNKNOWN"
        reasons.append(
            "Unable to verify IsLogging status for multi-region validated trail(s)."
        )
    else:
        verdict = "FAIL"
        if not trails_report:
            reasons.append("No CloudTrail trails are configured.")
        else:
            reasons.append(
                "No multi-region CloudTrail trail is actively logging with log file validation enabled."
            )

    return {
        "Trails": trails_report,
        "Summary": {
            "total_trails": len(trails_report),
            "multi_region_trails": len(multi_region),
            "logging_trails": len(logging_trails),
            "validated_trails": len(validated_trails),
            "compliant_trails": len(compliant),
            "unknown_status_trails": len(unknown_status),
        },
        "Verdict": verdict,
        "Reasons": reasons,
    }


def collect_kms_key_rotation(kms_client: Any = None) -> dict[str, Any]:
    """Inspect enabled customer-managed symmetric KMS keys for annual rotation.

    Addresses CIS AWS Foundations Benchmark v3.0/v4.0 control 3.6 and
    NIST SP 800-53 SC-12. Fails closed with ``Verdict: "UNKNOWN"`` when key
    listing, description, or rotation status cannot be read.
    """
    try:
        client = kms_client if kms_client is not None else boto3.client("kms")
        key_ids: list[str] = []
        for page in client.get_paginator("list_keys").paginate():
            key_ids.extend(k["KeyId"] for k in page.get("Keys", []) if "KeyId" in k)
    except (ClientError, BotoCoreError) as exc:
        err = describe_error(exc)
        logger.warning("KMS ListKeys failed: %s", err)
        return {
            "Keys": [],
            "Summary": {
                "evaluated_keys": 0,
                "rotation_enabled": 0,
                "rotation_disabled": 0,
                "unknown": 0,
            },
            "Verdict": "UNKNOWN",
            "Reasons": [f"Unable to list KMS keys: {err}"],
            "Error": err,
        }

    keys_report: list[dict[str, Any]] = []
    for key_id in sorted(key_ids):
        try:
            meta = client.describe_key(KeyId=key_id).get("KeyMetadata", {})
        except (ClientError, BotoCoreError) as exc:
            err = describe_error(exc)
            logger.warning("KMS DescribeKey failed for %s: %s", key_id, err)
            keys_report.append(
                {
                    "KeyId": key_id,
                    "KeyRotationEnabled": None,
                    "Error": err,
                }
            )
            continue

        key_manager = meta.get("KeyManager")
        key_state = meta.get("KeyState")
        key_spec = (
            meta.get("KeySpec")
            or meta.get("CustomerMasterKeySpec")
            or "SYMMETRIC_DEFAULT"
        )
        origin = meta.get("Origin") or "AWS_KMS"
        if (
            key_manager != "CUSTOMER"
            or key_state != "Enabled"
            or key_spec != "SYMMETRIC_DEFAULT"
            or origin != "AWS_KMS"
        ):
            continue

        entry: dict[str, Any] = {
            "KeyId": key_id,
            "Arn": _redact_account_ids(str(meta.get("Arn", ""))),
            "KeyState": key_state,
            "KeySpec": key_spec,
        }
        try:
            rot = client.get_key_rotation_status(KeyId=key_id)
            entry["KeyRotationEnabled"] = bool(rot.get("KeyRotationEnabled", False))
        except (ClientError, BotoCoreError) as exc:
            err = describe_error(exc)
            logger.warning("KMS GetKeyRotationStatus failed for %s: %s", key_id, err)
            entry["KeyRotationEnabled"] = None
            entry["Error"] = err
        keys_report.append(entry)

    enabled = [k for k in keys_report if k.get("KeyRotationEnabled") is True]
    disabled = [k for k in keys_report if k.get("KeyRotationEnabled") is False]
    unknown = [k for k in keys_report if k.get("KeyRotationEnabled") is None]

    reasons: list[str] = []
    if disabled:
        verdict = "FAIL"
        reasons.append(
            f"{len(disabled)} enabled customer-managed symmetric KMS key(s) do not have automatic rotation enabled."
        )
    elif unknown:
        verdict = "UNKNOWN"
        reasons.append(
            f"Unable to determine rotation status for {len(unknown)} KMS key(s)."
        )
    else:
        verdict = "PASS"

    return {
        "Keys": keys_report,
        "Summary": {
            "evaluated_keys": len(keys_report),
            "rotation_enabled": len(enabled),
            "rotation_disabled": len(disabled),
            "unknown": len(unknown),
        },
        "Verdict": verdict,
        "Reasons": reasons,
    }


def collect_guardduty_detectors(gd_client: Any = None) -> dict[str, Any]:
    """Inspect GuardDuty detectors and verify at least one detector is ``ENABLED``.

    Addresses NIST SP 800-53 SI-4 (System Monitoring). Fails closed with
    ``Verdict: "UNKNOWN"`` on AWS API errors.
    """
    try:
        client = gd_client if gd_client is not None else boto3.client("guardduty")
        detector_ids: list[str] = []
        for page in client.get_paginator("list_detectors").paginate():
            detector_ids.extend(page.get("DetectorIds", []))
    except (ClientError, BotoCoreError) as exc:
        err = describe_error(exc)
        logger.warning("GuardDuty ListDetectors failed: %s", err)
        return {
            "Detectors": [],
            "Summary": {
                "total_detectors": 0,
                "enabled_detectors": 0,
                "disabled_detectors": 0,
                "unknown_detectors": 0,
            },
            "Verdict": "UNKNOWN",
            "Reasons": [f"Unable to list GuardDuty detectors: {err}"],
            "Error": err,
        }

    detectors_report: list[dict[str, Any]] = []
    for detector_id in sorted(detector_ids):
        try:
            resp = client.get_detector(DetectorId=detector_id)
            detectors_report.append(
                {
                    "DetectorId": detector_id,
                    "Status": str(resp.get("Status", "UNKNOWN")),
                    "FindingPublishingFrequency": resp.get(
                        "FindingPublishingFrequency"
                    ),
                }
            )
        except (ClientError, BotoCoreError) as exc:
            err = describe_error(exc)
            logger.warning("GuardDuty GetDetector failed for %s: %s", detector_id, err)
            detectors_report.append(
                {
                    "DetectorId": detector_id,
                    "Status": "UNKNOWN",
                    "Error": err,
                }
            )

    enabled = [d for d in detectors_report if d["Status"] == "ENABLED"]
    disabled = [d for d in detectors_report if d["Status"] == "DISABLED"]
    unknown = [
        d for d in detectors_report if d["Status"] not in ("ENABLED", "DISABLED")
    ]

    reasons: list[str] = []
    if enabled:
        verdict = "PASS"
    elif unknown:
        verdict = "UNKNOWN"
        reasons.append(
            f"Unable to determine GuardDuty detector status for {len(unknown)} detector(s)."
        )
    else:
        verdict = "FAIL"
        reasons.append("No enabled GuardDuty detector found in this region.")

    return {
        "Detectors": detectors_report,
        "Summary": {
            "total_detectors": len(detectors_report),
            "enabled_detectors": len(enabled),
            "disabled_detectors": len(disabled),
            "unknown_detectors": len(unknown),
        },
        "Verdict": verdict,
        "Reasons": reasons,
    }


def collect_iam_access_analyzer(aa_client: Any = None) -> dict[str, Any]:
    """Inspect IAM Access Analyzers and count active external-access findings.

    Addresses CIS AWS Foundations Benchmark v3.0/v4.0 control 1.20 and
    NIST SP 800-53 AC-2 / AC-6. Fails closed with ``Verdict: "UNKNOWN"`` on
    AWS API errors.
    """
    try:
        client = aa_client if aa_client is not None else boto3.client("accessanalyzer")
        analyzers: list[dict[str, Any]] = []
        for page in client.get_paginator("list_analyzers").paginate():
            analyzers.extend(page.get("analyzers", []))
    except (ClientError, BotoCoreError) as exc:
        err = describe_error(exc)
        logger.warning("AccessAnalyzer ListAnalyzers failed: %s", err)
        return {
            "Analyzers": [],
            "Summary": {
                "total_analyzers": 0,
                "active_analyzers": 0,
                "active_findings": 0,
                "unknown_analyzers": 0,
            },
            "Verdict": "UNKNOWN",
            "Reasons": [f"Unable to list IAM Access Analyzers: {err}"],
            "Error": err,
        }

    analyzers_report: list[dict[str, Any]] = []
    total_active_findings = 0
    unknown_analyzers = 0

    for analyzer in analyzers:
        raw_arn = str(analyzer.get("arn", ""))
        status = str(analyzer.get("status", "UNKNOWN"))
        entry: dict[str, Any] = {
            "Name": analyzer.get("name", ""),
            "Arn": _redact_account_ids(raw_arn),
            "Type": analyzer.get("type", ""),
            "Status": status,
        }
        if status == "ACTIVE":
            try:
                active_count = 0
                for page in client.get_paginator("list_findings").paginate(
                    analyzerArn=raw_arn,
                    filter={"status": {"eq": ["ACTIVE"]}},
                ):
                    active_count += len(page.get("findings", []))
                entry["ActiveFindingsCount"] = active_count
                total_active_findings += active_count
            except (ClientError, BotoCoreError) as exc:
                err = describe_error(exc)
                logger.warning(
                    "AccessAnalyzer ListFindings failed for %s: %s",
                    entry["Arn"],
                    err,
                )
                entry["ActiveFindingsCount"] = None
                entry["FindingsError"] = err
                unknown_analyzers += 1
        analyzers_report.append(entry)

    active_analyzers = [a for a in analyzers_report if a["Status"] == "ACTIVE"]

    reasons: list[str] = []
    if not active_analyzers:
        verdict = "FAIL"
        reasons.append("No active IAM Access Analyzer is configured in this region.")
    elif total_active_findings > 0:
        verdict = "FAIL"
        reasons.append(
            f"IAM Access Analyzer reports {total_active_findings} active external-access finding(s)."
        )
    elif unknown_analyzers > 0:
        verdict = "UNKNOWN"
        reasons.append(
            f"Unable to read findings for {unknown_analyzers} active IAM Access Analyzer(s)."
        )
    else:
        verdict = "PASS"

    return {
        "Analyzers": analyzers_report,
        "Summary": {
            "total_analyzers": len(analyzers_report),
            "active_analyzers": len(active_analyzers),
            "active_findings": total_active_findings,
            "unknown_analyzers": unknown_analyzers,
        },
        "Verdict": verdict,
        "Reasons": reasons,
    }

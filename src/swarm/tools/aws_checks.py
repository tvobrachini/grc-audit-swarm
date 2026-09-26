"""Read-only AWS evidence collection, shared by the CrewAI tools and the MCP server.

Every function here takes already-built boto3 clients and returns plain Python
data. Nothing in this module writes to AWS, touches the evidence vault, or
imports CrewAI, so the same logic backs ``swarm.tools.aws_tools`` (which adds
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
import json
import logging
from typing import Any

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


def get_account_bpa(sts, s3control) -> dict:
    """Read account-level Block Public Access.

    The account ID comes from sts:GetCallerIdentity (which needs no IAM
    permission). It is used only for the S3 Control call and is never
    returned or logged.
    """
    try:
        account_id = sts.get_caller_identity()["Account"]
    except (ClientError, BotoCoreError, KeyError) as e:
        text = describe_error(e) if not isinstance(e, KeyError) else "no Account"
        logger.warning("GetCallerIdentity failed: %s", text)
        return _bpa_layer(error=f"GetCallerIdentity failed ({text})")
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
    unknowns: list[str] = []
    notes: list[str] = []

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
        if policy_public is None:
            unknowns.append(f"bucket policy status unknown ({policy_error})")
        else:
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
        if acl_grants is None:
            unknowns.append(f"bucket ACL unknown ({acl_error})")
        else:
            unknowns.append(
                f"ACL has public grants ({', '.join(acl_grants)}) but "
                "IgnorePublicAcls could not be determined"
            )

    for layer_name, layer in (("account", account_bpa), ("bucket", bucket_bpa)):
        if layer["status"] == "unknown":
            unknowns.append(
                f"{layer_name}-level Block Public Access unknown ({layer['error']})"
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
    """Evaluate every bucket in the account. Raises if buckets cannot be listed."""
    account_bpa = get_account_bpa(sts, s3control)
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
    }

"""Agent tools for read-only AWS evidence collection.

The collection logic lives in ``swarm.tools.aws_checks`` (shared with the MCP
server). These wrappers add what the audit path needs: every raw result is
registered in the evidence vault (which redacts account IDs before storing)
and the text returned to the agent is redacted the same way. Each
registration also carries non-sensitive collection metadata — AWS region,
the API operation(s) called, the tool name and app version, and (where
already available at no extra API call cost) the caller's identity as an ARN
with the account id redacted — see ADR-010 and ``swarm.evidence``.
"""

import json

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from swarm.crews._compat import tool
from swarm.evidence import EvidenceAssuranceProtocol, _redact_account_ids
from swarm.evidence import app_version as _app_version
from swarm.tools.aws_checks import (
    collect_access_key_age,
    collect_cloudtrail_trails,
    collect_guardduty_detectors,
    collect_iam_access_analyzer,
    collect_iam_users_mfa,
    collect_kms_key_rotation,
    collect_password_policy,
    collect_root_account_summary,
    collect_s3_public_access,
    describe_error,
)


def _boto_client(service: str):
    return boto3.client(service)


_APP_VERSION = _app_version()


def _region_of(client) -> str | None:
    try:
        region = client.meta.region_name
    except AttributeError:
        return None
    return region if isinstance(region, str) else None


def _collection_metadata(
    tool_name: str,
    operation: str,
    *,
    region: str | None = None,
    caller_identity: str | None = None,
    parameters: dict | None = None,
) -> dict:
    """Non-sensitive evidence-collection metadata for ``register_evidence``.

    ``caller_identity`` must already be an ARN with its account id redacted
    (see ``swarm.tools.aws_checks.get_caller_identity``) — never a key,
    token, or credential. It is ``None`` when capturing it would need an
    extra AWS call this tool does not otherwise make (IAM tools below); the
    S3 tool gets it for free from a call it already makes.
    """
    metadata: dict = {
        "tool": tool_name,
        "operation": operation,
        "app_version": _APP_VERSION,
        "region": region,
        "caller_identity": caller_identity,
    }
    if parameters:
        metadata["parameters"] = parameters
    return metadata


def _register_and_format(
    raw_output: str, source: str, *, metadata: dict, session_id: str | None = None
) -> str:
    vault_record = EvidenceAssuranceProtocol.register_evidence(
        raw_output, source, metadata=metadata, session_id=session_id
    )
    return f"Vault ID: {vault_record['vault_id']}\nRaw Output: {_redact_account_ids(raw_output)}"


def make_aws_tools(session_id: str | None):
    @tool("Get IAM Password Policy")
    def get_iam_password_policy(context: str = "") -> str:
        """Fetches the AWS IAM account password policy. Essential for NIST SP 800-53 IA-5(1) password authenticator compliance. If no policy is set, returns a finding saying so."""
        region = None
        try:
            client = _boto_client("iam")
            region = _region_of(client)
        except (ClientError, BotoCoreError) as e:
            return f"Error fetching password policy: {describe_error(e)}"

        raw_output = collect_password_policy(client)
        source = "aws.iam.get_account_password_policy"
        metadata = _collection_metadata(
            "get_iam_password_policy",
            "iam:GetAccountPasswordPolicy",
            region=region,
        )
        return _register_and_format(
            raw_output, source, metadata=metadata, session_id=session_id
        )

    @tool("List AWS IAM Users with MFA")
    def list_iam_users_with_mfa(context: str = "") -> str:
        """Lists every IAM user and whether an MFA device is assigned. Essential for NIST SP 800-53 IA-2(1)/IA-2(2) and AC-2 access and multi-factor authentication compliance. The account root user is not included (iam:ListUsers does not return it)."""
        region = None
        try:
            client = _boto_client("iam")
            region = _region_of(client)
            raw_output = json.dumps(
                collect_iam_users_mfa(client), indent=2, default=str
            )
        except (ClientError, BotoCoreError) as e:
            return f"Error listing IAM users: {describe_error(e)}"

        source = "aws.iam.list_users_mfa"
        metadata = _collection_metadata(
            "list_iam_users_with_mfa",
            "iam:ListUsers, iam:ListMFADevices",
            region=region,
        )
        return _register_and_format(
            raw_output, source, metadata=metadata, session_id=session_id
        )

    @tool("List Public S3 Buckets")
    def list_public_s3_buckets(context: str = "") -> str:
        """
        Evaluates every S3 bucket for effective public access: bucket policy status,
        bucket ACL grants to AllUsers/AuthenticatedUsers, and account- and
        bucket-level Block Public Access. Each bucket gets a Verdict of PUBLIC,
        NOT_PUBLIC or UNKNOWN (a read was denied), with reasons. Essential for data
        security audit.
        """
        region = None
        caller_identity = None
        try:
            s3 = _boto_client("s3")
            s3control = _boto_client("s3control")
            sts = _boto_client("sts")
            region = _region_of(s3)
            result = collect_s3_public_access(s3, s3control, sts)
            caller_identity = result.get("CallerIdentityArn")
            raw_output = json.dumps(result, indent=2, default=str)
        except (ClientError, BotoCoreError) as e:
            return f"Error listing S3 buckets: {describe_error(e)}"

        source = "aws.s3.list_public_buckets"
        metadata = _collection_metadata(
            "list_public_s3_buckets",
            "s3:ListBuckets, s3:GetPublicAccessBlock, s3control:GetPublicAccessBlock, "
            "s3:GetBucketPolicyStatus, s3:GetBucketAcl, sts:GetCallerIdentity",
            region=region,
            caller_identity=caller_identity,
        )
        return _register_and_format(
            raw_output, source, metadata=metadata, session_id=session_id
        )

    @tool("Get AWS Root Account Summary")
    def get_root_account_summary(context: str = "") -> str:
        """Checks AWS root account MFA status and active root access keys via iam:GetAccountSummary. Essential for CIS 1.4/1.5 and NIST SP 800-53 IA-2(1)."""
        region = None
        try:
            client = _boto_client("iam")
            region = _region_of(client)
            raw_output = json.dumps(
                collect_root_account_summary(client), indent=2, default=str
            )
        except (ClientError, BotoCoreError) as e:
            return f"Error fetching root account summary: {describe_error(e)}"

        source = "aws.iam.get_account_summary"
        metadata = _collection_metadata(
            "get_root_account_summary",
            "iam:GetAccountSummary",
            region=region,
        )
        return _register_and_format(
            raw_output, source, metadata=metadata, session_id=session_id
        )

    @tool("Get IAM Access Key Age")
    def get_access_key_age(context: str = "") -> str:
        """Lists IAM user access keys and evaluates key age against the 90-day rotation threshold. Essential for CIS 1.14 and NIST SP 800-53 IA-5(1)/AC-2."""
        region = None
        try:
            client = _boto_client("iam")
            region = _region_of(client)
            raw_output = json.dumps(
                collect_access_key_age(client), indent=2, default=str
            )
        except (ClientError, BotoCoreError) as e:
            return f"Error checking IAM access key age: {describe_error(e)}"

        source = "aws.iam.list_access_keys"
        metadata = _collection_metadata(
            "get_access_key_age",
            "iam:ListUsers, iam:ListAccessKeys",
            region=region,
        )
        return _register_and_format(
            raw_output, source, metadata=metadata, session_id=session_id
        )

    @tool("Get CloudTrail Trails")
    def get_cloudtrail_trails(context: str = "") -> str:
        """Checks CloudTrail trails for multi-region logging and log file validation. Essential for CIS 3.1/3.2 and NIST SP 800-53 AU-2/AU-9/AU-12."""
        region = None
        try:
            client = _boto_client("cloudtrail")
            region = _region_of(client)
            raw_output = json.dumps(
                collect_cloudtrail_trails(client), indent=2, default=str
            )
        except (ClientError, BotoCoreError) as e:
            return f"Error checking CloudTrail trails: {describe_error(e)}"

        source = "aws.cloudtrail.describe_trails"
        metadata = _collection_metadata(
            "get_cloudtrail_trails",
            "cloudtrail:DescribeTrails, cloudtrail:GetTrailStatus",
            region=region,
        )
        return _register_and_format(
            raw_output, source, metadata=metadata, session_id=session_id
        )

    @tool("Get KMS Key Rotation")
    def get_kms_key_rotation(context: str = "") -> str:
        """Checks enabled customer-managed symmetric KMS keys for automatic annual key rotation. Essential for CIS 3.6 and NIST SP 800-53 SC-12."""
        region = None
        try:
            client = _boto_client("kms")
            region = _region_of(client)
            raw_output = json.dumps(
                collect_kms_key_rotation(client), indent=2, default=str
            )
        except (ClientError, BotoCoreError) as e:
            return f"Error checking KMS key rotation: {describe_error(e)}"

        source = "aws.kms.get_key_rotation_status"
        metadata = _collection_metadata(
            "get_kms_key_rotation",
            "kms:ListKeys, kms:DescribeKey, kms:GetKeyRotationStatus",
            region=region,
        )
        return _register_and_format(
            raw_output, source, metadata=metadata, session_id=session_id
        )

    @tool("Get GuardDuty Detectors")
    def get_guardduty_detectors(context: str = "") -> str:
        """Checks AWS GuardDuty detectors to confirm active threat detection is enabled. Essential for NIST SP 800-53 SI-4."""
        region = None
        try:
            client = _boto_client("guardduty")
            region = _region_of(client)
            raw_output = json.dumps(
                collect_guardduty_detectors(client), indent=2, default=str
            )
        except (ClientError, BotoCoreError) as e:
            return f"Error checking GuardDuty detectors: {describe_error(e)}"

        source = "aws.guardduty.list_detectors"
        metadata = _collection_metadata(
            "get_guardduty_detectors",
            "guardduty:ListDetectors, guardduty:GetDetector",
            region=region,
        )
        return _register_and_format(
            raw_output, source, metadata=metadata, session_id=session_id
        )

    @tool("Get IAM Access Analyzer")
    def get_iam_access_analyzer(context: str = "") -> str:
        """Checks IAM Access Analyzer status and active external-access findings count. Essential for CIS 1.20 and NIST SP 800-53 AC-2/AC-6."""
        region = None
        try:
            client = _boto_client("accessanalyzer")
            region = _region_of(client)
            raw_output = json.dumps(
                collect_iam_access_analyzer(client), indent=2, default=str
            )
        except (ClientError, BotoCoreError) as e:
            return f"Error checking IAM Access Analyzer: {describe_error(e)}"

        source = "aws.accessanalyzer.list_analyzers"
        metadata = _collection_metadata(
            "get_iam_access_analyzer",
            "access-analyzer:ListAnalyzers, access-analyzer:ListFindings",
            region=region,
        )
        return _register_and_format(
            raw_output, source, metadata=metadata, session_id=session_id
        )

    return [
        get_iam_password_policy,
        list_iam_users_with_mfa,
        list_public_s3_buckets,
        get_root_account_summary,
        get_access_key_age,
        get_cloudtrail_trails,
        get_kms_key_rotation,
        get_guardduty_detectors,
        get_iam_access_analyzer,
    ]


# Session-less instances for the tests and the evaluation harness (evals/),
# which look tools up by name. Evidence they register is not bound to a
# session; audit runs build their own via the factory above.
_test_tools = make_aws_tools(None)
get_iam_password_policy = _test_tools[0]
list_iam_users_with_mfa = _test_tools[1]
list_public_s3_buckets = _test_tools[2]
get_root_account_summary = _test_tools[3]
get_access_key_age = _test_tools[4]
get_cloudtrail_trails = _test_tools[5]
get_kms_key_rotation = _test_tools[6]
get_guardduty_detectors = _test_tools[7]
get_iam_access_analyzer = _test_tools[8]

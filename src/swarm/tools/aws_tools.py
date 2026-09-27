"""CrewAI tools for read-only AWS evidence collection.

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
from crewai.tools import tool

from swarm.evidence import EvidenceAssuranceProtocol, _redact_account_ids
from swarm.evidence import app_version as _app_version
from swarm.tools.aws_checks import (
    collect_iam_users_mfa,
    collect_password_policy,
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


def _register_and_format(raw_output: str, source: str, *, metadata: dict, session_id: str | None = None) -> str:
    vault_record = EvidenceAssuranceProtocol.register_evidence(
        raw_output, source, metadata=metadata, session_id=session_id
    )
    return f"Vault ID: {vault_record['vault_id']}\nRaw Output: {_redact_account_ids(raw_output)}"


def make_aws_tools(session_id: str):
    @tool("Get IAM Password Policy")
    def get_iam_password_policy(context: str = "") -> str:
        """Fetches the AWS IAM account password policy. Essential for AC-01 password rules compliance. If no policy is set, returns a finding saying so."""
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
        return _register_and_format(raw_output, source, metadata=metadata, session_id=session_id)


    @tool("List AWS IAM Users with MFA")
    def list_iam_users_with_mfa(context: str = "") -> str:
        """Lists every IAM user and whether an MFA device is assigned. Essential for AC-02 access compliance. The account root user is not included (iam:ListUsers does not return it)."""
        region = None
        try:
            client = _boto_client("iam")
            region = _region_of(client)
            raw_output = json.dumps(collect_iam_users_mfa(client), indent=2, default=str)
        except (ClientError, BotoCoreError) as e:
            return f"Error listing IAM users: {describe_error(e)}"
        
        source = "aws.iam.list_users_mfa"
        metadata = _collection_metadata(
            "list_iam_users_with_mfa",
            "iam:ListUsers, iam:ListMFADevices",
            region=region,
        )
        return _register_and_format(raw_output, source, metadata=metadata, session_id=session_id)


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
        return _register_and_format(raw_output, source, metadata=metadata, session_id=session_id)

    return [get_iam_password_policy, list_iam_users_with_mfa, list_public_s3_buckets]

# Module-level exports for tests and MCP server
_test_tools = make_aws_tools(None)
get_iam_password_policy = _test_tools[0]
list_iam_users_with_mfa = _test_tools[1]
list_public_s3_buckets = _test_tools[2]

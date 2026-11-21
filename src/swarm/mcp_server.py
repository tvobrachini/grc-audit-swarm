import json
import logging
import sys
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from mcp.server.fastmcp import FastMCP

if __package__ in (None, ""):
    # Run as a script (config/mcp_servers.yaml: python src/swarm/mcp_server.py):
    # make the `swarm` package importable from src/.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swarm.evidence import _redact_account_ids  # noqa: E402
from swarm.tools.aws_checks import (  # noqa: E402
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

logger = logging.getLogger(__name__)

mcp = FastMCP("AWS-Audit-Agent")

# The evidence logic is shared with the agent tools (swarm.tools.aws_checks).
# Unlike the agent tools, this server does not register results in the
# evidence vault, but all tool outputs are redacted so AWS account IDs are
# never leaked.


def _redacted_json(payload: object) -> str:
    return _redact_account_ids(json.dumps(payload, indent=2, default=str))


@mcp.tool()
def get_iam_password_policy() -> str:
    """Fetches the AWS IAM account password policy. Essential for AC-01 audit."""
    logger.info("[MCP Server] Tool called: get_iam_password_policy")
    try:
        client = boto3.client("iam")
    except (ClientError, BotoCoreError) as e:
        return _redact_account_ids(
            f"Error fetching password policy: {describe_error(e)}"
        )
    return _redact_account_ids(collect_password_policy(client))


@mcp.tool()
def list_iam_users_with_mfa() -> str:
    """Lists IAM users and indicates if MFA is enabled. Essential for AC-02 audit."""
    logger.info("[MCP Server] Tool called: list_iam_users_with_mfa")
    try:
        client = boto3.client("iam")
        return _redacted_json(collect_iam_users_mfa(client))
    except (ClientError, BotoCoreError) as e:
        return _redact_account_ids(f"Error listing IAM users: {describe_error(e)}")


@mcp.tool()
def list_public_s3_buckets() -> str:
    """
    Evaluates each S3 bucket for effective public access (bucket policy status,
    ACL, account- and bucket-level Block Public Access). Essential for CLD-10 audit.
    """
    logger.info("[MCP Server] Tool called: list_public_s3_buckets")
    try:
        result = collect_s3_public_access(
            boto3.client("s3"), boto3.client("s3control"), boto3.client("sts")
        )
        return _redacted_json(result)
    except (ClientError, BotoCoreError) as e:
        return _redact_account_ids(f"Error listing S3 buckets: {describe_error(e)}")


@mcp.tool()
def get_root_account_summary() -> str:
    """Inspects AWS root account MFA and root access key status."""
    logger.info("[MCP Server] Tool called: get_root_account_summary")
    try:
        client = boto3.client("iam")
        return _redacted_json(collect_root_account_summary(client))
    except (ClientError, BotoCoreError) as e:
        return _redact_account_ids(
            f"Error fetching root account summary: {describe_error(e)}"
        )


@mcp.tool()
def get_access_key_age() -> str:
    """Lists IAM user access keys and evaluates rotation age against 90 days."""
    logger.info("[MCP Server] Tool called: get_access_key_age")
    try:
        client = boto3.client("iam")
        return _redacted_json(collect_access_key_age(client))
    except (ClientError, BotoCoreError) as e:
        return _redact_account_ids(
            f"Error checking IAM access key age: {describe_error(e)}"
        )


@mcp.tool()
def get_cloudtrail_trails() -> str:
    """Inspects CloudTrail trails for multi-region logging and log file validation."""
    logger.info("[MCP Server] Tool called: get_cloudtrail_trails")
    try:
        client = boto3.client("cloudtrail")
        return _redacted_json(collect_cloudtrail_trails(client))
    except (ClientError, BotoCoreError) as e:
        return _redact_account_ids(
            f"Error checking CloudTrail trails: {describe_error(e)}"
        )


@mcp.tool()
def get_kms_key_rotation() -> str:
    """Inspects enabled customer-managed symmetric KMS keys for annual rotation."""
    logger.info("[MCP Server] Tool called: get_kms_key_rotation")
    try:
        client = boto3.client("kms")
        return _redacted_json(collect_kms_key_rotation(client))
    except (ClientError, BotoCoreError) as e:
        return _redact_account_ids(
            f"Error checking KMS key rotation: {describe_error(e)}"
        )


@mcp.tool()
def get_guardduty_detectors() -> str:
    """Inspects AWS GuardDuty detectors and verifies active threat detection."""
    logger.info("[MCP Server] Tool called: get_guardduty_detectors")
    try:
        client = boto3.client("guardduty")
        return _redacted_json(collect_guardduty_detectors(client))
    except (ClientError, BotoCoreError) as e:
        return _redact_account_ids(
            f"Error checking GuardDuty detectors: {describe_error(e)}"
        )


@mcp.tool()
def get_iam_access_analyzer() -> str:
    """Inspects IAM Access Analyzers and counts active external-access findings."""
    logger.info("[MCP Server] Tool called: get_iam_access_analyzer")
    try:
        client = boto3.client("accessanalyzer")
        return _redacted_json(collect_iam_access_analyzer(client))
    except (ClientError, BotoCoreError) as e:
        return _redact_account_ids(
            f"Error checking IAM Access Analyzer: {describe_error(e)}"
        )


if __name__ == "__main__":
    mcp.run()

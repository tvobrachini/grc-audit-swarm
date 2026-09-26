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

from swarm.tools.aws_checks import (  # noqa: E402
    collect_iam_users_mfa,
    collect_password_policy,
    collect_s3_public_access,
    describe_error,
)

logger = logging.getLogger(__name__)

mcp = FastMCP("AWS-Audit-Agent")

# The evidence logic is shared with the CrewAI tools (swarm.tools.aws_checks).
# Unlike the CrewAI tools, this server does not register results in the
# evidence vault.


@mcp.tool()
def get_iam_password_policy() -> str:
    """Fetches the AWS IAM account password policy. Essential for AC-01 audit."""
    logger.info("[MCP Server] Tool called: get_iam_password_policy")
    try:
        client = boto3.client("iam")
    except (ClientError, BotoCoreError) as e:
        return f"Error fetching password policy: {describe_error(e)}"
    return collect_password_policy(client)


@mcp.tool()
def list_iam_users_with_mfa() -> str:
    """Lists IAM users and indicates if MFA is enabled. Essential for AC-02 audit."""
    logger.info("[MCP Server] Tool called: list_iam_users_with_mfa")
    try:
        client = boto3.client("iam")
        return json.dumps(collect_iam_users_mfa(client), indent=2, default=str)
    except (ClientError, BotoCoreError) as e:
        return f"Error listing IAM users: {describe_error(e)}"


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
        return json.dumps(result, indent=2, default=str)
    except (ClientError, BotoCoreError) as e:
        return f"Error listing S3 buckets: {describe_error(e)}"


if __name__ == "__main__":
    mcp.run()

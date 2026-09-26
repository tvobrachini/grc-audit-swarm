"""CrewAI tools for read-only AWS evidence collection.

The collection logic lives in ``swarm.tools.aws_checks`` (shared with the MCP
server). These wrappers add what the audit path needs: every raw result is
registered in the evidence vault (which redacts account IDs before storing)
and the text returned to the agent is redacted the same way.
"""

import json

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from crewai.tools import tool

from swarm.evidence import EvidenceAssuranceProtocol, _redact_account_ids
from swarm.tools.aws_checks import (
    collect_iam_users_mfa,
    collect_password_policy,
    collect_s3_public_access,
    describe_error,
)


def _boto_client(service: str):
    return boto3.client(service)


def _register_and_format(raw_output: str, source: str) -> str:
    vault_record = EvidenceAssuranceProtocol.register_evidence(raw_output, source)
    return f"Vault ID: {vault_record['vault_id']}\nRaw Output: {_redact_account_ids(raw_output)}"


@tool("Get IAM Password Policy")
def get_iam_password_policy(context: str = "") -> str:
    """Fetches the AWS IAM account password policy. Essential for AC-01 password rules compliance. If no policy is set, returns a finding saying so."""
    try:
        client = _boto_client("iam")
    except (ClientError, BotoCoreError) as e:
        raw_output = f"Error fetching password policy: {describe_error(e)}"
    else:
        raw_output = collect_password_policy(client)
    return _register_and_format(raw_output, "aws.iam.get_account_password_policy")


@tool("List AWS IAM Users with MFA")
def list_iam_users_with_mfa(context: str = "") -> str:
    """Lists every IAM user and whether an MFA device is assigned. Essential for AC-02 access compliance. The account root user is not included (iam:ListUsers does not return it)."""
    try:
        client = _boto_client("iam")
        raw_output = json.dumps(collect_iam_users_mfa(client), indent=2, default=str)
    except (ClientError, BotoCoreError) as e:
        raw_output = f"Error listing IAM users: {describe_error(e)}"
    return _register_and_format(raw_output, "aws.iam.list_users_mfa")


@tool("List Public S3 Buckets")
def list_public_s3_buckets(context: str = "") -> str:
    """
    Evaluates every S3 bucket for effective public access: bucket policy status,
    bucket ACL grants to AllUsers/AuthenticatedUsers, and account- and
    bucket-level Block Public Access. Each bucket gets a Verdict of PUBLIC,
    NOT_PUBLIC or UNKNOWN (a read was denied), with reasons. Essential for data
    security audit.
    """
    try:
        s3 = _boto_client("s3")
        s3control = _boto_client("s3control")
        sts = _boto_client("sts")
        result = collect_s3_public_access(s3, s3control, sts)
        raw_output = json.dumps(result, indent=2, default=str)
    except (ClientError, BotoCoreError) as e:
        raw_output = f"Error listing S3 buckets: {describe_error(e)}"
    return _register_and_format(raw_output, "aws.s3.list_public_buckets")

import concurrent.futures
import json
import logging
import concurrent.futures

import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP("AWS-Audit-Agent")


@mcp.tool()
def get_iam_password_policy() -> str:
    """Fetches the AWS IAM account password policy. Essential for AC-01 audit."""
    logger.info("[MCP Server] Tool called: get_iam_password_policy")
    try:
        client = boto3.client("iam")
        response = client.get_account_password_policy()
        return json.dumps(response.get("PasswordPolicy", {}), indent=2, default=str)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            return "Finding: No account password policy defined."
        return f"Error fetching password policy: {e}"
    except NoCredentialsError as e:
        return f"Error: {e}"


@mcp.tool()
def list_iam_users_with_mfa() -> str:
    """Lists IAM users and indicates if MFA is enabled. Essential for AC-02 audit."""
    logger.info("[MCP Server] Tool called: list_iam_users_with_mfa")
    try:
        client = boto3.client("iam")
        paginator = client.get_paginator("list_users")

        users = []
        for page in paginator.paginate():
            for user in page.get("Users", []):
                users.append(user["UserName"])

        def check_mfa(name):
            try:
                mfa_resp = client.list_mfa_devices(UserName=name)
                has_mfa = "Yes" if mfa_resp.get("MFADevices") else "No"
            except ClientError:
                has_mfa = "Unknown"
            return {"UserName": name, "MFA_Enabled": has_mfa}

        report = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            report = list(executor.map(check_mfa, users))

        return json.dumps(report, indent=2)
    except (ClientError, NoCredentialsError) as e:
        return f"Error listing IAM users: {e}"


def _check_bucket_public_access_mcp(s3, name: str) -> dict:
    entry: dict = {"Bucket": name, "IsPublic": False}
    try:
        pab = s3.get_public_access_block(Bucket=name)["PublicAccessBlockConfiguration"]
        all_blocked = all(
            [
                pab.get("BlockPublicAcls", False),
                pab.get("IgnorePublicAcls", False),
                pab.get("BlockPublicPolicy", False),
                pab.get("RestrictPublicBuckets", False),
            ]
        )
        if not all_blocked:
            entry["IsPublic"] = True
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchPublicAccessBlockConfiguration":
            entry["IsPublic"] = True
    try:
        acl = s3.get_bucket_acl(Bucket=name)
        public = any(
            "AllUsers" in g.get("Grantee", {}).get("URI", "")
            for g in acl.get("Grants", [])
        )
        if public:
            entry["IsPublic"] = True
    except ClientError:
        pass
    return entry


@mcp.tool()
def list_public_s3_buckets() -> str:
    """
    Checks each S3 bucket for public access via PublicAccessBlock and ACL.
    Essential for CLD-10 audit.
    """
    logger.info("[MCP Server] Tool called: list_public_s3_buckets")
    try:
        s3 = boto3.client("s3")
        buckets = s3.list_buckets().get("Buckets", [])
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(_check_bucket_public_access_mcp, s3, bucket["Name"])
                for bucket in buckets
            ]
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())

        # Sort results to maintain deterministic output
        results.sort(key=lambda x: x["Bucket"])

        return json.dumps(results, indent=2, default=str)
    except (ClientError, NoCredentialsError) as e:
        return f"Error listing S3 buckets: {e}"


if __name__ == "__main__":
    mcp.run()

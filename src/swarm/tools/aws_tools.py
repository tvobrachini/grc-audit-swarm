import json
import boto3
import concurrent.futures
from botocore.exceptions import ClientError, NoCredentialsError
from crewai.tools import tool
from swarm.evidence import EvidenceAssuranceProtocol, _redact_account_ids


def _boto_client(service: str):
    return boto3.client(service)


@tool("Get IAM Password Policy")
def get_iam_password_policy(context: str = "") -> str:
    """Fetches the AWS IAM account password policy. Essential for AC-01 password rules compliance."""
    try:
        client = _boto_client("iam")
        response = client.get_account_password_policy()
        raw_output = json.dumps(
            response.get("PasswordPolicy", {}), indent=2, default=str
        )
    except client.exceptions.NoSuchEntityException:
        raw_output = "Finding: No IAM password policy is defined for this account."
    except (ClientError, NoCredentialsError) as e:
        raw_output = f"Error fetching password policy: {e}"

    vault_record = EvidenceAssuranceProtocol.register_evidence(
        raw_output, "aws.iam.get_account_password_policy"
    )
    return f"Vault ID: {vault_record['vault_id']}\nRaw Output: {_redact_account_ids(raw_output)}"


@tool("List AWS IAM Users with MFA")
def list_iam_users_with_mfa(context: str = "") -> str:
    """Lists IAM users and indicates if MFA is enabled. Essential for AC-02 access compliance."""
    try:
        client = _boto_client("iam")
        paginator = client.get_paginator("list_users")
        report = []
        for page in paginator.paginate():
            for user in page.get("Users", []):
                name = user["UserName"]
                try:
                    mfa_resp = client.list_mfa_devices(UserName=name)
                    has_mfa = "Yes" if mfa_resp.get("MFADevices") else "No"
                except ClientError:
                    has_mfa = "Unknown"
                report.append({"UserName": name, "MFA_Enabled": has_mfa})

        raw_output = json.dumps(report, indent=2)
    except (ClientError, NoCredentialsError) as e:
        raw_output = f"Error listing IAM users: {e}"

    vault_record = EvidenceAssuranceProtocol.register_evidence(
        raw_output, "aws.iam.list_users_mfa"
    )
    return f"Vault ID: {vault_record['vault_id']}\nRaw Output: {_redact_account_ids(raw_output)}"


def _check_bucket_public_access(s3, name: str) -> dict:
    entry: dict = {
        "Bucket": name,
        "PublicAccessBlockEnabled": None,
        "ACL": None,
        "IsPublic": False,
    }

    # Check bucket-level Public Access Block settings.
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
        entry["PublicAccessBlockEnabled"] = all_blocked
        if not all_blocked:
            entry["IsPublic"] = True
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchPublicAccessBlockConfiguration":
            # No block config means public access controls are not restricted at bucket level.
            entry["PublicAccessBlockEnabled"] = False
            entry["IsPublic"] = True
        else:
            entry["PublicAccessBlockEnabled"] = f"Error: {e}"

    # Check bucket ACL for any public grants.
    try:
        acl = s3.get_bucket_acl(Bucket=name)
        public_grantees = [
            g["Grantee"].get("URI", "")
            for g in acl.get("Grants", [])
            if "URI" in g.get("Grantee", {})
            and "AllUsers" in g["Grantee"].get("URI", "")
        ]
        entry["ACL"] = "Public" if public_grantees else "Private"
        if public_grantees:
            entry["IsPublic"] = True
    except ClientError as e:
        entry["ACL"] = f"Error: {e}"

    return entry


@tool("List Public S3 Buckets")
def list_public_s3_buckets(context: str = "") -> str:
    """
    Checks each S3 bucket for public access by inspecting the bucket-level
    PublicAccessBlock configuration and bucket ACL. Essential for data security audit.
    """
    try:
        s3 = _boto_client("s3")
        buckets_resp = s3.list_buckets()
        buckets = buckets_resp.get("Buckets", [])

        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(_check_bucket_public_access, s3, bucket["Name"])
                for bucket in buckets
            ]
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())

        # Sort results to maintain deterministic output
        results.sort(key=lambda x: x["Bucket"])

        raw_output = json.dumps(results, indent=2, default=str)
    except (ClientError, NoCredentialsError) as e:
        raw_output = f"Error listing S3 buckets: {e}"

    vault_record = EvidenceAssuranceProtocol.register_evidence(
        raw_output, "aws.s3.list_public_buckets"
    )
    return f"Vault ID: {vault_record['vault_id']}\nRaw Output: {_redact_account_ids(raw_output)}"

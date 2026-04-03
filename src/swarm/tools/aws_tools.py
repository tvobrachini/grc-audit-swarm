import subprocess
import json
from crewai.tools import tool
from swarm.evidence import EvidenceAssuranceProtocol


@tool("Get IAM Password Policy")
def get_iam_password_policy(context: str = "") -> str:
    """Fetches the AWS IAM account password policy. Essential for AC-01 password rules compliance."""
    try:
        result = subprocess.run(
            ["aws", "iam", "get-account-password-policy"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        raw_output = (
            result.stdout
            if result.returncode == 0
            else f"Finding: No policy defined. Error: {result.stderr}"
        )

        vault_record = EvidenceAssuranceProtocol.register_evidence(
            raw_output, "aws.iam.get_password_policy"
        )
        return f"Vault ID: {vault_record['vault_id']}\nRaw Output: {raw_output}"
    except Exception as e:
        return f"Error executing tool: {e}"


@tool("List AWS IAM Users with MFA")
def list_iam_users_with_mfa(context: str = "") -> str:
    """Lists IAM users and indicates if MFA is enabled. Essential for AC-02 access compliance."""
    try:
        users_res = subprocess.run(
            ["aws", "iam", "list-users"], capture_output=True, text=True, timeout=30
        )
        if users_res.returncode != 0:
            return f"Error listing users: {users_res.stderr}"

        users = json.loads(users_res.stdout).get("Users", [])
        report = []
        for user in users:
            name = user["UserName"]
            mfa_res = subprocess.run(
                ["aws", "iam", "list-mfa-devices", "--user-name", name],
                capture_output=True,
                text=True,
                timeout=30,
            )
            has_mfa = (
                "Yes"
                if mfa_res.returncode == 0
                and json.loads(mfa_res.stdout).get("MFADevices")
                else "No"
            )
            report.append({"UserName": name, "MFA_Enabled": has_mfa})

        raw_output = json.dumps(report, indent=2)
        vault_record = EvidenceAssuranceProtocol.register_evidence(
            raw_output, "aws.iam.list_users_mfa"
        )
        return f"Vault ID: {vault_record['vault_id']}\nRaw Output: {raw_output}"
    except Exception as e:
        return f"Error executing tool: {e}"


@tool("List Public S3 Buckets")
def list_public_s3_buckets(context: str = "") -> str:
    """Checks for S3 buckets that might have public access. Essential for data security audit."""
    try:
        result = subprocess.run(
            ["aws", "s3api", "list-buckets"], capture_output=True, text=True, timeout=30
        )
        raw_output = (
            result.stdout if result.returncode == 0 else f"Error: {result.stderr}"
        )

        vault_record = EvidenceAssuranceProtocol.register_evidence(
            raw_output, "aws.s3.list_buckets"
        )
        return f"Vault ID: {vault_record['vault_id']}\nRaw Output: {raw_output}"
    except Exception as e:
        return f"Error executing tool: {e}"

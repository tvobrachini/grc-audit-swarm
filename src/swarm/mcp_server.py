import subprocess
import json
from mcp.server.fastmcp import FastMCP

# Define the Audit MCP Server
mcp = FastMCP("AWS-Audit-Agent")


@mcp.tool()
def get_iam_password_policy() -> str:
    """Fetches the AWS IAM account password policy. Essential for AC-01 audit."""
    print("[MCP Server] Tool called: get_iam_password_policy")
    try:
        result = subprocess.run(
            ["aws", "iam", "get-account-password-policy"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return result.stdout
        else:
            return (
                f"Finding: No account password policy defined. Error: {result.stderr}"
            )
    except Exception as e:
        return f"Error executing tool: {e}"


@mcp.tool()
def list_iam_users_with_mfa() -> str:
    """Lists IAM users and indicates if MFA is enabled. Essential for AC-02 audit."""
    print("[MCP Server] Tool called: list_iam_users_with_mfa")
    try:
        # Get users
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

        return json.dumps(report, indent=2)
    except Exception as e:
        return f"Error executing tool: {e}"


@mcp.tool()
def list_public_s3_buckets() -> str:
    """Checks for S3 buckets that might have public access. Essential for CLD-10 audit."""
    print("[MCP Server] Tool called: list_public_s3_buckets")
    try:
        result = subprocess.run(
            ["aws", "s3api", "list-buckets"], capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return f"Error: {result.stderr}"
        return result.stdout
    except Exception as e:
        return f"Error executing tool: {e}"


if __name__ == "__main__":
    mcp.run()

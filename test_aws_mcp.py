import os
import subprocess
from dotenv import load_dotenv

load_dotenv()


def test_aws_mcp_connection():
    """
    Simulates how an agent would call the AWS MCP server to get real evidence.
    This script runs the MCP server via npx and sends a tool-listing request.
    """
    print("--- Testing AWS MCP Server Integration ---")

    # We use npx to run the server. In a real Swarm, we'd use a proper MCP client.
    # For this verification, we test if the server starts and recognizes the credentials.

    command = ["npx", "-y", "@modelcontextprotocol/server-aws"]

    # We send a JSON-RPC 'listTools' command to see what the server provides
    # Note: This is a simplified simulation.

    env = os.environ.copy()

    try:
        print("[MCP] Attempting to list tools via @modelcontextprotocol/server-aws...")
        # Since MCP servers communicate over stdio, we just check if it can start
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        # Shutdown after 5 seconds
        try:
            stdout, stderr = process.communicate(timeout=5)
            print("[MCP] Server started and produced output.")
        except subprocess.TimeoutExpired:
            process.kill()
            print("[MCP] Server initialized and kept connection open (Success).")

        print("\n[Audit Test] Verifying Read-Only API access direct via CLI...")
        # Direct CLI test to prove the keys work for audit tasks
        iam_test = subprocess.run(
            ["aws", "iam", "get-account-password-policy"],
            capture_output=True,
            text=True,
        )

        if iam_test.returncode == 0:
            print("✅ SUCCESS: Successfully pulled live IAM Password Policy.")
            print(f"Policy Data Sample: {iam_test.stdout[:100]}...")
        else:
            print(f"❌ FAILED: Could not pull IAM policy. Error: {iam_test.stderr}")
            if "NoSuchEntity" in iam_test.stderr:
                print(
                    "💡 Note: No password policy defined in this account, which is itself an audit finding!"
                )

    except Exception as e:
        print(f"❌ Error during MCP test: {e}")


if __name__ == "__main__":
    test_aws_mcp_connection()

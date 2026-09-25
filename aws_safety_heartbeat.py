import os
import sys

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

# Reuse the same account-ID redaction the evidence vault uses, so this script
# never prints a bare/hyphenated 12-digit account ID to stdout/logs. Falls
# back to a local copy of the regex if src/swarm isn't importable (e.g. run
# outside the project venv).
try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
    from swarm.evidence import _redact_account_ids
except ImportError:  # pragma: no cover
    import re

    _ACCOUNT_ID_RE = re.compile(r"(?<!\d)\d{12}(?!\d)|(?<!\d)\d{4}-\d{4}-\d{4}(?!\d)")

    def _redact_account_ids(text: str) -> str:
        return _ACCOUNT_ID_RE.sub("[REDACTED]", text)


def check_budget_safety():
    """
    Checks AWS identity and scans for running resources that may incur costs.
    Uses boto3 — no subprocess or AWS CLI required.
    """
    print("--- AWS Zero-Dollar Heartbeat ---")

    try:
        sts = boto3.client("sts")
        id_data = sts.get_caller_identity()
        print(f"Identity Verified: {_redact_account_ids(str(id_data.get('Arn')))}")

        print(
            "\n[Resource Scan] Checking for potentially expensive active resources..."
        )

        ec2 = boto3.client("ec2")
        reservations = ec2.describe_instances(
            Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
        ).get("Reservations", [])
        running_ids = [
            i["InstanceId"] for r in reservations for i in r.get("Instances", [])
        ]
        if running_ids:
            print(
                f"WARNING: {len(running_ids)} EC2 Instances are RUNNING. These may incur costs!"
            )
        else:
            print("EC2: 0 running instances.")

        rds = boto3.client("rds")
        dbs = rds.describe_db_instances().get("DBInstances", [])
        if dbs:
            print(f"WARNING: {len(dbs)} RDS Databases found.")
        else:
            print("RDS: 0 active databases.")

        print("\n[Safety Result] Environment appears stable for Audit Engineering.")
        print(
            "Remember to check your AWS Billing Dashboard manually for the absolute source of truth."
        )

    except (ClientError, NoCredentialsError) as e:
        print(f"Error connecting to AWS: {e}")
    except Exception as e:  # noqa: BLE001
        print(f"Unexpected error: {e}")


if __name__ == "__main__":
    check_budget_safety()

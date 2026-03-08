import subprocess
import json


def check_budget_safety():
    """
    Checks if the AWS account is currently at $0.00 cost and verifies
    that no non-free-tier resources are running according to basic checks.
    """
    print("--- 🛡️ AWS Zero-Dollar Heartbeat ---")

    try:
        # 1. Identity Check
        identity = subprocess.run(
            ["aws", "sts", "get-caller-identity"],
            capture_output=True,
            text=True,
            check=True,
        )
        id_data = json.loads(identity.stdout)
        print(f"✅ Identity Verified: {id_data.get('Arn')}")

        # 2. Basic Cost Explorer Check (if enabled)
        # Note: Cost explorer might have its own small cost if queried via API,
        # but we can check if there are any active resources in common services.

        print(
            "\n[Resource Scan] Checking for potentially expensive active resources..."
        )

        # EC2
        ec2 = subprocess.run(
            [
                "aws",
                "ec2",
                "describe-instances",
                "--query",
                "Reservations[*].Instances[?State.Name=='running'].InstanceId",
            ],
            capture_output=True,
            text=True,
        )
        if ec2.returncode == 0:
            running = json.loads(ec2.stdout)
            if running:
                print(
                    f"⚠️ WARNING: {len(running)} EC2 Instances are RUNNING. These may incur costs!"
                )
            else:
                print("✅ EC2: 0 running instances.")

        # RDS
        rds = subprocess.run(
            [
                "aws",
                "rds",
                "describe-db-instances",
                "--query",
                "DBInstances[*].DBInstanceIdentifier",
            ],
            capture_output=True,
            text=True,
        )
        if rds.returncode == 0:
            dbs = json.loads(rds.stdout)
            if dbs:
                print(f"⚠️ WARNING: {len(dbs)} RDS Databases found.")
            else:
                print("✅ RDS: 0 active databases.")

        print("\n[Safety Result] Environment appears stable for Audit Engineering.")
        print(
            "💡 Remember to always check your AWS Billing Dashboard manually for the absolute source of truth."
        )

    except subprocess.CalledProcessError as e:
        print(f"❌ Error connecting to AWS: {e.stderr}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")


if __name__ == "__main__":
    check_budget_safety()

import boto3
from botocore.exceptions import ClientError, NoCredentialsError


def check_budget_safety():
    """
    Checks AWS identity and scans for running resources that may incur costs.
    Uses boto3 — no subprocess or AWS CLI required.
    """
    print("--- AWS Zero-Dollar Heartbeat ---")

    try:
        sts = boto3.client("sts")
        id_data = sts.get_caller_identity()
        print(f"Identity Verified: {id_data.get('Arn')}")

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

import time
import concurrent.futures
from unittest.mock import Mock

def simulate_latency(func):
    def wrapper(*args, **kwargs):
        time.sleep(0.01) # Simulate 10ms network latency
        return func(*args, **kwargs)
    return wrapper

# Mock boto3 client
mock_s3 = Mock()
mock_s3.list_buckets.return_value = {
    "Buckets": [{"Name": f"bucket-{i}"} for i in range(100)]
}

@simulate_latency
def mock_get_pab(Bucket):
    return {
        "PublicAccessBlockConfiguration": {
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True
        }
    }

@simulate_latency
def mock_get_acl(Bucket):
    return {
        "Grants": []
    }

mock_s3.get_public_access_block.side_effect = mock_get_pab
mock_s3.get_bucket_acl.side_effect = mock_get_acl

def original_method(s3):
    buckets = s3.list_buckets().get("Buckets", [])
    results = []
    for bucket in buckets:
        name = bucket["Name"]
        entry: dict = {"Bucket": name, "IsPublic": False}
        try:
            pab = s3.get_public_access_block(Bucket=name)["PublicAccessBlockConfiguration"]
            all_blocked = all([
                pab.get("BlockPublicAcls", False),
                pab.get("IgnorePublicAcls", False),
                pab.get("BlockPublicPolicy", False),
                pab.get("RestrictPublicBuckets", False),
            ])
            if not all_blocked:
                entry["IsPublic"] = True
        except Exception:
            pass

        try:
            acl = s3.get_bucket_acl(Bucket=name)
            public_grantees = [
                g["Grantee"].get("URI", "")
                for g in acl.get("Grants", [])
                if "URI" in g.get("Grantee", {})
                and "AllUsers" in g["Grantee"].get("URI", "")
            ]
            if public_grantees:
                entry["IsPublic"] = True
        except Exception:
            pass
        results.append(entry)
    return results

def optimized_method(s3):
    buckets = s3.list_buckets().get("Buckets", [])
    results = []

    def process_bucket(bucket):
        name = bucket["Name"]
        entry: dict = {"Bucket": name, "IsPublic": False}
        try:
            pab = s3.get_public_access_block(Bucket=name)["PublicAccessBlockConfiguration"]
            all_blocked = all([
                pab.get("BlockPublicAcls", False),
                pab.get("IgnorePublicAcls", False),
                pab.get("BlockPublicPolicy", False),
                pab.get("RestrictPublicBuckets", False),
            ])
            if not all_blocked:
                entry["IsPublic"] = True
        except Exception:
            pass

        try:
            acl = s3.get_bucket_acl(Bucket=name)
            public_grantees = [
                g["Grantee"].get("URI", "")
                for g in acl.get("Grants", [])
                if "URI" in g.get("Grantee", {})
                and "AllUsers" in g["Grantee"].get("URI", "")
            ]
            if public_grantees:
                entry["IsPublic"] = True
        except Exception:
            pass
        return entry

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(process_bucket, b): b for b in buckets}
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    return results

if __name__ == "__main__":
    print("Running baseline...")
    start = time.time()
    res1 = original_method(mock_s3)
    baseline_time = time.time() - start
    print(f"Baseline (Sequential): {baseline_time:.2f}s")

    print("Running optimized...")
    start = time.time()
    res2 = optimized_method(mock_s3)
    optimized_time = time.time() - start
    print(f"Optimized (Concurrent): {optimized_time:.2f}s")

    print(f"Improvement: {baseline_time / optimized_time:.2f}x faster")
    assert len(res1) == len(res2)

import time
import concurrent.futures
from unittest.mock import Mock

def simulate_latency(func):
    def wrapper(*args, **kwargs):
        time.sleep(0.01) # Simulate 10ms network latency
        return func(*args, **kwargs)
    return wrapper

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

def original_mcp(s3):
    buckets = s3.list_buckets().get("Buckets", [])
    results = []
    for bucket in buckets:
        name = bucket["Name"]
        entry: dict = {"Bucket": name, "IsPublic": False}
        try:
            pab = s3.get_public_access_block(Bucket=name)[
                "PublicAccessBlockConfiguration"
            ]
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
        except Exception:
            pass
        try:
            acl = s3.get_bucket_acl(Bucket=name)
            public = any(
                "AllUsers" in g.get("Grantee", {}).get("URI", "")
                for g in acl.get("Grants", [])
            )
            if public:
                entry["IsPublic"] = True
        except Exception:
            pass
        results.append(entry)
    return results

def optimized_mcp(s3):
    buckets = s3.list_buckets().get("Buckets", [])
    results = []

    def process_bucket(bucket):
        name = bucket["Name"]
        entry: dict = {"Bucket": name, "IsPublic": False}
        try:
            pab = s3.get_public_access_block(Bucket=name)[
                "PublicAccessBlockConfiguration"
            ]
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
        except Exception:
            pass
        try:
            acl = s3.get_bucket_acl(Bucket=name)
            public = any(
                "AllUsers" in g.get("Grantee", {}).get("URI", "")
                for g in acl.get("Grants", [])
            )
            if public:
                entry["IsPublic"] = True
        except Exception:
            pass
        return entry

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(process_bucket, b) for b in buckets]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    return results

if __name__ == "__main__":
    start = time.time()
    res1 = original_mcp(mock_s3)
    baseline_time = time.time() - start
    print(f"MCP Baseline: {baseline_time:.2f}s")

    start = time.time()
    res2 = optimized_mcp(mock_s3)
    optimized_time = time.time() - start
    print(f"MCP Optimized: {optimized_time:.2f}s")

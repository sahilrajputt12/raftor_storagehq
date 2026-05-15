import argparse

import boto3
from botocore.config import Config


def main() -> None:
    p = argparse.ArgumentParser(description="Test Cloudflare R2 connectivity")
    p.add_argument("--endpoint", required=True)
    p.add_argument("--access-key", required=True, dest="access_key")
    p.add_argument("--secret-key", required=True, dest="secret_key")
    p.add_argument("--bucket", required=True)
    p.add_argument("--public-url", required=True, dest="public_url")
    args = p.parse_args()

    client = boto3.client(
        "s3",
        endpoint_url=args.endpoint,
        aws_access_key_id=args.access_key,
        aws_secret_access_key=args.secret_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )

    resp = client.list_objects_v2(Bucket=args.bucket, MaxKeys=5)
    print(f"OK (objects found: {resp.get('KeyCount', 0)})")

    test_key = "test-site/public/r2_test_ping.txt"
    test_content = b"frappe-r2-storage connectivity test"
    client.put_object(
        Bucket=args.bucket,
        Key=test_key,
        Body=test_content,
        ContentType="text/plain",
    )

    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": args.bucket, "Key": test_key},
        ExpiresIn=60,
    )
    print(url)

    cdn_url = f"{args.public_url.rstrip('/')}/test-site/public/r2_test_ping.txt"
    print(cdn_url)

    client.delete_object(Bucket=args.bucket, Key=test_key)

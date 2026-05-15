#!/usr/bin/env python3
"""
scripts/test_r2_connection.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Quick sanity-check: run this OUTSIDE Frappe (plain Python) to verify
that your R2 credentials and bucket are reachable before wiring up the app.

Usage:
    python scripts/test_r2_connection.py \
        --endpoint  https://<ACCOUNT_ID>.r2.cloudflarestorage.com \
        --access-key <KEY_ID> \
        --secret-key <SECRET> \
        --bucket     my-erpnext-bucket \
        --public-url https://cdn.yourdomain.com
"""

import argparse
import sys
import boto3
from botocore.config import Config


def main() -> None:
    p = argparse.ArgumentParser(description="Test Cloudflare R2 connectivity")
    p.add_argument("--endpoint",   required=True)
    p.add_argument("--access-key", required=True, dest="access_key")
    p.add_argument("--secret-key", required=True, dest="secret_key")
    p.add_argument("--bucket",     required=True)
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

    # ── 1. List bucket (connectivity check) ─────────────────────────────────
    print("1. Listing bucket …", end=" ")
    resp = client.list_objects_v2(Bucket=args.bucket, MaxKeys=5)
    print(f"OK  (objects found: {resp.get('KeyCount', 0)})")

    # ── 2. Upload a test object ──────────────────────────────────────────────
    test_key     = "test-site/public/r2_test_ping.txt"
    test_content = b"frappe-r2-storage connectivity test"
    print(f"2. Uploading test object  key={test_key} …", end=" ")
    client.put_object(
        Bucket=args.bucket,
        Key=test_key,
        Body=test_content,
        ContentType="text/plain",
    )
    print("OK")

    # ── 3. Generate a signed URL for it ─────────────────────────────────────
    print("3. Generating signed URL …", end=" ")
    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": args.bucket, "Key": test_key},
        ExpiresIn=60,
    )
    print(f"OK\n   {url}")

    # ── 4. Expected CDN URL ──────────────────────────────────────────────────
    cdn_url = f"{args.public_url.rstrip('/')}/test-site/public/r2_test_ping.txt"
    print(f"4. Public CDN URL would be:\n   {cdn_url}")

    # ── 5. Clean up ──────────────────────────────────────────────────────────
    print("5. Deleting test object …", end=" ")
    client.delete_object(Bucket=args.bucket, Key=test_key)
    print("OK")

    print("\n✅  All checks passed – R2 is reachable and writable.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\n❌  Test failed: {exc}", file=sys.stderr)
        sys.exit(1)

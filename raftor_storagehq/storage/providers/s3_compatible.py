from __future__ import annotations

import frappe
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from raftor_storagehq.storage.base import StorageAdapter


class S3CompatibleStorageAdapter(StorageAdapter):
    """
    Adapter for S3-compatible providers (Cloudflare R2, AWS S3, MinIO, ...).
    """

    def __init__(self, config: dict):
        self.config = config

    def _client(self):
        cache_attr = "_raftor_storagehq_s3_client"
        if not hasattr(frappe.local, cache_attr):
            setattr(
                frappe.local,
                cache_attr,
                boto3.client(
                    "s3",
                    endpoint_url=self.config["endpoint"],
                    aws_access_key_id=self.config["access_key"],
                    aws_secret_access_key=self.config["secret_key"],
                    config=Config(signature_version="s3v4"),
                    region_name=self.config.get("region") or "auto",
                ),
            )
        return getattr(frappe.local, cache_attr)

    def get_native_client(self):
        return self._client()

    def upload_object(
        self,
        *,
        bucket: str,
        key: str,
        content: bytes | Any,
        content_type: str,
        is_private: bool,
    ) -> None:
        extra_args: dict = {"ContentType": content_type}
        if not is_private:
            extra_args["ACL"] = "public-read"

        self._client().put_object(
            Bucket=bucket,
            Key=key,
            Body=content,
            **extra_args,
        )

    def delete_object(self, *, bucket: str, key: str) -> None:
        self._client().delete_object(Bucket=bucket, Key=key)

    def copy_object(
        self,
        *,
        bucket: str,
        source_key: str,
        target_key: str,
        is_private: bool,
    ) -> None:
        extra_args: dict = {
            "Bucket": bucket,
            "CopySource": {"Bucket": bucket, "Key": source_key},
            "Key": target_key,
            "MetadataDirective": "COPY",
        }
        if not is_private:
            extra_args["ACL"] = "public-read"

        self._client().copy_object(**extra_args)

    def get_object_metadata(self, *, bucket: str, key: str) -> dict | None:
        try:
            response = self._client().head_object(Bucket=bucket, Key=key)
            return {
                "content_length": response.get("ContentLength"),
                "etag": (response.get("ETag") or "").strip('"'),
            }
        except ClientError as exc:
            code = (exc.response or {}).get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise

    def generate_signed_download_url(
        self,
        *,
        bucket: str,
        key: str,
        expiry: int,
    ) -> str:
        return self._client().generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expiry,
        )

    def check_connectivity(self, *, bucket: str) -> None:
        self._client().list_objects_v2(Bucket=bucket, MaxKeys=1)

    def download_object(self, *, bucket: str, key: str, local_path: str) -> None:
        import os
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        self._client().download_file(Bucket=bucket, Key=key, Filename=local_path)

    def list_objects_with_prefix(self, *, bucket: str, prefix: str) -> list[dict]:
        """List all objects under *prefix*. Returns list of {key, size, last_modified}."""
        results = []
        paginator = self._client().get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                results.append({
                    "key": obj["Key"],
                    "size": obj["Size"],
                    "last_modified": obj["LastModified"].isoformat(),
                })
        return results

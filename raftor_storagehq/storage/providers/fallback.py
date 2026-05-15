from __future__ import annotations

import logging

from raftor_storagehq.storage.base import StorageAdapter

logger = logging.getLogger(__name__)


class FallbackStorageAdapter(StorageAdapter):
    """Adapter that delegates to a primary adapter and falls back to another.

    Only upload/delete/copy/metadata/connectivity are supported for fallback.
    Signed URL generation is always delegated to the primary adapter.
    """

    def __init__(self, primary: StorageAdapter, fallback: StorageAdapter):
        self.primary = primary
        self.fallback = fallback

    def upload_object(
        self,
        *,
        bucket: str,
        key: str,
        content: bytes,
        content_type: str,
        is_private: bool,
    ) -> None:
        try:
            self.primary.upload_object(
                bucket=bucket,
                key=key,
                content=content,
                content_type=content_type,
                is_private=is_private,
            )
        except Exception as exc:
            logger.warning("StorageHQ: primary upload failed (%s), falling back to local: %s", key, exc)
            self.fallback.upload_object(
                bucket=bucket,
                key=key,
                content=content,
                content_type=content_type,
                is_private=is_private,
            )

    def delete_object(self, *, bucket: str, key: str) -> None:
        try:
            self.primary.delete_object(bucket=bucket, key=key)
        except Exception as exc:
            logger.warning("StorageHQ: primary delete failed (%s), falling back to local: %s", key, exc)
            self.fallback.delete_object(bucket=bucket, key=key)

    def copy_object(
        self,
        *,
        bucket: str,
        source_key: str,
        target_key: str,
        is_private: bool,
    ) -> None:
        try:
            self.primary.copy_object(
                bucket=bucket,
                source_key=source_key,
                target_key=target_key,
                is_private=is_private,
            )
        except Exception as exc:
            logger.warning(
                "StorageHQ: primary copy failed (%s -> %s), falling back to local: %s",
                source_key, target_key, exc,
            )
            self.fallback.copy_object(
                bucket=bucket,
                source_key=source_key,
                target_key=target_key,
                is_private=is_private,
            )

    def get_object_metadata(self, *, bucket: str, key: str) -> dict | None:
        meta = self.primary.get_object_metadata(bucket=bucket, key=key)
        if meta is not None:
            return meta
        return self.fallback.get_object_metadata(bucket=bucket, key=key)

    def generate_signed_download_url(self, *, bucket: str, key: str, expiry: int) -> str:
        return self.primary.generate_signed_download_url(
            bucket=bucket,
            key=key,
            expiry=expiry,
        )

    def check_connectivity(self, *, bucket: str) -> None:
        try:
            self.primary.check_connectivity(bucket=bucket)
        except Exception as exc:
            logger.warning("StorageHQ: primary connectivity check failed, falling back to local: %s", exc)
            self.fallback.check_connectivity(bucket=bucket)

    def download_object(self, *, bucket: str, key: str, local_path: str) -> None:
        # Always download from the primary (cloud) adapter.
        self.primary.download_object(bucket=bucket, key=key, local_path=local_path)

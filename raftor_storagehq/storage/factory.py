from __future__ import annotations

import frappe

from raftor_storagehq.storage.base import StorageAdapter
from raftor_storagehq.storage.providers.fallback import FallbackStorageAdapter
from raftor_storagehq.storage.providers.local_disk import LocalDiskStorageAdapter
from raftor_storagehq.storage.providers.s3_compatible import S3CompatibleStorageAdapter


def get_storage_adapter(config: dict) -> StorageAdapter:
    """
    Return a cached adapter instance for the current request.
    """
    provider = (config.get("provider") or "r2").strip().lower()
    allow_fallback_to_local = bool(config.get("allow_fallback_to_local", False))
    cache_attr = f"_raftor_storagehq_adapter_{provider}_{int(allow_fallback_to_local)}"

    if hasattr(frappe.local, cache_attr):
        return getattr(frappe.local, cache_attr)

    adapter: StorageAdapter
    if provider in {"local"}:
        adapter = LocalDiskStorageAdapter(config)
    elif provider in {"r2", "s3", "minio"}:
        primary = S3CompatibleStorageAdapter(config)
        if allow_fallback_to_local:
            adapter = FallbackStorageAdapter(primary=primary, fallback=LocalDiskStorageAdapter(config))
        else:
            adapter = primary
    else:
        raise ValueError(
            f"Unsupported storage provider '{provider}'. "
            "Supported providers: local, r2, s3, minio"
        )

    setattr(frappe.local, cache_attr, adapter)
    return adapter

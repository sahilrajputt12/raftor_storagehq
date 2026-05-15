"""raftor_storagehq.overrides.storage_client

Storage facade with adapter-pattern delegation.

Canonical, storage-provider-neutral API surface.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

import frappe

from raftor_storagehq.settings import get_effective_storage_config
from raftor_storagehq.storage import get_storage_adapter as get_unified_storage_adapter

logger = logging.getLogger(__name__)

DEFAULT_PREFIX_STRATEGY = "site_name"
DOMAIN_HIERARCHY_STRATEGY = "domain_hierarchy"
VALID_PREFIX_STRATEGIES = {
    DEFAULT_PREFIX_STRATEGY,
    DOMAIN_HIERARCHY_STRATEGY,
}
DEFAULT_STORAGE_PROVIDER = "r2"
VALID_STORAGE_PROVIDERS = {"r2", "s3", "minio"}


def normalize_storage_provider(provider: str | None) -> str:
    provider = (provider or "").strip().lower()
    if provider in VALID_STORAGE_PROVIDERS:
        return provider
    return ""


def parse_bucket_address(raw: str) -> tuple[str, str]:
    raw = raw.strip().strip("/")
    if "/" in raw:
        bucket, _, rest = raw.partition("/")
        return bucket.strip(), rest.strip("/")
    return raw, ""


def normalize_prefix_strategy(strategy: str | None) -> str:
    strategy = (strategy or DEFAULT_PREFIX_STRATEGY).strip().lower()
    if strategy not in VALID_PREFIX_STRATEGIES:
        return DEFAULT_PREFIX_STRATEGY
    return strategy


def get_storage_config() -> dict:
    conf = get_effective_storage_config()

    enabled = bool(conf.get("enabled"))
    provider = normalize_storage_provider(conf.get("provider"))
    backup_enabled = bool(conf.get("backup_enabled"))

    # Cloud mode is only active for live file redirection.
    cloud_enabled = enabled and bool(provider)

    backup_ready = bool(
        backup_enabled
        and bool(provider)
        and bool((conf.get("bucket") or "").strip())
        and bool((conf.get("endpoint") or "").strip())
        and bool((conf.get("access_key") or "").strip())
        and bool((conf.get("secret_key") or "").strip())
    )

    if (enabled or backup_enabled) and not provider:
        raise ValueError(
            "StorageHQ is enabled for storage/backups but provider is not configured. "
            "Please select a provider (r2/s3/minio) in StorageHQ Settings."
        )

    raw_bucket = (conf.get("bucket") or "").strip()
    if (cloud_enabled or backup_ready) and not raw_bucket:
        raise ValueError(
            "StorageHQ is enabled but bucket is not configured. "
            "Please set bucket in StorageHQ Settings."
        )

    bucket, bucket_prefix = parse_bucket_address(raw_bucket)

    return {
        "enabled": enabled,
        "cloud_enabled": cloud_enabled,
        "backup_enabled": backup_enabled,
        "backup_ready": backup_ready,
        "provider": provider,
        "bucket": bucket,
        "bucket_prefix": bucket_prefix,
        "endpoint": conf.get("endpoint", ""),
        "access_key": conf.get("access_key", ""),
        "secret_key": conf.get("secret_key", ""),
        "region": conf.get("region"),
        "public_base_url": (conf.get("public_base_url") or "").rstrip("/"),
        "prefix_strategy": normalize_prefix_strategy(conf.get("prefix_strategy", DEFAULT_PREFIX_STRATEGY)),
        "private_url_expiry_seconds": int(conf.get("private_url_expiry_seconds") or 300),
        "allow_fallback_to_local": bool(conf.get("allow_fallback_to_local", True)),
    }


def is_storage_enabled() -> bool:
    return bool(get_storage_config()["enabled"])


def is_cloud_storage_enabled() -> bool:
    return bool(get_storage_config()["cloud_enabled"])


def is_backup_ready() -> bool:
    """Return True if backup uploads/listing/restore can use cloud storage."""
    try:
        return bool(get_storage_config()["backup_ready"])
    except Exception:
        return False


def get_storage_adapter():
    cfg = get_storage_config()
    return get_unified_storage_adapter(cfg)


def get_site_name() -> str:
    return frappe.local.site


def get_storage_prefix(site_name: str | None = None, strategy: str | None = None) -> str:
    site_name = site_name or get_site_name()
    strategy = normalize_prefix_strategy(strategy or get_storage_config()["prefix_strategy"])

    if strategy == DOMAIN_HIERARCHY_STRATEGY:
        parts = [part for part in site_name.split(".") if part]
        if len(parts) >= 2:
            root = parts[-2]
            subdomains = list(reversed(parts[:-2]))
            return "/".join([root, *subdomains]) if subdomains else root

    return site_name


def build_object_key(
    file_name: str,
    is_private: bool = False,
    *,
    site_name: str | None = None,
    strategy: str | None = None,
    prefix: str | None = None,
) -> str:
    cfg = get_storage_config()
    folder = "private/files" if is_private else "public/files"
    effective_strategy = normalize_prefix_strategy(strategy or cfg["prefix_strategy"])
    site_prefix = prefix or get_storage_prefix(site_name=site_name, strategy=effective_strategy)

    # When using domain_hierarchy, strip the root-domain segment from the prefix
    # if it matches the bucket name (or bucket_prefix root), to avoid double
    # segments like devhq/devhq/b2b/agmc/... → devhq/b2b/agmc/...
    if effective_strategy == DOMAIN_HIERARCHY_STRATEGY:
        for to_match in [cfg.get("bucket"), cfg.get("bucket_prefix")]:
            if not to_match:
                continue
            if site_prefix == to_match:
                site_prefix = ""
                break
            if site_prefix.startswith(f"{to_match}/"):
                site_prefix = site_prefix[len(to_match) + 1 :]
                break

    if cfg["bucket_prefix"]:
        if not site_prefix:
            return f"{cfg['bucket_prefix']}/{folder}/{file_name}"
        return f"{cfg['bucket_prefix']}/{site_prefix}/{folder}/{file_name}"

    if not site_prefix:
        return f"{folder}/{file_name}"
    return f"{site_prefix}/{folder}/{file_name}"


def build_private_file_route(file_id: str, prefix_strategy: str | None = None) -> str:
    query = {"fid": file_id}
    strategy = normalize_prefix_strategy(prefix_strategy or get_storage_config()["prefix_strategy"])
    query["prefix_strategy"] = strategy
    return f"/api/method/raftor_storagehq.api.download_private_file?{urlencode(query)}"


def upload_file(
    *,
    content: bytes | Any,
    file_name: str,
    is_private: bool = False,
    content_type: str = "application/octet-stream",
) -> str:
    cfg = get_storage_config()
    key = build_object_key(file_name=file_name, is_private=is_private)

    get_storage_adapter().upload_object(
        bucket=cfg["bucket"],
        key=key,
        content=content,
        content_type=content_type,
        is_private=bool(is_private),
    )
    return key


def delete_file(key: str) -> None:
    cfg = get_storage_config()
    get_storage_adapter().delete_object(bucket=cfg["bucket"], key=key)


def copy_file(source_key: str, target_key: str, *, is_private: bool = False) -> None:
    cfg = get_storage_config()
    get_storage_adapter().copy_object(
        bucket=cfg["bucket"],
        source_key=source_key,
        target_key=target_key,
        is_private=bool(is_private),
    )


def get_object_metadata(key: str) -> dict | None:
    cfg = get_storage_config()
    return get_storage_adapter().get_object_metadata(bucket=cfg["bucket"], key=key)


def object_exists(key: str) -> bool:
    return get_object_metadata(key) is not None


def get_public_url(file_name: str) -> str:
    cfg = get_storage_config()
    key = build_object_key(file_name=file_name, is_private=False)
    return f"{cfg['public_base_url']}/{key}"


def get_public_url_for_strategy(file_name: str, strategy: str) -> str:
    cfg = get_storage_config()
    key = build_object_key(file_name=file_name, is_private=False, strategy=strategy)
    return f"{cfg['public_base_url']}/{key}"


def get_signed_url(file_name: str, expiry: int = 300, strategy: str | None = None) -> str:
    cfg = get_storage_config()
    key = build_object_key(file_name=file_name, is_private=True, strategy=strategy)

    return get_storage_adapter().generate_signed_download_url(
        bucket=cfg["bucket"],
        key=key,
        expiry=expiry,
    )


def download_file(key: str, local_path: str) -> None:
    """Download the object at *key* from the bucket to *local_path*."""
    cfg = get_storage_config()
    get_storage_adapter().download_object(
        bucket=cfg["bucket"],
        key=key,
        local_path=local_path,
    )


def list_backup_objects(prefix: str | None = None) -> list[dict]:
    """List all backup objects under the site's backup prefix in R2.

    Returns a list of dicts with keys: key, size, last_modified.
    """
    cfg = get_storage_config()
    adapter = get_storage_adapter()
    if not hasattr(adapter, "list_objects_with_prefix"):
        # Local disk or fallback without listing — return empty
        return []

    effective_prefix = prefix or _backup_key_prefix()
    return adapter.list_objects_with_prefix(bucket=cfg["bucket"], prefix=effective_prefix)


def _backup_key_prefix() -> str:
    """Return the object-key prefix for backups using canonical key generation."""
    key = build_object_key(file_name=".prefix", is_private=True)
    return key.rsplit("/", 1)[0].replace("/private/files", "/private/backups") + "/"


def get_backup_key_prefix() -> str:
    """Public accessor for the active backup key prefix."""
    return _backup_key_prefix()


def backup_key_for(filename: str) -> str:
    """Return the full R2 key for a backup file, using a datestamp sub-folder."""
    prefix = _backup_key_prefix()
    return f"{prefix}{filename}"

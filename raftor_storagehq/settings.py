from __future__ import annotations

import logging

import frappe

logger = logging.getLogger(__name__)


def get_storagehq_settings() -> dict | None:
    """Return StorageHQ Settings values if the DocType is available."""
    try:
        if not frappe.db.exists("DocType", "StorageHQ Settings"):
            return None
    except Exception:
        return None

    try:
        doc = frappe.get_single("StorageHQ Settings")
    except Exception:
        return None

    return {
        "enabled": bool(getattr(doc, "enabled", 0)),
        "provider": (getattr(doc, "provider", "") or "").strip().lower(),
        "bucket": (getattr(doc, "bucket", "") or "").strip(),
        "endpoint": (getattr(doc, "endpoint", "") or "").strip(),
        "access_key": (doc.get_password("access_key") or "").strip(),
        "secret_key": (doc.get_password("secret_key") or "").strip(),
        "region": (getattr(doc, "region", "") or "").strip() or None,
        "public_base_url": (getattr(doc, "public_base_url", "") or "").rstrip("/"),
        "prefix_strategy": (getattr(doc, "prefix_strategy", "site_name") or "site_name").strip().lower(),
        "private_url_expiry_seconds": int(getattr(doc, "private_url_expiry_seconds", 300) or 300),
        "allow_fallback_to_local": bool(getattr(doc, "allow_fallback_to_local", 1)),
        # Backup settings
        "backup_enabled": bool(getattr(doc, "backup_enabled", 0)),
        "backup_frequency": (getattr(doc, "backup_frequency", "None") or "None").strip(),
        "keep_backups_for_days": int(getattr(doc, "keep_backups_for_days", 7) or 7),
        "delete_local_backups_after_upload": bool(getattr(doc, "delete_local_backups_after_upload", 0)),
    }


def get_effective_storage_config() -> dict:
    """Return effective config for the current site.

    Precedence:
    - StorageHQ Settings (if DocType exists)
    """
    settings = get_storagehq_settings()
    if settings:
        return settings

    return {
        "enabled": False,
        "provider": "",
        "bucket": "",
        "endpoint": "",
        "access_key": "",
        "secret_key": "",
        "region": None,
        "public_base_url": "",
        "prefix_strategy": "site_name",
        "private_url_expiry_seconds": 300,
        "allow_fallback_to_local": True,
        "backup_enabled": False,
        "backup_frequency": "None",
        "keep_backups_for_days": 7,
        "delete_local_backups_after_upload": False,
    }

"""
raftor_storagehq.backups
~~~~~~~~~~~~~~~~~~~~~~~~
Automated backup upload to R2 and database restoration utilities.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile

import frappe
from frappe.utils.backups import new_backup

from raftor_storagehq.overrides import storage_client as r2_client
from raftor_storagehq.settings import get_effective_storage_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scheduler entry points
# ---------------------------------------------------------------------------


def daily_backup():
    _take_backup_if("Daily")


def weekly_backup():
    _take_backup_if("Weekly")


def monthly_backup():
    _take_backup_if("Monthly")


def _take_backup_if(freq: str):
    cfg = get_effective_storage_config()
    if not cfg.get("backup_enabled"):
        return
    if cfg.get("backup_frequency", "None") != freq:
        return
    if not r2_client.is_backup_ready():
        logger.warning("StorageHQ: backup not configured/ready — skipping backup upload.")
        return
    upload_latest_backups(force_new=True)


# ---------------------------------------------------------------------------
# Backup upload
# ---------------------------------------------------------------------------


@frappe.whitelist()
def upload_latest_backups(force_new: bool = False) -> dict:
    """
    Take a fresh Frappe backup and upload all generated files to R2.

    Returns a summary dict with the list of uploaded R2 keys.
    """
    frappe.only_for("System Manager")

    if not r2_client.is_backup_ready():
        frappe.throw(frappe._("Cloud backups are not enabled/configured on this site."))

    # 1. Trigger Frappe backup
    odb = new_backup(ignore_files=False, force=bool(force_new))

    files_to_upload = {
        "database": odb.backup_path_db,
        "config": odb.backup_path_conf,
        "public_files": odb.backup_path_files,
        "private_files": odb.backup_path_private_files,
    }

    uploaded = {}
    failed = {}

    cfg = get_effective_storage_config()
    delete_local = cfg.get("delete_local_backups_after_upload")

    for label, local_path in files_to_upload.items():
        if not local_path or not os.path.exists(local_path):
            continue
        filename = os.path.basename(local_path)
        key = r2_client.backup_key_for(filename)
        try:
            with open(local_path, "rb") as f:
                r2_client.get_storage_adapter().upload_object(
                    bucket=r2_client.get_storage_config()["bucket"],
                    key=key,
                    content=f,
                    content_type="application/octet-stream",
                    is_private=True,
                )
            uploaded[label] = {"local": local_path, "key": key}
            logger.info("Backup uploaded: %s -> %s", local_path, key)

            # Cleanup local file if requested
            if delete_local:
                try:
                    os.remove(local_path)
                    logger.info("Local backup removed after upload: %s", local_path)
                except Exception as cleanup_exc:
                    logger.warning("Failed to remove local backup %s: %s", local_path, cleanup_exc)

        except Exception as exc:
            logger.error("Backup upload failed for %s: %s", label, exc)
            failed[label] = str(exc)

    # Cleanup the metadata JSON file if it exists and delete_local is enabled
    if delete_local and hasattr(odb, "backup_path_db") and odb.backup_path_db:
        # Frappe usually creates a .json file with the same base name as the database backup
        json_path = odb.backup_path_db.replace(".sql.gz", ".json")
        if os.path.exists(json_path):
            try:
                os.remove(json_path)
                logger.info("Local backup metadata JSON removed: %s", json_path)
            except Exception as json_exc:
                logger.warning("Failed to remove local backup metadata %s: %s", json_path, json_exc)

    # Cleanup old backups from R2
    try:
        cleanup_old_backups()
    except Exception as exc:
        logger.warning("Backup cleanup failed: %s", exc)

    return {
        "site": frappe.local.site,
        "uploaded": uploaded,
        "failed": failed,
    }


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


@frappe.whitelist()
def list_backups() -> list[dict]:
    """
    List all backup files stored in R2 for this site, sorted newest-first.
    Each item includes: key, filename, size_mb, last_modified.
    """
    frappe.only_for("System Manager")

    if not r2_client.is_backup_ready():
        return []

    raw = r2_client.list_backup_objects()
    results = []
    for obj in raw:
        filename = obj["key"].split("/")[-1]
        results.append({
            "key": obj["key"],
            "filename": filename,
            "size_mb": round(obj["size"] / 1024 / 1024, 2),
            "last_modified": obj["last_modified"],
            # Identify file type from the filename convention
            "type": _classify_backup_file(filename),
        })

    results.sort(key=lambda x: x["last_modified"], reverse=True)
    return results


@frappe.whitelist()
def get_backup_download_url(backup_key: str, expiry: int | None = None) -> dict:
    """Return a short-lived signed download URL for a backup object."""
    frappe.only_for("System Manager")

    if not r2_client.is_backup_ready():
        frappe.throw(frappe._("Cloud backups are not enabled/configured on this site."))

    _assert_backup_key_in_active_prefix(backup_key)

    cfg = r2_client.get_storage_config()
    ttl = int(expiry or cfg.get("private_url_expiry_seconds") or 300)
    url = r2_client.get_storage_adapter().generate_signed_download_url(
        bucket=cfg["bucket"],
        key=backup_key,
        expiry=ttl,
    )
    return {
        "url": url,
        "filename": backup_key.split("/")[-1],
        "expiry_seconds": ttl,
    }


def _classify_backup_file(filename: str) -> str:
    if "-database" in filename or filename.endswith(".sql.gz"):
        return "database"
    if "-private-files" in filename:
        return "private_files"
    if "-files" in filename:
        return "public_files"
    if "site_config_backup" in filename:
        return "config"
    return "other"


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


@frappe.whitelist()
def restore_backup(backup_key: str) -> dict:
    """
    Download a database backup from R2 and restore it to the current site.

    Steps:
      1. Download the .sql.gz from R2 to a temp directory.
      2. Decompress the SQL and pipe it through the DB restore command.
      3. Return a status report.

    ⚠ This replaces the current database. The caller must confirm this action.
    """
    frappe.only_for("System Manager")

    if not r2_client.is_backup_ready():
        frappe.throw(frappe._("Cloud backups are not enabled/configured on this site."))

    _assert_backup_key_in_active_prefix(backup_key)

    filename = backup_key.split("/")[-1]
    if not filename.endswith(".sql.gz") and "-database" not in filename:
        frappe.throw(frappe._("Only database backup files (.sql.gz) can be restored."))

    tmp_dir = tempfile.mkdtemp(prefix="storagehq_restore_")
    local_path = os.path.join(tmp_dir, filename)

    try:
        # Step 1: Download
        frappe.publish_realtime(
            "storagehq_restore_progress",
            {"status": "downloading", "key": backup_key},
            user=frappe.session.user,
        )
        r2_client.download_file(backup_key, local_path)
        logger.info("Restore: downloaded %s -> %s", backup_key, local_path)

        # Step 2: Restore
        frappe.publish_realtime(
            "storagehq_restore_progress",
            {"status": "restoring", "key": backup_key},
            user=frappe.session.user,
        )
        _restore_database(local_path)
        logger.info("Restore: database restored from %s", local_path)

        frappe.publish_realtime(
            "storagehq_restore_progress",
            {"status": "done", "key": backup_key},
            user=frappe.session.user,
        )
        return {
            "status": "success",
            "key": backup_key,
            "restored_from": filename,
        }

    except Exception as exc:
        logger.error("Restore failed for %s: %s", backup_key, exc)
        frappe.publish_realtime(
            "storagehq_restore_progress",
            {"status": "error", "key": backup_key, "error": str(exc)},
            user=frappe.session.user,
        )
        frappe.throw(frappe._("Restore failed: {0}").format(str(exc)))

    finally:
        # Clean up temp files
        try:
            if os.path.exists(local_path):
                os.remove(local_path)
            os.rmdir(tmp_dir)
        except Exception:
            pass


def _assert_backup_key_in_active_prefix(backup_key: str) -> None:
    prefix = r2_client.get_backup_key_prefix()
    if not backup_key.startswith(prefix):
        frappe.throw(
            frappe._("Backup key does not belong to the active domain_hierarchy prefix.")
        )


def _restore_database(sql_gz_path: str):
    """Decompress and restore the .sql.gz dump into the site's database."""
    from frappe.database import get_command

    conf = frappe.conf
    bin_path, args, bin_name = get_command(
        socket=conf.get("db_socket"),
        host=conf.get("db_host"),
        port=conf.get("db_port"),
        user=conf.db_name,
        password=conf.db_password,
        db_name=conf.db_name,
        dump=False,   # restore mode
    )

    if not bin_path:
        raise RuntimeError(f"{bin_name} not found in PATH. Cannot restore database.")

    import shlex

    restore_cmd = (
        f"set -o pipefail; "
        f"gunzip -c {shlex.quote(sql_gz_path)} | "
        f"{bin_path} {' '.join(shlex.quote(a) for a in args)}"
    )
    result = subprocess.run(
        restore_cmd,
        shell=True,
        executable="/bin/bash",
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"DB restore command failed:\n{result.stderr}")


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def cleanup_old_backups() -> dict:
    """
    Delete backup objects from R2 that are older than `keep_backups_for_days`.
    """
    from datetime import datetime, timezone, timedelta

    cfg = get_effective_storage_config()
    keep_days = int(cfg.get("keep_backups_for_days") or 7)
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)

    objects = r2_client.list_backup_objects()
    deleted = []
    skipped = []

    for obj in objects:
        # last_modified is an ISO string from boto3
        try:
            modified = datetime.fromisoformat(obj["last_modified"])
            if modified.tzinfo is None:
                modified = modified.replace(tzinfo=timezone.utc)
        except Exception:
            skipped.append(obj["key"])
            continue

        if modified < cutoff:
            try:
                r2_client.delete_file(obj["key"])
                deleted.append(obj["key"])
                logger.info("Cleanup: deleted old backup %s", obj["key"])
            except Exception as exc:
                logger.warning("Cleanup: failed to delete %s: %s", obj["key"], exc)
                skipped.append(obj["key"])

    return {"deleted": deleted, "skipped": skipped, "cutoff": cutoff.isoformat()}

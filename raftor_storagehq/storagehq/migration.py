from __future__ import annotations

import mimetypes
import os

import frappe
from frappe import _

from raftor_storagehq.overrides import storage_client


@frappe.whitelist()
def migrate_local_files_to_cloud() -> dict:
    """Migrate File records pointing to local paths into cloud object storage."""
    frappe.only_for("System Manager")

    if not storage_client.is_cloud_storage_enabled():
        frappe.throw(
            _("Cloud storage is not enabled. Please configure and enable StorageHQ first.")
        )

    all_files = _get_local_file_records()
    if not all_files:
        return {
            "status": "success",
            "message": _("No local files found for migration."),
            "migrated_count": 0,
            "skipped_count": 0,
            "error_count": 0,
            "errors": [],
        }

    migrated_count = 0
    skipped_count = 0
    errors: list[str] = []
    commit_every = 20

    for file_meta in all_files:
        try:
            disk_name = _resolve_disk_filename(file_meta)
            local_path = _resolve_local_path(file_meta, disk_name)
            if not local_path:
                skipped_count += 1
                continue

            with open(local_path, "rb") as file_obj:
                content = file_obj.read()

            content_type = mimetypes.guess_type(disk_name)[0]
            storage_client.upload_file(
                content=content,
                file_name=disk_name,
                is_private=bool(file_meta.is_private),
                content_type=content_type or "application/octet-stream",
            )

            file_url = (
                storage_client.build_private_file_route(file_meta.name)
                if file_meta.is_private
                else storage_client.get_public_url(disk_name)
            )
            frappe.db.set_value("File", file_meta.name, "file_url", file_url, update_modified=False)

            migrated_count += 1
            if migrated_count % commit_every == 0:
                frappe.db.commit()
        except Exception as exc:
            err_msg = f"Error migrating {file_meta.name}: {exc}"
            errors.append(err_msg)
            frappe.log_error(title="StorageHQ File Migration", message=err_msg)

    frappe.db.commit()

    message = _("Migrated {0} files to cloud storage.").format(migrated_count)
    if skipped_count:
        message += " " + _("Skipped {0} files (not found on disk).").format(skipped_count)
    if errors:
        message += " " + _("Encountered {0} errors.").format(len(errors))

    return {
        "status": "success" if not errors else "partial_success",
        "message": message,
        "migrated_count": migrated_count,
        "skipped_count": skipped_count,
        "error_count": len(errors),
        "errors": errors[:10],
    }


def _get_local_file_records() -> list[frappe._dict]:
    public_files = frappe.get_all(
        "File",
        filters={"is_folder": 0, "file_url": ["like", "/files/%"]},
        fields=["name", "file_name", "file_url", "is_private"],
    )
    private_files = frappe.get_all(
        "File",
        filters={"is_folder": 0, "file_url": ["like", "/private/files/%"]},
        fields=["name", "file_name", "file_url", "is_private"],
    )
    return list(public_files) + list(private_files)


def _resolve_disk_filename(file_meta: frappe._dict) -> str:
    file_url = (file_meta.file_url or "").strip()
    from_url = os.path.basename(file_url)
    return from_url or (file_meta.file_name or "").strip()


def _resolve_local_path(file_meta: frappe._dict, disk_name: str) -> str | None:
    if not disk_name:
        return None

    if bool(file_meta.is_private):
        path = frappe.get_site_path("private", "files", disk_name)
    else:
        path = frappe.get_site_path("public", "files", disk_name)
    return path if os.path.exists(path) else None

"""
raftor_storagehq.api
~~~~~~~~~~~~~~~~~~~~~~
Helpers for generating public or signed URLs for files stored in R2.
"""

import frappe
from frappe import _

from raftor_storagehq.overrides import storage_client as r2_client


@frappe.whitelist(allow_guest=False)
def download_private_file(
    fid: str | None = None,
    file_name: str | None = None,
    expiry: int | None = None,
    prefix_strategy: str | None = None,
):
    """
    Redirect an authorized user to a short-lived signed R2 URL for a private
    file. This avoids relying on Frappe's local /private/files route.
    """
    if not r2_client.is_cloud_storage_enabled():
        frappe.throw(_("R2 storage is not enabled on this site."))

    if fid:
        doc = frappe.get_doc("File", fid)
    elif file_name:
        doc = frappe.get_doc("File", {"file_name": file_name, "is_private": 1})
    else:
        frappe.throw(_("Either fid or file_name is required."))

    if not doc.is_downloadable():
        raise frappe.PermissionError

    if expiry is None:
        expiry = int(r2_client.get_storage_config().get("private_url_expiry_seconds") or 300)

    signed_url = r2_client.get_signed_url(
        doc.file_name,
        expiry=int(expiry),
        strategy=prefix_strategy,
    )
    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = signed_url


@frappe.whitelist(allow_guest=False)
def get_r2_file_url(file_name: str, expiry: int | None = None, prefix_strategy: str | None = None) -> dict:
    """
    Return a signed URL (private files) or CDN URL (public files) for the
    given *file_name*.
    """
    if not r2_client.is_cloud_storage_enabled():
        frappe.throw(_("R2 storage is not enabled on this site."))

    doc = frappe.get_doc("File", {"file_name": file_name})
    if not doc:
        frappe.throw(_("File not found: {0}").format(file_name))

    if doc.is_private:
        if expiry is None:
            expiry = int(r2_client.get_storage_config().get("private_url_expiry_seconds") or 300)
        url = r2_client.get_signed_url(
            file_name,
            expiry=int(expiry),
            strategy=prefix_strategy,
        )
    else:
        strategy = r2_client.normalize_prefix_strategy(
            prefix_strategy or r2_client.get_storage_config()["prefix_strategy"]
        )
        url = r2_client.get_public_url_for_strategy(file_name, strategy)

    return {"url": url, "is_private": bool(doc.is_private)}


@frappe.whitelist(allow_guest=False)
def r2_storage_status() -> dict:
    """
    Health-check endpoint – returns R2 connectivity status.
    Useful for verifying the configuration without uploading a real file.
    """
    if not r2_client.is_cloud_storage_enabled():
        return {"enabled": False, "status": "R2 not configured"}

    try:
        cfg = r2_client.get_storage_config()
        r2_client.get_storage_adapter().check_connectivity(bucket=cfg["bucket"])
        return {
            "enabled": True,
            "status": "ok",
            "bucket": cfg["bucket"],
            "provider": cfg.get("provider", "r2"),
        }
    except Exception as exc:
        return {"enabled": True, "status": "error", "detail": str(exc)}


def e2e_smoke_test() -> dict:
    """
    Bench-executable smoke test for the main File flows:
    public upload, private upload, URL persistence, and delete.
    """
    results = {}
    created = []

    try:
        public_file = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": "r2_e2e_public.txt",
                "content": "public test content",
                "is_private": 0,
                "folder": "Home/Attachments",
            }
        )
        public_file.save(ignore_permissions=True)
        created.append(public_file.name)
        results["public_create"] = {
            "name": public_file.name,
            "file_url": public_file.file_url,
            "is_private": public_file.is_private,
        }

        private_file = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": "r2_e2e_private.txt",
                "content": "private test content",
                "is_private": 1,
                "folder": "Home/Attachments",
            }
        )
        private_file.save(ignore_permissions=True)
        created.append(private_file.name)
        results["private_create"] = {
            "name": private_file.name,
            "file_url": private_file.file_url,
            "is_private": private_file.is_private,
        }

        results["public_reload"] = {
            "file_url": frappe.get_doc("File", public_file.name).file_url,
        }
        results["private_reload"] = {
            "file_url": frappe.get_doc("File", private_file.name).file_url,
        }
    finally:
        cleanup = {}
        for name in created:
            try:
                doc = frappe.get_doc("File", name)
                cleanup[name] = {
                    "file_name": doc.file_name,
                    "is_private": doc.is_private,
                }
                frappe.delete_doc("File", name, ignore_permissions=True)
                cleanup[name]["deleted"] = True
            except Exception as exc:
                cleanup[name] = {"deleted": False, "error": str(exc)}

        results["cleanup"] = cleanup

    return results

from __future__ import annotations

import frappe


def _ensure_module_def() -> None:
    if frappe.db.exists("Module Def", "StorageHQ"):
        return
    doc = frappe.get_doc(
        {
            "doctype": "Module Def",
            "module_name": "StorageHQ",
            "custom": 0,
            "app_name": "raftor_storagehq",
        }
    )
    doc.insert(ignore_permissions=True)
    frappe.db.commit()


def _reload_settings_doctype() -> None:
    try:
        frappe.reload_doc("StorageHQ", "doctype", "storagehq_settings")
    except Exception:
        # Module path registration may not be ready during fresh install; safe to skip.
        pass


def after_install() -> None:
    _ensure_module_def()
    _reload_settings_doctype()


def after_migrate() -> None:
    _ensure_module_def()
    _reload_settings_doctype()

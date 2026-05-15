app_name = "raftor_storagehq"
app_title = "Raftor StorageHQ"
app_publisher = "Raftor Technologies"
app_description = "Replace Frappe/ERPNext local file storage with Cloudflare R2 (S3-compatible)"
app_email = ""
app_license = "MIT"

# ---------------------------------------------------------------------------
# Override the built-in File doctype with our R2-aware subclass.
# ---------------------------------------------------------------------------
override_doctype_class = {
    "File": "raftor_storagehq.overrides.storage_file.StorageFile"
}

# ---------------------------------------------------------------------------
# Frontend bundle — required so bench build resolves a valid public/ path.
# ---------------------------------------------------------------------------
app_include_js = "/assets/raftor_storagehq/js/raftor_storagehq.bundle.js"

# ---------------------------------------------------------------------------
# Expose whitelisted API methods so Frappe's router can find them.
# ---------------------------------------------------------------------------
override_whitelisted_methods = {
    "raftor_storagehq.api.download_private_file": "raftor_storagehq.api.download_private_file",
    "raftor_storagehq.api.get_r2_file_url": "raftor_storagehq.api.get_r2_file_url",
    "raftor_storagehq.api.r2_storage_status": "raftor_storagehq.api.r2_storage_status",
}

after_install = "raftor_storagehq.install.after_install"
after_migrate = "raftor_storagehq.install.after_migrate"

scheduler_events = {
    "daily_maintenance": [
        "raftor_storagehq.backups.daily_backup",
    ],
    "weekly_long": [
        "raftor_storagehq.backups.weekly_backup",
    ],
    "monthly_long": [
        "raftor_storagehq.backups.monthly_backup",
    ],
}

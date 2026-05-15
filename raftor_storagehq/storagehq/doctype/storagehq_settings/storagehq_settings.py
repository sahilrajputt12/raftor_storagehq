import frappe
from frappe.model.document import Document
from frappe import _


class StorageHQSettings(Document):
    def validate(self):
        storage_enabled = bool(getattr(self, "enabled", 0))
        backup_enabled = bool(getattr(self, "backup_enabled", 0))

        if not (storage_enabled or backup_enabled):
            return

        provider = (getattr(self, "provider", "") or "").strip().lower()
        if provider not in {"r2", "s3", "minio"}:
            frappe.throw(_("Please select a Storage Provider (r2/s3/minio) to enable StorageHQ/Backups."))

        required_fields = {
            "bucket": (getattr(self, "bucket", "") or "").strip(),
            "endpoint": (getattr(self, "endpoint", "") or "").strip(),
            "access_key": (self.get_password("access_key") or "").strip(),
            "secret_key": (self.get_password("secret_key") or "").strip(),
        }

        missing = [label for label, value in {
            _("Bucket"): required_fields["bucket"],
            _("Endpoint"): required_fields["endpoint"],
            _("Access Key"): required_fields["access_key"],
            _("Secret Key"): required_fields["secret_key"],
        }.items() if not value]

        if missing:
            frappe.throw(_("Missing required cloud credentials: {0}").format(", ".join(missing)))

    @frappe.whitelist()
    def migrate_local_files(self):
        frappe.only_for("System Manager")
        from raftor_storagehq.storagehq.migration import migrate_local_files_to_cloud
        return migrate_local_files_to_cloud()

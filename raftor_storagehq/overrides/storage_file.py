from __future__ import annotations

import logging
import mimetypes
import os

import frappe
from frappe import _
from frappe.core.doctype.file.file import File

from raftor_storagehq.overrides import storage_client

logger = logging.getLogger(__name__)


class StorageFile(File):
    @frappe.whitelist()
    def optimize_file(self):
        # Frappe's default optimizer reads from local filesystem.
        # For cloud-backed public URLs this raises FileNotFoundError.
        if storage_client.is_cloud_storage_enabled():
            frappe.throw(
                _(
                    "Image optimization is not supported for cloud-backed files. "
                    "Please optimize locally before upload."
                )
            )

        return super().optimize_file()

    def save_file(
        self,
        content=None,
        decode=False,
        ignore_existing_file_check=False,
        overwrite=False,
    ):
        if not storage_client.is_cloud_storage_enabled():
            return super().save_file(
                content=content,
                decode=decode,
                ignore_existing_file_check=ignore_existing_file_check,
                overwrite=overwrite,
            )

        if content is None:
            return super().save_file(
                content=content,
                decode=decode,
                ignore_existing_file_check=ignore_existing_file_check,
                overwrite=overwrite,
            )

        if decode:
            import base64

            if isinstance(content, str):
                content = content.encode("utf-8")
            content = base64.b64decode(content)

        if isinstance(content, str):
            content = content.encode("utf-8")

        file_name = self._get_clean_file_name()

        content_type, _ = mimetypes.guess_type(file_name)
        content_type = content_type or "application/octet-stream"

        storage_client.upload_file(
            content=content,
            file_name=file_name,
            is_private=bool(self.is_private),
            content_type=content_type,
        )

        self.file_name = file_name
        if self.is_private:
            # URL will be set to the signed-redirect route in after_insert once
            # the record has a name. Use a placeholder that validates_file_on_disk
            # will not reject, consistent with what Frappe core does.
            self.file_url = f"/private/files/{file_name}"
        else:
            self.file_url = storage_client.get_public_url(file_name)

        if isinstance(content, (bytes, str)):
            self.file_size = len(content)
            self.content_hash = self._compute_hash(content if isinstance(content, bytes) else content.encode())
        else:
            # For streams, we might not know the size or hash easily without reading
            # but since save_file is usually called with content for small/medium files
            # we'll try to get size from the stream if possible.
            try:
                content.seek(0, os.SEEK_END)
                self.file_size = content.tell()
                content.seek(0)
            except Exception:
                self.file_size = 0
            self.content_hash = None


        self._storage_key = storage_client.build_object_key(
            file_name=file_name,
            is_private=bool(self.is_private),
        )

        logger.info(
            "File '%s' stored in object storage key=%s private=%s",
            file_name,
            self._storage_key,
            bool(self.is_private),
        )

    def after_insert(self):
        if not self.is_folder and storage_client.is_cloud_storage_enabled() and self.is_private:
            self.file_url = storage_client.build_private_file_route(self.name)
            self.db_set("file_url", self.file_url, update_modified=False)

        if not self.is_folder:
            self.create_attachment_record()

    def validate_file_on_disk(self):
        if self._is_storage_backed_file():
            return True
        return super().validate_file_on_disk()

    def exists_on_disk(self):
        if self._is_storage_backed_file():
            return True
        return super().exists_on_disk()

    def download_file(self):
        if not storage_client.is_cloud_storage_enabled():
            return super().download_file()

        file_name = self.file_name or os.path.basename(self.file_url or "")
        if not file_name:
            frappe.throw(_("Cannot determine file name for download."))

        try:
            if self.is_private:
                expiry = int(storage_client.get_storage_config().get("private_url_expiry_seconds") or 300)
                redirect_url = storage_client.get_signed_url(file_name, expiry=expiry)
            else:
                redirect_url = storage_client.get_public_url(file_name)
        except Exception as exc:
            logger.error("Storage URL generation failed for %s: %s", file_name, exc)
            frappe.throw(_("File temporarily unavailable. Please try again later."))

        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = redirect_url

    def after_delete(self):
        if not storage_client.is_cloud_storage_enabled():
            return

        file_name = self.file_name or os.path.basename(self.file_url or "")
        if not file_name:
            return

        key = storage_client.build_object_key(file_name=file_name, is_private=bool(self.is_private))
        try:
            storage_client.delete_file(key)
        except Exception as exc:
            logger.error(
                "Storage delete failed for key=%s error=%s (object may need manual cleanup)",
                key,
                exc,
            )

    def _is_storage_backed_file(self) -> bool:
        return bool(
            storage_client.is_cloud_storage_enabled()
            and self.file_url
            and (
                self.file_url.startswith(("/files/", "/private/files/"))
                or self.file_url.startswith("/api/method/raftor_storagehq.api.download_private_file")
            )
        )

    def _get_clean_file_name(self) -> str:
        from frappe.utils.file_manager import get_file_name

        if self.file_name:
            return (
                get_file_name(self.file_name, self.content_hash)
                if self.content_hash
                else self.file_name
            )

        return get_file_name(self.file_name or "upload", None)

    @staticmethod
    def _compute_hash(content: bytes) -> str:
        import hashlib

        return hashlib.md5(content, usedforsecurity=False).hexdigest()

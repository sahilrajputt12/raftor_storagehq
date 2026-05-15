from __future__ import annotations

import os

import frappe

from raftor_storagehq.storage.base import StorageAdapter


class LocalDiskStorageAdapter(StorageAdapter):
    """Local filesystem adapter.

    This adapter exists to make local disk a first-class provider behind the
    same StorageAdapter interface used for cloud storage.

    Note: The current app's R2 integration still relies on Frappe core to
    generate thumbnails, manage File records, and enforce permissions.
    """

    def __init__(self, config: dict):
        self.config = config

    def _abs_path_for_key(self, key: str) -> str:
        # Key format: <prefix>/<public|private>/<relative_path>
        # prefix may contain slashes (domain_hierarchy strategy), so locate the
        # public/private marker anywhere in the key.
        parts = [p for p in (key or "").split("/") if p]
        for i, part in enumerate(parts):
            if part in {"public", "private"}:
                relative_path = "/".join(parts[i + 1 :])
                if not relative_path:
                    raise ValueError(f"Invalid local key (no filename): {key}")

                base = frappe.get_site_path(part, "files")
                return os.path.join(base, relative_path)

        raise ValueError(f"Invalid local key (no public/private marker): {key}")

    def upload_object(
        self,
        *,
        bucket: str,
        key: str,
        content: bytes | Any,
        content_type: str,
        is_private: bool,
    ) -> None:
        path = self._abs_path_for_key(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            if isinstance(content, bytes):
                f.write(content)
            else:
                import shutil
                shutil.copyfileobj(content, f)

    def delete_object(self, *, bucket: str, key: str) -> None:
        path = self._abs_path_for_key(key)
        try:
            os.remove(path)
        except FileNotFoundError:
            return

    def copy_object(
        self,
        *,
        bucket: str,
        source_key: str,
        target_key: str,
        is_private: bool,
    ) -> None:
        source_path = self._abs_path_for_key(source_key)
        target_path = self._abs_path_for_key(target_key)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(source_path, "rb") as src:
            data = src.read()
        with open(target_path, "wb") as dst:
            dst.write(data)

    def get_object_metadata(self, *, bucket: str, key: str) -> dict | None:
        path = self._abs_path_for_key(key)
        try:
            stat = os.stat(path)
            return {
                "content_length": stat.st_size,
                "mtime": stat.st_mtime,
            }
        except FileNotFoundError:
            return None

    def generate_signed_download_url(self, *, bucket: str, key: str, expiry: int) -> str:
        # No signed URLs locally. The app should use native Frappe routes.
        raise NotImplementedError("Signed URLs are not applicable for local storage.")

    def check_connectivity(self, *, bucket: str) -> None:
        # Validate that we can write into both folders.
        for folder in ("public", "private"):
            base = frappe.get_site_path("public", "files") if folder == "public" else frappe.get_site_path("private", "files")
            os.makedirs(base, exist_ok=True)
            test_path = os.path.join(base, ".storagehq_write_test")
            with open(test_path, "wb") as f:
                f.write(b"ok")
            try:
                os.remove(test_path)
            except Exception:
                pass

    def download_object(self, *, bucket: str, key: str, local_path: str) -> None:
        src_path = self._abs_path_for_key(key)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(src_path, "rb") as src, open(local_path, "wb") as dst:
            dst.write(src.read())

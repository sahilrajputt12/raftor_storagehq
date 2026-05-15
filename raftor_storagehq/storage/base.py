from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class StorageAdapter(ABC):
    """
    Unified storage adapter interface.

    Provider implementations (R2/S3/GCS/...) must implement these object-level
    operations so the app can remain provider-agnostic.
    """

    @abstractmethod
    def upload_object(
        self,
        *,
        bucket: str,
        key: str,
        content: bytes | Any,
        content_type: str,
        is_private: bool,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete_object(self, *, bucket: str, key: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def copy_object(
        self,
        *,
        bucket: str,
        source_key: str,
        target_key: str,
        is_private: bool,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_object_metadata(self, *, bucket: str, key: str) -> dict | None:
        raise NotImplementedError

    @abstractmethod
    def generate_signed_download_url(
        self,
        *,
        bucket: str,
        key: str,
        expiry: int,
    ) -> str:
        raise NotImplementedError

    @abstractmethod
    def check_connectivity(self, *, bucket: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def download_object(self, *, bucket: str, key: str, local_path: str) -> None:
        """Download object at *key* and write it to *local_path*."""
        raise NotImplementedError

    def get_native_client(self) -> Any:
        """
        Optional provider-native client accessor.
        Kept for backward compatibility where old code needs direct client use.
        """
        return None

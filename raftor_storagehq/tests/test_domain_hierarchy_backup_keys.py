from __future__ import annotations

import unittest
from unittest.mock import patch

from raftor_storagehq.overrides import storage_client


class TestDomainHierarchyBackupKeys(unittest.TestCase):
    def test_backup_prefix_matches_file_key_hierarchy(self):
        with (
            patch.object(storage_client, "get_site_name", return_value="agmc.b2b.devhq.in"),
            patch.object(
                storage_client,
                "get_storage_config",
                return_value={
                    "enabled": True,
                    "cloud_enabled": True,
                    "provider": "r2",
                    "bucket": "devhq",
                    "bucket_prefix": "",
                    "endpoint": "https://example.invalid",
                    "access_key": "x",
                    "secret_key": "y",
                    "region": None,
                    "public_base_url": "https://cdn.example.com",
                    "prefix_strategy": storage_client.DOMAIN_HIERARCHY_STRATEGY,
                    "private_url_expiry_seconds": 300,
                    "allow_fallback_to_local": True,
                },
            ),
        ):
            file_key = storage_client.build_object_key("doc.sql.gz", is_private=True)
            backup_prefix = storage_client.get_backup_key_prefix()
            backup_key = storage_client.backup_key_for("doc.sql.gz")

            self.assertEqual(file_key, "b2b/agmc/private/files/doc.sql.gz")
            self.assertEqual(backup_prefix, "b2b/agmc/private/backups/")
            self.assertEqual(backup_key, "b2b/agmc/private/backups/doc.sql.gz")

    def test_backup_prefix_respects_bucket_prefix(self):
        with (
            patch.object(storage_client, "get_site_name", return_value="agmc.b2b.markethq.in"),
            patch.object(
                storage_client,
                "get_storage_config",
                return_value={
                    "enabled": True,
                    "cloud_enabled": True,
                    "provider": "r2",
                    "bucket": "storage",
                    "bucket_prefix": "markethq",
                    "endpoint": "https://example.invalid",
                    "access_key": "x",
                    "secret_key": "y",
                    "region": None,
                    "public_base_url": "https://cdn.example.com",
                    "prefix_strategy": storage_client.DOMAIN_HIERARCHY_STRATEGY,
                    "private_url_expiry_seconds": 300,
                    "allow_fallback_to_local": True,
                },
            ),
        ):
            self.assertEqual(
                storage_client.get_backup_key_prefix(),
                "markethq/b2b/agmc/private/backups/",
            )

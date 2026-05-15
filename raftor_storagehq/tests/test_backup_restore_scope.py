from __future__ import annotations

import unittest
from unittest.mock import patch

import frappe

from raftor_storagehq import backups


class TestBackupRestoreScope(unittest.TestCase):
    def setUp(self):
        if not hasattr(frappe.local, "flags"):
            frappe.local.flags = frappe._dict()
        frappe.local.flags.in_test = True

    def test_restore_rejects_legacy_or_foreign_prefix(self):
        with (
            patch.object(backups.r2_client, "is_backup_ready", return_value=True),
            patch.object(
                backups.r2_client,
                "get_backup_key_prefix",
                return_value="new/prefix/private/backups/",
            ),
            patch.object(backups.frappe, "only_for", return_value=None),
            patch.object(backups.frappe, "_", side_effect=lambda message: message),
            patch.object(
                backups.frappe,
                "throw",
                side_effect=lambda message: (_ for _ in ()).throw(RuntimeError(str(message))),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "active domain_hierarchy prefix"):
                backups.restore_backup("old/prefix/private/backups/20260512-example-database.sql.gz")

    def test_restore_rejects_non_database_backup_file(self):
        with (
            patch.object(backups.r2_client, "is_backup_ready", return_value=True),
            patch.object(
                backups.r2_client,
                "get_backup_key_prefix",
                return_value="new/prefix/private/backups/",
            ),
            patch.object(backups.frappe, "only_for", return_value=None),
            patch.object(backups.frappe, "_", side_effect=lambda message: message),
            patch.object(
                backups.frappe,
                "throw",
                side_effect=lambda message: (_ for _ in ()).throw(RuntimeError(str(message))),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "Only database backup files"):
                backups.restore_backup("new/prefix/private/backups/20260512-example-private-files.tar")

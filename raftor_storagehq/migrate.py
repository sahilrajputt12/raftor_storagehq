"""
raftor_storagehq.migrate
~~~~~~~~~~~~~~~~~~~~~~~~
Site-scoped migration utilities for moving existing R2 objects and File
records from one prefix strategy to another.
"""

from __future__ import annotations

import mimetypes
import os

import frappe

from raftor_storagehq.overrides import storage_client
from raftor_storagehq.storage.providers.s3_compatible import S3CompatibleStorageAdapter


def preview_prefix_migration(
    target_strategy: str = storage_client.DOMAIN_HIERARCHY_STRATEGY,
    source_strategy: str | None = None,
    limit: int | None = None,
    file_names: list[str] | str | None = None,
) -> dict:
    """
    Dry-run preview of a prefix-strategy migration for the current site.
    """
    plan = _build_migration_plan(
        target_strategy=target_strategy,
        source_strategy=source_strategy,
        limit=limit,
        file_names=file_names,
    )
    plan["dry_run"] = True
    return plan


def migrate_existing_files(
    target_strategy: str = storage_client.DOMAIN_HIERARCHY_STRATEGY,
    source_strategy: str | None = None,
    limit: int | None = None,
    dry_run: bool = True,
    delete_old: bool = False,
    file_names: list[str] | str | None = None,
) -> dict:
    """
    Migrate existing File records for the current site from one prefix
    strategy to another.
    """
    if dry_run:
        return preview_prefix_migration(
            target_strategy=target_strategy,
            source_strategy=source_strategy,
            limit=limit,
            file_names=file_names,
        )

    plan = _build_migration_plan(
        target_strategy=target_strategy,
        source_strategy=source_strategy,
        limit=limit,
        file_names=file_names,
    )

    results = {
        "site": frappe.local.site,
        "source_strategy": plan["source_strategy"],
        "target_strategy": plan["target_strategy"],
        "dry_run": False,
        "delete_old": bool(delete_old),
        "summary": {
            "examined": plan["summary"]["examined"],
            "eligible": 0,
            "migrated": 0,
            "skipped": 0,
            "failed": 0,
            "deleted_old": 0,
        },
        "details": [],
    }

    for item in plan["details"]:
        outcome = {
            "file": item["file"],
            "file_name": item["file_name"],
            "is_private": item["is_private"],
            "old_key": item["old_key"],
            "new_key": item["new_key"],
            "old_exists": item["old_exists"],
            "new_exists": item["new_exists"],
            "current_url": item["current_url"],
            "desired_url": item["desired_url"],
        }

        if item["status"] in {"no_change", "skipped_unmanaged"}:
            outcome["status"] = "skipped"
            outcome["reason"] = item["status"]
            results["summary"]["skipped"] += 1
            results["details"].append(outcome)
            continue

        if item["status"] == "collision":
            outcome["status"] = "failed"
            outcome["reason"] = "target_exists_with_different_metadata"
            results["summary"]["failed"] += 1
            results["details"].append(outcome)
            continue

        if item["status"] == "missing_source":
            outcome["status"] = "failed"
            outcome["reason"] = "source_object_missing"
            results["summary"]["failed"] += 1
            results["details"].append(outcome)
            continue

        results["summary"]["eligible"] += 1
        savepoint = f"raftor_storagehq_migrate_{item['file']}"
        frappe.db.savepoint(savepoint)

        try:
            if item["old_key"] != item["new_key"] and not item["new_exists"]:
                storage_client.copy_file(
                    item["old_key"],
                    item["new_key"],
                    is_private=bool(item["is_private"]),
                )

            if item["old_key"] != item["new_key"] and not storage_client.object_exists(item["new_key"]):
                raise RuntimeError(f"New object missing after copy: {item['new_key']}")

            frappe.db.set_value(
                "File",
                item["file"],
                "file_url",
                item["desired_url"],
                update_modified=False,
            )
            frappe.db.commit()

            outcome["status"] = "migrated"
            results["summary"]["migrated"] += 1

            if delete_old and item["old_key"] != item["new_key"] and item["old_exists"]:
                storage_client.delete_file(item["old_key"])
                outcome["deleted_old"] = True
                results["summary"]["deleted_old"] += 1
            else:
                outcome["deleted_old"] = False

            results["details"].append(outcome)
        except Exception as exc:
            frappe.db.rollback(save_point=savepoint)
            outcome["status"] = "failed"
            outcome["reason"] = str(exc)
            results["summary"]["failed"] += 1
            results["details"].append(outcome)

    return results


def cleanup_old_keys(
    target_strategy: str = storage_client.DOMAIN_HIERARCHY_STRATEGY,
    source_strategy: str | None = None,
    limit: int | None = None,
    dry_run: bool = True,
    file_names: list[str] | str | None = None,
) -> dict:
    """
    Delete old objects after verifying the new keys exist and File URLs already
    point to the target strategy.
    """
    plan = _build_migration_plan(
        target_strategy=target_strategy,
        source_strategy=source_strategy,
        limit=limit,
        file_names=file_names,
    )

    results = {
        "site": frappe.local.site,
        "source_strategy": plan["source_strategy"],
        "target_strategy": plan["target_strategy"],
        "dry_run": bool(dry_run),
        "summary": {
            "examined": plan["summary"]["examined"],
            "eligible": 0,
            "deleted": 0,
            "skipped": 0,
            "failed": 0,
        },
        "details": [],
    }

    for item in plan["details"]:
        outcome = {
            "file": item["file"],
            "file_name": item["file_name"],
            "old_key": item["old_key"],
            "new_key": item["new_key"],
        }

        if (
            item["old_key"] == item["new_key"]
            or not item["old_exists"]
            or not item["new_exists"]
            or item["current_url"] != item["desired_url"]
        ):
            outcome["status"] = "skipped"
            results["summary"]["skipped"] += 1
            results["details"].append(outcome)
            continue

        results["summary"]["eligible"] += 1

        if dry_run:
            outcome["status"] = "would_delete"
            results["details"].append(outcome)
            continue

        try:
            storage_client.delete_file(item["old_key"])
            outcome["status"] = "deleted"
            results["summary"]["deleted"] += 1
        except Exception as exc:
            outcome["status"] = "failed"
            outcome["reason"] = str(exc)
            results["summary"]["failed"] += 1

        results["details"].append(outcome)

    return results


def e2e_migration_smoke_test(
    target_strategy: str = storage_client.DOMAIN_HIERARCHY_STRATEGY,
) -> dict:
    """
    Create temporary public/private files, preview migration, migrate them to
    the target strategy, verify URL updates, and clean up test data.
    """
    created = []
    results = {}

    try:
        public_name = f"r2_migrate_public_{frappe.generate_hash(length=6)}.txt"
        private_name = f"r2_migrate_private_{frappe.generate_hash(length=6)}.txt"

        public_file = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": public_name,
                "content": "migration public test",
                "is_private": 0,
                "folder": "Home/Attachments",
            }
        )
        public_file.save(ignore_permissions=True)
        created.append(public_file.name)

        private_file = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": private_name,
                "content": "migration private test",
                "is_private": 1,
                "folder": "Home/Attachments",
            }
        )
        private_file.save(ignore_permissions=True)
        created.append(private_file.name)

        results["before"] = {
            public_file.name: public_file.file_url,
            private_file.name: private_file.file_url,
        }

        results["preview"] = preview_prefix_migration(
            target_strategy=target_strategy,
            file_names=created,
        )
        results["migrate"] = migrate_existing_files(
            target_strategy=target_strategy,
            dry_run=False,
            file_names=created,
        )

        results["after"] = {
            public_file.name: frappe.get_doc("File", public_file.name).file_url,
            private_file.name: frappe.get_doc("File", private_file.name).file_url,
        }

        results["cleanup_old"] = cleanup_old_keys(
            target_strategy=target_strategy,
            dry_run=False,
            file_names=created,
        )
    finally:
        doc_cleanup = {}
        for file_id in created:
            try:
                frappe.delete_doc("File", file_id, ignore_permissions=True)
                doc_cleanup[file_id] = {"deleted": True}
            except Exception as exc:
                doc_cleanup[file_id] = {"deleted": False, "error": str(exc)}
        results["doc_cleanup"] = doc_cleanup

    return results


def _build_migration_plan(
    target_strategy: str,
    source_strategy: str | None = None,
    limit: int | None = None,
    file_names: list[str] | str | None = None,
) -> dict:
    source_strategy = storage_client.normalize_prefix_strategy(
        source_strategy or storage_client.get_storage_config()["prefix_strategy"]
    )
    target_strategy = storage_client.normalize_prefix_strategy(target_strategy)

    items = []
    for file_doc in _get_candidate_files(limit=limit, file_names=file_names):
        item = _describe_file_migration(
            file_doc=file_doc,
            source_strategy=source_strategy,
            target_strategy=target_strategy,
        )
        items.append(item)

    return {
        "site": frappe.local.site,
        "source_strategy": source_strategy,
        "target_strategy": target_strategy,
        "summary": {
            "examined": len(items),
            "eligible": sum(1 for item in items if item["status"] == "pending"),
            "already_migrated": sum(1 for item in items if item["status"] == "already_migrated"),
            "missing_source": sum(1 for item in items if item["status"] == "missing_source"),
            "collision": sum(1 for item in items if item["status"] == "collision"),
            "skipped": sum(
                1 for item in items if item["status"] in {"no_change", "skipped_unmanaged"}
            ),
        },
        "details": items,
    }


def _coerce_file_names(file_names: list[str] | str | None) -> list[str] | None:
    if file_names is None:
        return None
    if isinstance(file_names, str):
        return [item.strip() for item in file_names.split(",") if item.strip()]
    return [item for item in file_names if item]


def _get_candidate_files(
    limit: int | None = None,
    file_names: list[str] | str | None = None,
) -> list[dict]:
    filters = {"is_folder": 0}
    coerced_names = _coerce_file_names(file_names)
    if coerced_names:
        filters["name"] = ["in", coerced_names]

    page_length = int(limit) if limit else 500000
    return frappe.get_all(
        "File",
        filters=filters,
        fields=["name", "file_name", "file_url", "is_private"],
        order_by="creation asc",
        limit_page_length=page_length,
    )


def _describe_file_migration(file_doc: dict, source_strategy: str, target_strategy: str) -> dict:
    old_key = storage_client.build_object_key(
        file_name=file_doc["file_name"],
        is_private=bool(file_doc["is_private"]),
        strategy=source_strategy,
    )
    new_key = storage_client.build_object_key(
        file_name=file_doc["file_name"],
        is_private=bool(file_doc["is_private"]),
        strategy=target_strategy,
    )

    old_meta = storage_client.get_object_metadata(old_key)
    new_meta = storage_client.get_object_metadata(new_key)
    current_url = file_doc.get("file_url") or ""
    desired_url = _desired_file_url(file_doc, target_strategy)

    managed = _looks_managed(file_doc, source_strategy, target_strategy, old_meta, new_meta)

    if old_key == new_key:
        status = "no_change"
    elif not managed:
        status = "skipped_unmanaged"
    elif current_url == desired_url and new_meta and not old_meta:
        status = "already_migrated"
    elif old_meta and new_meta and old_meta != new_meta:
        status = "collision"
    elif not old_meta and new_meta:
        status = "already_migrated"
    elif not old_meta:
        status = "missing_source"
    else:
        status = "pending"

    return {
        "file": file_doc["name"],
        "file_name": file_doc["file_name"],
        "is_private": bool(file_doc["is_private"]),
        "current_url": current_url,
        "desired_url": desired_url,
        "old_key": old_key,
        "new_key": new_key,
        "old_exists": bool(old_meta),
        "new_exists": bool(new_meta),
        "status": status,
    }


def _looks_managed(
    file_doc: dict,
    source_strategy: str,
    target_strategy: str,
    old_meta: dict | None,
    new_meta: dict | None,
) -> bool:
    current_url = file_doc.get("file_url") or ""
    public_urls = {
        storage_client.get_public_url_for_strategy(file_doc["file_name"], source_strategy),
        storage_client.get_public_url_for_strategy(file_doc["file_name"], target_strategy),
    }
    private_routes = {
        storage_client.build_private_file_route(file_doc["name"], source_strategy),
        storage_client.build_private_file_route(file_doc["name"], target_strategy),
    }
    legacy_routes = {
        f"/files/{file_doc['file_name']}",
        f"/private/files/{file_doc['file_name']}",
    }

    return bool(
        old_meta
        or new_meta
        or current_url in public_urls
        or current_url in private_routes
        or current_url in legacy_routes
        or current_url.startswith("/api/method/raftor_storagehq.api.download_private_file")
    )


def _desired_file_url(file_doc: dict, target_strategy: str) -> str:
    if file_doc["is_private"]:
        return storage_client.build_private_file_route(file_doc["name"], target_strategy)
    return storage_client.get_public_url_for_strategy(file_doc["file_name"], target_strategy)


# ---------------------------------------------------------------------------
# Standard Path Migration (path structure upgrade)
# ---------------------------------------------------------------------------


def migrate_to_standard_paths(
    dry_run: bool = True,
    delete_old: bool = False,
    limit: int | None = None,
) -> dict:
    """
    Migrate existing R2 objects from the legacy key structure to the new one.

    Legacy structure:  {site_prefix}/{public|private}/{filename}
      where site_prefix may include a redundant bucket-name segment.

    New structure:     {stripped_site_prefix}/{public|private}/files/{filename}

    This function:
      1. Scans all File records.
      2. Computes the legacy key using the old logic (hardcoded here so it is
         independent of the current build_object_key implementation).
      3. Computes the new key using the current build_object_key.
      4. If the object exists at the legacy key but not the new key, copies it.
      5. Updates File.file_url in the database.
      6. Optionally deletes the old object.
    """
    cfg = storage_client.get_storage_config()
    results = {
        "site": frappe.local.site,
        "dry_run": dry_run,
        "delete_old": delete_old and not dry_run,
        "summary": {
            "examined": 0,
            "already_correct": 0,
            "migrated": 0,
            "skipped_missing": 0,
            "failed": 0,
        },
        "details": [],
    }

    files = _get_candidate_files(limit=limit)
    results["summary"]["examined"] = len(files)

    for file_doc in files:
        file_name = file_doc.get("file_name") or ""
        is_private = bool(file_doc.get("is_private"))

        if not file_name:
            continue

        # -- Legacy key (old logic before our two patches) ------------------
        legacy_key = _build_legacy_key(
            file_name=file_name,
            is_private=is_private,
            cfg=cfg,
        )

        # -- New key (current logic) ----------------------------------------
        new_key = storage_client.build_object_key(
            file_name=file_name,
            is_private=is_private,
        )

        # -- New URL -----------------------------------------------------------
        if is_private:
            new_url = storage_client.build_private_file_route(file_doc["name"])
        else:
            new_url = storage_client.get_public_url(file_name)

        item = {
            "file": file_doc["name"],
            "file_name": file_name,
            "is_private": is_private,
            "legacy_key": legacy_key,
            "new_key": new_key,
            "new_url": new_url,
            "current_url": file_doc.get("file_url") or "",
        }

        if legacy_key == new_key:
            item["status"] = "already_correct"
            results["summary"]["already_correct"] += 1
            results["details"].append(item)
            continue

        if dry_run:
            item["status"] = "would_migrate"
            results["details"].append(item)
            continue

        # -- Check object existence -------------------------------------------
        legacy_exists = storage_client.object_exists(legacy_key)
        new_exists = storage_client.object_exists(new_key)

        if new_exists and not legacy_exists:
            # Already at the new path; just fix the DB URL if needed
            if file_doc.get("file_url") != new_url:
                frappe.db.set_value("File", file_doc["name"], "file_url", new_url, update_modified=False)
                frappe.db.commit()
            item["status"] = "already_correct"
            results["summary"]["already_correct"] += 1
            results["details"].append(item)
            continue

        if not legacy_exists:
            item["status"] = "skipped_missing_source"
            results["summary"]["skipped_missing"] += 1
            results["details"].append(item)
            continue

        # -- Copy + update DB -------------------------------------------------
        savepoint = f"storagehq_std_migrate_{file_doc['name']}"
        frappe.db.savepoint(savepoint)
        try:
            if not new_exists:
                storage_client.copy_file(
                    legacy_key,
                    new_key,
                    is_private=is_private,
                )

            frappe.db.set_value(
                "File",
                file_doc["name"],
                "file_url",
                new_url,
                update_modified=False,
            )
            frappe.db.commit()

            if delete_old and legacy_exists:
                storage_client.delete_file(legacy_key)
                item["deleted_old"] = True

            item["status"] = "migrated"
            results["summary"]["migrated"] += 1

        except Exception as exc:
            frappe.db.rollback(save_point=savepoint)
            item["status"] = "failed"
            item["error"] = str(exc)
            results["summary"]["failed"] += 1

        results["details"].append(item)

    return results


def _build_legacy_key(file_name: str, is_private: bool, cfg: dict) -> str:
    """
    Reconstruct what the R2 key looked like before our two fixes:
      - Before fix 1: no domain-hierarchy deduplication
        → prefix = raw get_storage_prefix() without stripping
      - Before fix 2: no /files/ subfolder
        → folder = 'private' | 'public'  (no trailing /files)
    """
    site_name = frappe.local.site
    strategy = storage_client.normalize_prefix_strategy(cfg.get("prefix_strategy"))

    # Raw prefix (same as current get_storage_prefix — no stripping)
    site_prefix = storage_client.get_storage_prefix(site_name=site_name, strategy=strategy)

    # Old folder (no /files/ suffix)
    folder = "private" if is_private else "public"

    if cfg.get("bucket_prefix"):
        return f"{cfg['bucket_prefix']}/{site_prefix}/{folder}/{file_name}"
    return f"{site_prefix}/{folder}/{file_name}"


def sync_local_files_to_cloud(
    dry_run: bool = True,
    limit: int | None = None,
    update_urls: bool = True,
    overwrite_existing: bool = False,
) -> dict:
    """
    Upload locally-restored files from site/public|private/files to cloud storage,
    then optionally update File.file_url to the canonical cloud/private route.

    This is useful after `bench restore --with-public-files/--with-private-files`.
    """
    cfg = storage_client.get_storage_config()
    if not cfg.get("cloud_enabled"):
        frappe.throw("Cloud storage is not enabled. Enable StorageHQ with provider r2/s3/minio first.")

    if cfg.get("provider") == "local":
        frappe.throw("Storage provider is local. Set provider to r2/s3/minio before syncing.")

    cloud_adapter = S3CompatibleStorageAdapter(cfg)
    files = _get_candidate_files(limit=limit)

    results = {
        "site": frappe.local.site,
        "dry_run": bool(dry_run),
        "update_urls": bool(update_urls),
        "overwrite_existing": bool(overwrite_existing),
        "summary": {
            "examined": len(files),
            "missing_local": 0,
            "already_in_cloud": 0,
            "would_upload": 0,
            "uploaded": 0,
            "url_updated": 0,
            "failed": 0,
        },
        "details": [],
    }

    for file_doc in files:
        file_name = (file_doc.get("file_name") or "").strip()
        if not file_name:
            continue

        is_private = bool(file_doc.get("is_private"))
        object_key = storage_client.build_object_key(file_name=file_name, is_private=is_private)
        desired_url = (
            storage_client.build_private_file_route(file_doc["name"])
            if is_private
            else storage_client.get_public_url(file_name)
        )

        local_candidates = [
            frappe.get_site_path("private", "files", file_name) if is_private else frappe.get_site_path("public", "files", file_name),
            # Legacy accidental nested path produced by previous restore flows
            frappe.get_site_path("private", "files", "files", file_name) if is_private else frappe.get_site_path("public", "files", "files", file_name),
        ]
        local_path = next((p for p in local_candidates if os.path.exists(p)), None)

        item = {
            "file": file_doc["name"],
            "file_name": file_name,
            "is_private": is_private,
            "local_path": local_path,
            "cloud_key": object_key,
            "current_url": file_doc.get("file_url") or "",
            "desired_url": desired_url,
        }

        if not local_path:
            item["status"] = "missing_local"
            results["summary"]["missing_local"] += 1
            results["details"].append(item)
            continue

        cloud_exists = storage_client.object_exists(object_key)
        item["cloud_exists"] = bool(cloud_exists)

        if cloud_exists and not overwrite_existing:
            item["status"] = "already_in_cloud"
            results["summary"]["already_in_cloud"] += 1
            if update_urls and item["current_url"] != desired_url and not dry_run:
                frappe.db.set_value("File", file_doc["name"], "file_url", desired_url, update_modified=False)
                results["summary"]["url_updated"] += 1
                item["url_updated"] = True
            results["details"].append(item)
            continue

        if dry_run:
            item["status"] = "would_upload"
            results["summary"]["would_upload"] += 1
            results["details"].append(item)
            continue

        try:
            content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
            with open(local_path, "rb") as stream:
                cloud_adapter.upload_object(
                    bucket=cfg["bucket"],
                    key=object_key,
                    content=stream,
                    content_type=content_type,
                    is_private=is_private,
                )

            if update_urls and item["current_url"] != desired_url:
                frappe.db.set_value("File", file_doc["name"], "file_url", desired_url, update_modified=False)
                results["summary"]["url_updated"] += 1
                item["url_updated"] = True

            frappe.db.commit()
            item["status"] = "uploaded"
            results["summary"]["uploaded"] += 1
        except Exception as exc:
            frappe.db.rollback()
            item["status"] = "failed"
            item["error"] = str(exc)
            results["summary"]["failed"] += 1

        results["details"].append(item)

    return results


def sync_local_directories_to_cloud(
    dry_run: bool = True,
    include_private: bool = True,
    include_public: bool = True,
    overwrite_existing: bool = False,
    include_backups: bool = False,
    limit: int | None = None,
) -> dict:
    """
    Upload all files from local site public/private file directories to cloud keys.

    This is path-based migration (filesystem -> cloud), independent of File records.
    Use this after bench restore when you want cloud-only serving.
    """
    cfg = storage_client.get_storage_config()
    if not cfg.get("cloud_enabled"):
        frappe.throw("Cloud storage is not enabled. Enable StorageHQ first.")

    if cfg.get("provider") == "local":
        frappe.throw("Storage provider is local. Set provider to r2/s3/minio before syncing.")

    cloud_adapter = S3CompatibleStorageAdapter(cfg)
    roots: list[tuple[bool, str]] = []
    if include_public:
        roots.append((False, frappe.get_site_path("public", "files")))
    if include_private:
        roots.append((True, frappe.get_site_path("private", "files")))

    candidates: list[dict] = []
    for is_private, root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _, filenames in os.walk(root):
            for filename in filenames:
                abs_path = os.path.join(dirpath, filename)
                rel_path = os.path.relpath(abs_path, root).replace(os.sep, "/")
                if not include_backups and is_private and rel_path.startswith("backups/"):
                    continue
                candidates.append({
                    "is_private": is_private,
                    "abs_path": abs_path,
                    "rel_path": rel_path,
                    "file_name": rel_path,
                })

    if limit:
        candidates = candidates[: int(limit)]

    results = {
        "site": frappe.local.site,
        "dry_run": bool(dry_run),
        "overwrite_existing": bool(overwrite_existing),
        "include_public": bool(include_public),
        "include_private": bool(include_private),
        "include_backups": bool(include_backups),
        "summary": {
            "examined": len(candidates),
            "already_in_cloud": 0,
            "would_upload": 0,
            "uploaded": 0,
            "failed": 0,
        },
        "details": [],
    }

    for entry in candidates:
        file_name = entry["file_name"]
        is_private = bool(entry["is_private"])
        cloud_key = storage_client.build_object_key(file_name=file_name, is_private=is_private)
        cloud_exists = storage_client.object_exists(cloud_key)

        item = {
            "file_name": file_name,
            "is_private": is_private,
            "local_path": entry["abs_path"],
            "cloud_key": cloud_key,
            "cloud_exists": bool(cloud_exists),
        }

        if cloud_exists and not overwrite_existing:
            item["status"] = "already_in_cloud"
            results["summary"]["already_in_cloud"] += 1
            results["details"].append(item)
            continue

        if dry_run:
            item["status"] = "would_upload"
            results["summary"]["would_upload"] += 1
            results["details"].append(item)
            continue

        try:
            content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
            with open(entry["abs_path"], "rb") as stream:
                cloud_adapter.upload_object(
                    bucket=cfg["bucket"],
                    key=cloud_key,
                    content=stream,
                    content_type=content_type,
                    is_private=is_private,
                )
            item["status"] = "uploaded"
            results["summary"]["uploaded"] += 1
        except Exception as exc:
            item["status"] = "failed"
            item["error"] = str(exc)
            results["summary"]["failed"] += 1

        results["details"].append(item)

    return results

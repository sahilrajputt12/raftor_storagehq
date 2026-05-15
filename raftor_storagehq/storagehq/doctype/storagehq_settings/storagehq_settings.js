// Copyright (c) 2024, Raftor and contributors
// For license information, please see license.txt

frappe.ui.form.on("StorageHQ Settings", {
	refresh(frm) {
		// Add manual backup trigger button
		frm.add_custom_button(__("Upload Backup Now"), function () {
			frappe.confirm(
				__("This will take a fresh backup and upload it to R2. Continue?"),
				function () {
					frappe.show_alert({ message: __("Starting backup..."), indicator: "blue" });
					frappe.call({
						method: "raftor_storagehq.backups.upload_latest_backups",
						args: { force_new: true },
						freeze: true,
						freeze_message: __("Taking backup and uploading to R2..."),
						callback(r) {
							if (r.message) {
								const uploaded = Object.keys(r.message.uploaded || {}).length;
								const failed = Object.keys(r.message.failed || {}).length;
								if (failed === 0) {
									frappe.show_alert({
										message: __("Backup uploaded successfully ({0} files)", [uploaded]),
										indicator: "green",
									});
								} else {
									frappe.show_alert({
										message: __("Backup completed with {0} failures. Check logs.", [failed]),
										indicator: "orange",
									});
								}
								frm.trigger("load_backup_list");
							}
						},
					});
				}
			);
		}, __("Actions"));

		frm.add_custom_button(__("Test Connection"), function () {
			frappe.call({
				method: "raftor_storagehq.api.r2_storage_status",
				freeze: true,
				freeze_message: __("Checking storage connection..."),
				callback(r) {
					const status = r.message || {};
					if (status.status === "ok") {
						frappe.show_alert({
							message: __("Connection successful ({0}: {1})", [
								status.provider || "r2",
								status.bucket || "-",
							]),
							indicator: "green",
						});
						return;
					}

					if (status.enabled === false) {
						frappe.show_alert({
							message: __(status.status || "R2 not configured"),
							indicator: "orange",
						});
						return;
					}

					frappe.show_alert({
						message: __("Connection failed: {0}", [status.detail || status.status || "Unknown error"]),
						indicator: "red",
					});
				},
				error() {
					frappe.show_alert({
						message: __("Connection check failed. Please review server logs."),
						indicator: "red",
					});
				},
			});
		}, __("Actions"));

		frm.add_custom_button(__("Migrate Local Files"), function () {
			frappe.confirm(
				__("This will upload ALL local files to cloud storage. For large sites, this may take a significant amount of time. Do you want to proceed?"),
				function () {
					frappe.call({
						method: "migrate_local_files",
						doc: frm.doc,
						freeze: true,
						freeze_message: __("Migrating files to cloud..."),
						callback(r) {
							if (r.message) {
								const res = r.message;
								const indicator = res.status === "success" ? "green" : "orange";
								frappe.msgprint({
									title: __("Migration Result"),
									message: res.message,
									indicator,
								});
							}
						},
					});
				}
			);
		}, __("Actions"));

		// Load backup list if on the Backup & Restore tab
		frm.trigger("load_backup_list");
	},

	load_backup_list(frm) {
		const container = frm.get_field("backup_actions_html");
		if (!container) return;

		const $wrapper = $(container.wrapper);
		$wrapper.html(
			`<div class="text-muted text-center p-3">
				<i class="fa fa-spinner fa-spin"></i> ${__("Loading backups from R2...")}
			</div>`
		);

		frappe.call({
			method: "raftor_storagehq.backups.list_backups",
			callback(r) {
				const backups = r.message || [];
				const site = frappe.boot.sitename || "<site>";
				const commandPanel = `
					<div class="r2-restore-commands mb-3">
						<div class="text-muted mb-2"><strong>${__("Native Frappe Restore Commands")}</strong></div>
						<div class="r2-restore-command-group mb-2">
							<div class="text-muted small mb-1">${__("Database + Files")}</div>
							<pre class="mb-0">bench --site ${site} restore &lt;database.sql.gz&gt; --with-public-files &lt;files.tar&gt; --with-private-files &lt;private-files.tar&gt;</pre>
						</div>
						<div class="r2-restore-command-group mb-2">
							<div class="text-muted small mb-1">${__("Database Only")}</div>
							<pre class="mb-0">bench --site ${site} restore &lt;database.sql.gz&gt;</pre>
						</div>
						<div class="r2-restore-command-group">
							<div class="text-muted small mb-1">${__("Files Only (requires DB arg in native restore)")}</div>
							<pre class="mb-0">bench --site ${site} restore &lt;database.sql.gz&gt; --with-public-files &lt;files.tar&gt;
bench --site ${site} restore &lt;database.sql.gz&gt; --with-private-files &lt;private-files.tar&gt;</pre>
						</div>
					</div>
				`;

				if (!backups.length) {
					$wrapper.html(
						`${commandPanel}
						<div class="text-muted text-center p-4">
							<i class="fa fa-cloud" style="font-size:2rem;"></i>
							<p class="mt-2">${__("No backups found in R2.")}</p>
						</div>`
					);
					return;
				}

				// Group backups by date prefix (first 15 chars of filename = YYYYMMDD_HHMMSS)
				const groups = {};
				backups.forEach(b => {
					const ts = b.filename.substring(0, 15); // e.g. 20240512_103000
					if (!groups[ts]) groups[ts] = [];
					groups[ts].push(b);
				});

				let html = `
					<style>
						.r2-restore-commands { border: 1px solid var(--border-color); border-radius: 6px; padding: 12px; background: var(--subtle-bg); }
						.r2-restore-command-group pre { background: var(--fg-color); border: 1px solid var(--border-color); border-radius: 4px; padding: 8px; white-space: pre-wrap; word-break: break-word; }
						.r2-backup-group { border: 1px solid var(--border-color); border-radius: 6px; margin-bottom: 12px; overflow: hidden; }
						.r2-backup-group-header { background: var(--subtle-bg); padding: 8px 14px; font-weight: 600; display: flex; align-items: center; }
						.r2-backup-file { padding: 6px 14px; display: flex; justify-content: space-between; align-items: center; border-top: 1px solid var(--border-color); font-size: 0.85rem; }
						.r2-backup-badge { font-size: 0.75rem; padding: 2px 8px; border-radius: 10px; background: var(--blue-50); color: var(--blue-600); }
						.r2-backup-actions { display: flex; align-items: center; gap: 8px; }
						.r2-group-command { border-top: 1px solid var(--border-color); padding: 8px 14px; background: var(--fg-color); }
						.r2-group-command pre { margin: 0; white-space: pre-wrap; word-break: break-word; font-size: 0.8rem; }
					</style>
					${commandPanel}
				`;

				Object.entries(groups).sort((a, b) => b[0].localeCompare(a[0])).forEach(([ts, files]) => {
					const displayDate = ts.replace(/_/, " ").replace(/(\d{4})(\d{2})(\d{2})/, "$3/$2/$1");
					const dbFile = files.find(f => f.type === "database");
					const publicFile = files.find(f => f.type === "public_files");
					const privateFile = files.find(f => f.type === "private_files");

					html += `
						<div class="r2-backup-group">
							<div class="r2-backup-group-header">
								<span><i class="fa fa-archive mr-1"></i> ${displayDate}</span>
							</div>
					`;

					files.forEach(f => {
						const typeLabel = {
							database: "Database",
							public_files: "Public Files",
							private_files: "Private Files",
							config: "Config",
							other: "Other",
						}[f.type] || f.type;

						html += `
							<div class="r2-backup-file">
								<span class="r2-backup-badge">${typeLabel}</span>
								<span class="text-muted" style="flex:1; margin: 0 10px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${f.filename}">${f.filename}</span>
								<div class="r2-backup-actions">
									<span>${f.size_mb} MB</span>
									<button class="btn btn-xs btn-default r2-download-btn"
										data-key="${f.key}"
										data-filename="${f.filename}">
										<i class="fa fa-download"></i> ${__("Download")}
									</button>
								</div>
							</div>
						`;
					});

					if (dbFile) {
						const dbCmd = `bench --site ${site} restore ${dbFile.filename}`;
						const withPublic = publicFile ? `${dbCmd} --with-public-files ${publicFile.filename}` : "";
						const withPrivate = privateFile ? `${dbCmd} --with-private-files ${privateFile.filename}` : "";
						const withBoth = publicFile && privateFile
							? `${dbCmd} --with-public-files ${publicFile.filename} --with-private-files ${privateFile.filename}`
							: "";
						const lines = [withBoth, dbCmd, withPublic, withPrivate].filter(Boolean);
						html += `
							<div class="r2-group-command">
								<div class="text-muted small mb-1">${__("Commands for this backup set (run from bench)")}</div>
								<pre>${lines.join("\n")}</pre>
							</div>
						`;
					}

					html += `</div>`;
				});

				$wrapper.html(html);

				$wrapper.find(".r2-download-btn").on("click", function () {
					const key = $(this).data("key");
					const filename = $(this).data("filename");
					frappe.call({
						method: "raftor_storagehq.backups.get_backup_download_url",
						args: { backup_key: key },
						freeze: true,
						freeze_message: __("Preparing download link..."),
						callback(res) {
							const msg = res.message || {};
							if (msg.url) {
								window.open(msg.url, "_blank");
								frappe.show_alert({
									message: __("Download started: {0}", [filename]),
									indicator: "green",
								});
							}
						},
					});
				});
			},
		});
	},
});

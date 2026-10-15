# Changelog

NSM Core follows `0.49.x`: every release promoted to `deploy` increases the
patch number in `app/app/entrypoint.py` (`APP_VERSION`, shown in the sidebar).
MikroTik Agent versions are independent (`mikrotik_agent_generation.py`).

## 0.49.154 — 2026-10-15

New interface (React) — backup restore tests, phase 7r
- **Restore test of a file** (`/app/devices/{id}/backups/artifacts/{artifact_id}/restore-test`), opened from "Restore test" in the Backup tab and in the export viewer:
  - integrity re-checked now on the archived file (present, size, SHA-256) and the source RouterOS version;
  - outcome form (`backup.execute`): method, target, target RouterOS version, result, date and notes, with the classic checks. "Passed" cannot be chosen for a file that is not intact;
  - history of the file.
- **Restore test history** (`/app/devices/{id}/backups/restore-tests`): every test of the device, with who performed it. The file identity is kept after retention removes the file.
- **Shared code.** `backup_restore_tests.record_restore_test` validates, stores, syncs the Action Center issue and audits; the classic form uses it and keeps its messages. Validation errors now carry codes (`method`, `result`, `target`, `performed_at`, `performed_future`, `integrity`).
- **API.**
  - `GET /api/ui/v1/devices/{id}/backups/restore-tests` (`backup.read`);
  - `GET/POST /api/ui/v1/devices/{id}/backups/artifacts/{artifact_id}/restore-test` (`backup.read` / `backup.execute` + CSRF);
  - the device backup list no longer returns the classic `restore_url` / `restore_tests` links.

## 0.49.153 — 2026-10-15

New interface (React) — backup text exports, phase 7q
- **Export viewer** (`/app/devices/{id}/backups/artifacts/{artifact_id}`), opened from "Open / compare" in the device Backup tab and from "View" / "Diff" in the configuration history:
  - read-only RouterOS export with line numbers, a search box that keeps the original line numbers, size and SHA-256, download and restore-test links;
  - comparison with any older text export of the same device: added / removed / unchanged counts, changes only with 3 lines of context (or the whole file), a notice when the diff is truncated;
  - binary, too large and invalid comparisons show a translated message.
- The Backup tab stays highlighted on its sub-pages.
- **Shared code.** `device_backup_explorer.artifact_text_record` reads, diffs and audits the export (`BACKUP_EXPORT_VIEWED` / `BACKUP_EXPORT_DIFF_VIEWED`); the classic viewer uses it and behaves as before.
- **API.**
  - `GET /api/ui/v1/devices/{id}/backups/artifacts/{artifact_id}?against=` (`backup.read`); errors are 422 `binary`, `too_large`, `compare_id`, `compare_binary`;
  - the device backup list now returns `viewable` instead of the classic `view_url`.

## 0.49.152 — 2026-10-15

New interface (React) — device lifecycle, phase 7p
- **Device lifecycle** (`/app/devices/{id}/lifecycle`), opened from the lifecycle field on the device overview and from the lifecycle list:
  - **Value:** state, EOL and EOS dates, source and verification date, with their origin (catalog or manual).
  - **Catalog:** the correlation result for the model (match, ambiguous, missing).
  - **Manual value:** the classic checks with translated messages, plus a button to go back to the catalog.
  - **End-of-life decisions:** for an EOL or EOS device, plan the replacement, record an exception, record the replacement (the new device shows its vendor icon) or the decommissioning, and reopen. Every decision keeps its history.
- **Links.** The agent field on the device overview now opens the Agent tab of the new interface.
- **Shared code.** The lifecycle changes are now shared functions, used by both interfaces; the classic page behaves as before, with the same messages.
  - `lifecycle_catalog.set_manual_record`, `use_catalog_record`;
  - `lifecycle_remediation.update_record`.
- **API.**
  - `GET /api/ui/v1/devices/{id}/lifecycle` (`security.read`);
  - `POST .../lifecycle`, `.../lifecycle/catalog`, `.../lifecycle/remediation` (`lifecycle.manage` + CSRF).

## 0.49.151 — 2026-10-15

New interface (React) — cnMaestro and GenieACS pages of a device, phase 7o
- **cnMaestro tab** (`/app/devices/{id}/cnmaestro`):
  - link state, with the name, type, network, tower and state read from cnMaestro;
  - the last values with their history charts, from 24 hours to 90 days.
- **ACS tab** (`/app/devices/{id}/genieacs`, TR-069 CPEs):
  - NSM record, connector state and the inventory read from GenieACS;
  - «Search GenieACS» looks up the serial first, then the MAC, without changing anything; then come the association and «Refresh from GenieACS». The NSM customer and site never change.
- **Charts.** The UISP and cnMaestro charts now share the same component.
- **Shared code.** The GenieACS association and refresh are now shared functions, used by both interfaces; the classic page behaves as before.
  - `genieacs_connector.associate_record`, `refresh_record`.
- **API.**
  - `GET /api/ui/v1/devices/{id}/cnmaestro` (`devices.read`), `.../cnmaestro/metrics` (`monitoring.read`);
  - `GET /api/ui/v1/devices/{id}/genieacs` (`devices.read`);
  - `POST .../genieacs/preview`, `/associate`, `/refresh` (`devices.read` / `devices.write` + CSRF).

## 0.49.150 — 2026-10-15

New interface (React) — UISP page of a device, phase 7n
- **UISP tab** (`/app/devices/{id}/uisp`, Ubiquiti):
  - **Record and connector:** the NSM record (customer, site, alias, MAC, UISP ID) and the connector state.
  - **Association:** «Search UISP» finds the exact MAC without changing anything; then come the association and «Refresh from UISP», which keeps the NSM customer and site.
  - **Charts:** signal, link capacity, CPU/RAM and stations over 24 hours to 90 days, plus current, minimum, average and maximum values.
  - **Firmware:** the versions seen by UISP and the compatibility warning.
  - **Operations through UISP:**
    - configuration backup archived in NSM;
    - reboot, confirmed with the device name and verified by NSM;
    - interfaces and events read on request.
- **Shared code.** The UISP association and refresh are now shared functions, used by both interfaces; the classic page behaves as before.
  - `uisp_connector.associate_record`, `refresh_record`.

  The UISP operations reuse `uisp_operations`.
- **API.** A new module, `app/ui_device_connectors.py`:
  - `GET /api/ui/v1/devices/{id}/uisp` (`devices.read`);
  - `GET .../uisp/metrics` (`monitoring.read`);
  - `POST .../uisp/preview`, `/associate`, `/refresh`, `/reboot`, `/backup`, `/interfaces`, `/events`, with the classic permissions and the CSRF header.

## 0.49.149 — 2026-10-15

New interface (React) — RouterOS upgrade plan, phase 7l
- **Firmware tab** (`/app/devices/{id}/firmware-upgrade`), opened from the device tabs and from the firmware worklist:
  - **Prerequisites:** installed and available version, channel, free space, last check and the MikroTik catalogue with security reasons.
  - **Plan steps:** plan creation with the pre-upgrade backup, approval, package download and activation, each a separate step with the same literal confirmation as the classic page (`UPGRADE`, `DOWNLOAD`, `ACTIVATE` + version).
  - **Progress:** the five gates with their state, the plan dates and the post-reboot verification.
  - **Cancel:** the plan can be cancelled with a confirmation; its pending jobs are withdrawn.
  - **Legacy agents:** they get their own upgrade, with the backup acknowledgement and the `AGGIORNA <version>` confirmation.
- **Worklist.** «Prepare upgrade» in the firmware worklist now creates the plan and opens it in the new interface. The new interface no longer sends any classic form.
- **Shared code.** The plan steps are now shared functions, used by both interfaces; the classic pages behave as before, with the same messages and status codes.
  - `firmware_upgrade_planner.latest_plan`, `approve_record`, `cancel_record`;
  - `firmware_package_staging.stage_record`;
  - `firmware_activation.activate_record`;
  - `mikrotik_legacy_operations.queue_legacy_upgrade_record`.
- **API.**
  - `GET /api/ui/v1/devices/{id}/firmware-upgrade` (`firmware.read`);
  - `POST .../firmware-upgrade/plan`, `.../firmware-upgrade/{plan_id}/approve|stage|activate|cancel` and `.../legacy-upgrade` (`firmware.execute` + CSRF).

## 0.49.148 — 2026-10-15

New interface (React) — controlled reboot and RouterBOOT, phase 7k
- **Controlled reboot** (`/app/devices/{id}/reboot`, from «Reboot» on the device page):
  - shows what blocks the reboot (legacy agent, profile, credential, offline device, operation in progress), the last request with uptime before and after, and the history;
  - the reboot needs a reason and the device name typed as confirmation.
- **RouterBOOT** (`/app/devices/{id}/routerboot`):
  - active and available bootloader, workflow state and last check;
  - the four steps (read, flash, reboot, verification) with their state;
  - flash and reboot are separate actions, each confirmed with the same literal text as the classic page.
- **Shared code.** Reboot and RouterBOOT actions are now shared functions, used by both interfaces; the classic pages behave as before, with the same messages and status codes.
  - `mikrotik_device_reboot.queue_reboot_record`, `reboot_history`;
  - `routerboot_lifecycle.stage_record`, `reboot_record`.

  RouterBOOT errors now carry a code (`InputError`).
- **API.**
  - `GET /api/ui/v1/devices/{id}/reboot`, `/routerboot` (`firmware.read`);
  - `POST /api/ui/v1/devices/{id}/reboot`, `/routerboot/stage`, `/routerboot/reboot` (`firmware.execute` + CSRF).

## 0.49.147 — 2026-10-15

New interface (React) — MikroTik Configuration tab and export history, phase 7j
- **Configuration tab** (`/app/devices/{id}/configuration`, MikroTik). It shows the latest agent read of each section, with the same filters as the classic page:
  - **Sections:** resources, interfaces, IP addresses, routes, firewall, DHCP leases, PPP/VPN and warning/error log;
  - **Each section:** counters, search, state/kind/action filters, and the time of the last read;
  - **Refresh section:** asks the agent for a new read, only for an online device; a section already queued is reported.
- **Configuration history** (`/app/devices/{id}/configuration/history`):
  - archived RouterOS exports with the change from the previous one, the approved baseline and the open drift;
  - «Set as baseline» resolves the open drift issue;
  - view and differences open the export viewer.
- **Fix.** The definition list of the Exposure tab now lays out its fields correctly.
- **Shared code.** Configuration refresh and baseline approval are now shared functions, used by both interfaces; the classic pages behave as before.
  - `mikrotik_workspace.queue_snapshot_record`;
  - `config_drift.set_baseline_record`.

  The Resources section now gives its fields a key as well as their label.
- **API.**
  - `GET /api/ui/v1/devices/{id}/configuration` (`devices.read`);
  - `POST .../configuration/refresh` (`devices.write` + CSRF);
  - `GET .../configuration/history` (`backup.read`);
  - `POST .../configuration/history/{artifact_id}/baseline` (`backup.configure` + CSRF).

## 0.49.146 — 2026-10-15

New interface (React) — device Activity, Exposure and Compliance tabs, phase 7i
- **Activity tab** (`/app/devices/{id}/jobs`):
  - agent jobs, newest first, with type, state, creation and completion time, attempts and error, plus the number of active jobs;
  - search, state and type filters, and pages of 50;
  - payloads and results are never shown;
  - a link opens the activity log filtered on the device.
- **Exposure tab** (`/app/devices/{id}/exposure`), same content as the classic tab:
  - services reachable from the WAN, as read by the agent or probed from outside, with state, severity and reason;
  - port forwards, the devices that share the IP (with their vendor icons) and the last check;
  - «Check now» and the public IP to probe, for whoever can remediate.
- **Compliance tab** (`/app/devices/{id}/compliance`): results of the effective baseline, failures first, with exceptions and acknowledgements; failed controls link to their handling page.
- **Shared code.** The Exposure tab data and actions are now shared functions, used by both interfaces; the classic page behaves as before.
  - `device_exposure.page_state`, `request_check_record`, `set_target_record`.
- **API.** A new module, `app/ui_device_tabs.py`:
  - `GET /api/ui/v1/devices/{id}/jobs` (`devices.read`);
  - `GET /api/ui/v1/devices/{id}/exposure` (`security.read`);
  - `POST .../exposure/check`, `.../exposure/target` (`security.remediate` + CSRF);
  - `GET /api/ui/v1/devices/{id}/compliance` (`compliance.read`).

## 0.49.145 — 2026-10-15

New interface (React) — device CSV import, phase 7h
- **CSV import in `/app/devices/import`**, from «Import CSV» in the device list (administrators):
  - **Check first:** a check that saves nothing shows every row with its outcome (ready, already present, error with the reason), the vendor icon, customer, site and address.
  - **Import:** the «Import N devices» button imports only the ready rows, and the imported devices link to their page.
  - **Limits:** the template download, the limits and the columns are shown on the page, which also says that credentials are never accepted.
- **Shared code.** The check and the import are now one shared function (`device_csv_import.process_upload`), used by both interfaces; the classic page behaves as before.
- **API.** `GET` and `POST /api/ui/v1/device-import` (administrators):
  - `POST` takes multipart `csv_file` + `mode` (`validate` or `import`) and needs the CSRF header;
  - an unreadable file returns 422 `file_invalid` with the reason.

## 0.49.144 — 2026-10-15

New interface (React) — device management, phase 7g
- **Manage device in `/app/devices/{id}/manage`**, from «Manage» on the device page:
  - **Assignment:** change the alias, the customer and the site. Only the customers you can see are offered, and only the chosen customer's sites. When the device changes customer, a warning says that its open issues and notifications follow it.
  - **Delete:** the button only becomes active once you type the device name; backup files are removed and a snapshot stays in the activity log.
- **Manage devices of a customer in `/app/customers/{id}/devices/manage`**, from «Manage devices» on the customer page:
  - the devices are listed with their vendor icon, site, IP/MAC and state, and can be filtered;
  - you can select several devices, then move them to another customer and site, or delete them by typing how many they are.
- **Messages.** Validation messages are translated.
- **Shared code.** Device changes now go through shared functions, used by both interfaces; the classic pages behave as before, with the same messages.
  - `crud_extension.update_device_record`, `delete_device_record`, `bulk_devices_record`.
- **API.** All routes need `devices.write`; the `POST` routes need the CSRF header. Validation errors are 422 `{code, message}`.
  - `GET /api/ui/v1/devices/{id}/manage`;
  - `POST /api/ui/v1/devices/{id}`, `/devices/{id}/delete`;
  - `GET /api/ui/v1/customers/{id}/devices/manage`;
  - `POST /api/ui/v1/customers/{id}/devices/bulk`.

## 0.49.143 — 2026-10-15

New interface (React) — new device, phase 7f
- **New device in `/app/customers/{id}/devices/new`**, opened from «+ Device» on the customer page. It follows the classic form in three steps (type, organisation, association), and the help text changes with the vendor:
  - **MikroTik:** the device is created waiting for enrollment. The page then shows the RouterOS command that installs the agent, with a copy button. The command is shown only once; its token is stored hashed.
  - **Ubiquiti and Cambium:** NSM looks the device up in UISP or cnMaestro by MAC or serial. If the connector is not configured, the page says so before submitting and links to its settings.
  - **TP-Link (TR-069):** MAC or serial is required.
  - **Other vendor:** manufacturer (used for CVE matching), MAC, serial, model, management IP and firmware.

  Errors are translated: invalid MAC, site of another customer, MAC already used, connector missing.
- **Agent reinstall** from the device Agent tab now stays in the new interface: it creates a new one-shot enrollment and shows the command there.
- **Shared code.** Device creation, the enrollment command and the agent reinstall are now shared functions, used by both interfaces; the classic pages behave as before.
  - `main.create_device_record`, `main.enrollment_command`;
  - `agent_ui.reinstall_record`.

  Connector onboarding errors now carry a code (`input_errors.InputError`); the classic messages are unchanged.
- **API.**
  - `GET /api/ui/v1/customers/{id}/devices/new` and `POST /api/ui/v1/customers/{id}/devices` need `devices.write`;
  - `POST /api/ui/v1/devices/{id}/agent/reinstall` needs `devices.enroll`.

  The `POST` routes need the CSRF header; answers that contain an enrollment command are `no-store`.

## 0.49.142 — 2026-10-15

New interface (React) — customer Security, Backup and Activity tabs, phase 7e
- **Security tab in `/app/customers/{id}/security`**, with the same data as the classic tab:
  - **Summary:** counters for severe CVEs, firmware, end of life, issues and compliance.
  - **Severe CVEs:** each open critical or high CVE with its device (vendor icon), CVSS score, installed and fixed version.
  - **Firmware:** devices to update, with current and recommended version.
  - **End of life:** EOL and EOS devices, with their date.
  - **Issues:** open security, firmware and lifecycle issues.
  - **Compliance:** compliance summary with the failing controls.

  Each section links to the full page (vulnerabilities and firmware filtered on the customer).
- **Backup and Activity tabs** now open the backup overview and the activity log of the new interface, filtered on the customer. Every customer tab is now in the new interface.
- **Other links.** The «Incidents», «Action Center» and «All events» links of the customer page now stay in the new interface, filtered on the customer.
- **Shared code.** The classic Security tab now reads its data from `customer_tabs.security_data`, which the API uses too; the classic page behaves as before.
- **API.** `GET /api/ui/v1/customers/{id}/security` needs `security.read`; customer scope applies.

## 0.49.141 — 2026-10-15

New interface (React) — customer and site forms, phase 7d
- **New customer** in `/app/customers/new`, opened from «New customer» in the customer list. It asks for a name, an optional unique code and notes, then opens the new customer.
- **Manage customer** in `/app/customers/{id}/edit`:
  - **Details:** edit name, code and notes.
  - **Delete:** the page shows how many devices and sites will go. The button only becomes active once you type the exact customer name; the server checks the name again, and a snapshot stays in the activity log.
- **Sites, in the customer's Sites tab, without leaving the page:**
  - add a site;
  - edit name, address and notes;
  - delete it, after a confirmation that says how many devices stay in the customer without a site.
- **Messages.** Validation messages are translated: name required, customer code already used, confirmation name does not match.
- **Shared code.** Customer and site changes now go through shared functions, used by both interfaces; the classic forms behave as before, except that a blank customer or site name is now refused when created too.
  - `main.create_customer_record`, `create_site_record`;
  - `crud_extension.update_customer_record`, `delete_customer_record`, `update_site_record`, `delete_site_record`.
- **API.** All routes need `customers.write` and the CSRF header. Validation errors are 422 `{code, message}`.
  - `POST /api/ui/v1/customers`, `/customers/{id}`, `/customers/{id}/delete`;
  - `POST /api/ui/v1/customers/{id}/sites`, `/sites/{site_id}`, `/sites/{site_id}/delete`.

## 0.49.140 — 2026-10-14

New interface (React) — profile, phase 7c
- **Profile in `/app/profile`**, opened from your name in the top bar:
  - **Account:** name, username, e-mail, role, last sign-in.
  - **Password change:** the classic checks with translated messages. This session stays signed in (it is re-issued with a new CSRF token) and the other sessions end.
  - **Sessions:** sign out of the other sessions, with a confirmation.
  - **Appearance:** light or dark theme, saved on the account.
  - **Two-step verification:** setup with QR code and manual key, then confirmation with the app code. Recovery codes are shown once, with a copy button. You can also generate new codes (password required) or turn the feature off (password + app code).
  - **Notifications:** e-mail address; for e-mail, Telegram and Slack, the on/off switch, minimum level and categories. The Telegram chat ID is shown; the Slack webhook is never sent back to the browser, and an empty field keeps it. You can also send a test message, set the vulnerability newsletter and see the latest deliveries.
- **Shared code.** The profile logic is now made of shared functions, used by both interfaces; the classic profile page behaves as before:
  - `ui_extension.change_own_password_record`;
  - `login_security.revoke_own_record`;
  - `two_factor.setup_record`, `enable_record`, `regenerate_record` and `disable_record`;
  - `notification_delivery.save_preferences_record` and `send_test_record`;
  - `notification_digest.set_digest`.
- **API.** All routes are open to any signed-in user; the `POST` routes need the CSRF header. Validation errors return 422 with the classic error codes.
  - `GET /api/ui/v1/profile` (`no-store`);
  - `POST /api/ui/v1/profile/password`, `/sessions/revoke`;
  - `POST /api/ui/v1/profile/2fa/setup`, `/2fa/enable`, `/2fa/recovery`, `/2fa/disable` (recovery codes `no-store`);
  - `POST /api/ui/v1/profile/notifications`, `/notifications/test`, `/notifications/digest`.

## 0.49.139 — 2026-10-14

New interface (React) — global search and notifications, phase 7b
- **Search box in the top bar** of the new interface. It opens `/app/search?q=…`, which finds devices, customers and sites by name, model, serial number, MAC (in any notation), IP, external ID or address:
  - every device shows its vendor icon, customer, site, identifiers and state;
  - customers and sites link to their pages;
  - the results are the same as the classic global search, and the customer scope still applies.
- **Notification bell** in the top bar, with the unread count refreshed every minute.
- **`/app/notifications`** lists active notifications, newest first, and can show only unread ones. A notification can show its device (with vendor icon), open its advisory or its source, and be marked as read; all of them can be marked as read at once. The read state stays personal to each user. Only web links and in-app paths are offered as a source.
- **Shared code.** The global search and the notification read logic are now shared functions (`main.search_records`, `notification_rows`, `mark_notification_read_record`, `mark_all_notifications_read_record`), used by both interfaces; the classic pages behave as before.
- **API.** All routes are open to any signed-in user; the `POST` routes need the CSRF header:
  - `GET /api/ui/v1/search`;
  - `GET /api/ui/v1/notifications` and `/notifications/summary`;
  - `POST /api/ui/v1/notifications/{id}/read` and `/notifications/read-all`.

## 0.49.138 — 2026-10-14

New interface (React) — dashboard, phase 7a
- **Dashboard in `/app/`.** The home of the new interface was a list of links; it is now the full dashboard:
  - **KPIs:** devices, online, offline, protected backups, severe CVEs, firmware to update, Action Center issues and end-of-life devices. Each one opens the matching page;
  - **Coverage:** availability and backup coverage rings, with percentages formatted for the chosen language;
  - **Needs attention:** critical issues, severe CVEs, failed backups, firmware to update and pending devices;
  - **Fleet distribution:** the most common vendors, firmware versions and models;
  - **Latest advisories:** recent CVEs with severity and the number of affected devices, linked to the advisory page;
  - **Recent activity:** the latest audit events, linked to the activity log.
  The page refreshes every 30 seconds.
- **Shared code.** The classic dashboard now reads its data from `dashboard_ui.dashboard_data`, which the API uses too; the classic page behaves as before.
- **API.** `GET /api/ui/v1/dashboard` is open to any signed-in user.
- **Phase 7 starts.** The remaining classic pages are migrated one at a time before the classic interface is retired.

## 0.49.137 — 2026-10-14

New interface (React) — UISP onboarding, phase 6q
- **UISP onboarding in `/app/integrations/uisp/onboarding`**, opened from the UISP connector page:
  - **States:** UISP devices by NSM state (new, to link to an existing record, already in NSM, not importable), with counts, search and UISP site filter; each device shows the Ubiquiti icon and, when it is already in NSM, the linked device;
  - **Selection:** devices are selected on the page, then the operator picks the NSM customer and optional site (only that customer's sites are offered);
  - **Preview and confirmation:** the preview shows what will be created, linked or skipped. On confirmation the UISP list is read again, so devices changed in the meantime are skipped and reported; linked devices stay in their customer. Afterwards the page links to the customer.
- The onboarding preview and commit are now shared functions (`uisp_onboarding.target`/`preview_plan`/`commit_selection`), used by both interfaces; the classic page behaves as before.
- **Phase 6 complete.** Every administration page, integration and connector is now in the new interface.
- **API.** All routes need `devices.write`; the `POST` routes also need the CSRF header:
  - `GET /api/ui/v1/integrations/uisp/onboarding`;
  - `POST /api/ui/v1/integrations/uisp/onboarding/preview`, `/commit`.

## 0.49.136 — 2026-10-14

New interface (React) — UISP connector, phase 6p
- **UISP in `/app/admin/integrations/uisp`**, in the integration sub-tabs; it is also where the *Integrations* tab of the administration area now opens:
  - **Connection:** console URL, read-only API token (encrypted, kept when empty, never sent back to the browser), connector enabled, sync interval, TLS check with its warning;
  - **Connector status:** endpoint, read-only mode, TLS, last test and outcome, last sync, last error, with *Test connection*;
  - **Periodic sync:** state, interval, last success, next attempt, consecutive errors, last outcome (linked, updated, missing, conflicts), with *Synchronise now*;
  - **Linking flow** of an Ubiquiti device.
- *Device onboarding* still opens the classic page.
- With this, the four connectors (UISP, GenieACS, Zabbix, cnMaestro), NVD and the RouterOS catalogue are configured in the new interface. The integrations hub opens all of them.
- Saving the connector, the connection test and the manual sync are now shared functions (`uisp_connector.save_config`/`test_connection`/`sync_interval`, `uisp_sync.manual_sync`), used by both interfaces; the classic page behaves as before.
- **API.** All routes need `users.manage`; the `POST` routes also need the CSRF header:
  - `GET /api/ui/v1/admin/integrations/uisp`;
  - `POST /api/ui/v1/admin/integrations/uisp`, `/test`, `/sync`.

## 0.49.135 — 2026-10-14

New interface (React) — GenieACS connector, phase 6o
- **GenieACS / TR-069 in `/app/admin/integrations/genieacs`**, in the integration sub-tabs:
  - **Built-in GenieACS:** when the Docker stack runs it, *Use built-in GenieACS* links NSM to it; otherwise the page shows how to enable it;
  - **External GenieACS:** NBI URL (localhost and loopback refused), authentication (none, HTTP Basic, Bearer), password or token (encrypted, kept when empty, never sent back to the browser), MAC parameter paths, online window, connector enabled, TLS check;
  - **Connector status:** endpoint, read-only mode, authentication, last test, outcome, last CPE read and last error, with *Test connection*;
  - **Linking flow** of a CPE between NSM and GenieACS.
- The integrations hub opens the new page from the GenieACS card.
- Saving the connector, linking the built-in GenieACS and the connection test are now shared functions (`genieacs_connector.save_config`/`use_internal`/`test_connection`/`auth_info`), used by both interfaces; the classic page behaves as before.
- **API.** All routes need `users.manage`; the `POST` routes also need the CSRF header:
  - `GET /api/ui/v1/admin/integrations/genieacs`;
  - `POST /api/ui/v1/admin/integrations/genieacs`, `/internal`, `/test`.

## 0.49.134 — 2026-10-14

New interface (React) — Zabbix connector, phase 6n
- **Zabbix in `/app/admin/integrations/zabbix`**, in the integration sub-tabs:
  - **Status:** Zabbix version and test outcome, last sync, hosts managed by NSM (created, updated, disabled), open problems, excluded devices, missing templates and the errors of the last sync;
  - **Devices with a shared IP:** each device with its manufacturer icon and the address Zabbix must use behind the customer's NAT, editable on the row;
  - **Connection:** URL, API token or username and password (encrypted, kept when empty, never sent back to the browser), TLS check;
  - **Hosts:** group prefix, agent or SNMP interface, SNMP community, templates per vendor;
  - **Scope:** all devices or selected customers, active sync;
  - **Actions:** *Test connection*, *Synchronise now* and *Open Zabbix*, with the authentication notes per Zabbix version.
- The integrations hub opens the new page from the Zabbix card.
- Saving the settings, the connection test, the manual sync and the per-device Zabbix address are now shared functions (`zabbix_connector.save_config`/`test_connection`/`sync_now`/`set_device_ip`/`credential_info`), used by both interfaces. Unreadable stored credentials now give a clear test error instead of a server error.
- **API.** All routes need `users.manage`; the `POST` routes also need the CSRF header, and the device address also needs `devices.write`:
  - `GET /api/ui/v1/admin/integrations/zabbix`;
  - `POST /api/ui/v1/admin/integrations/zabbix`, `/test`, `/sync`, `/devices/{id}/ip`.

## 0.49.133 — 2026-10-14

New interface (React) — cnMaestro connector, phase 6m
- **cnMaestro in `/app/admin/integrations/cnmaestro`**, in the integration sub-tabs:
  - **Status:** last sync with its outcome, devices read from cnMaestro, linked Cambium devices and those without a match, statistics errors, the last failed test;
  - **Connection:** URL, client ID, client secret (encrypted, kept when empty, never sent back to the browser), TLS check, active sync;
  - **Actions:** *Test connection* and *Synchronise now*, with the result shown on the page.
- The integrations hub opens the new page from the cnMaestro card.
- Saving the connection and the connection test are now shared functions (`cnmaestro_connector.save_config`/`test_connection`/`client_id_of`), used by both interfaces. Unreadable stored credentials now give a clear test error instead of a server error.
- **API.** All routes need `users.manage`; the `POST` routes also need the CSRF header:
  - `GET /api/ui/v1/admin/integrations/cnmaestro`;
  - `POST /api/ui/v1/admin/integrations/cnmaestro`, `/test`, `/sync`.

## 0.49.132 — 2026-10-14

New interface (React) — Demo Lab, phase 6l
- **Demo Lab in `/app/admin/demo`**, in the *Demo Lab* tab of the new administration area:
  - the number of demo customers (code `DEMO-*`);
  - *Generate demo data* when there are none;
  - *Remove demo data*, enabled only after typing DEMO; it removes only demo objects and demo backup files.
- After seeding or clearing, every list of the new interface refreshes.
- Seeding and clearing, each with its audit event, are now shared functions (`demo_ui.run_seed`/`run_clear`), used by both interfaces; the classic page behaves as before.
- **With this, every tab of the administration area is in the new interface.** Only the configuration of the UISP, GenieACS, Zabbix and cnMaestro connectors still opens the classic pages.
- **API.** All routes need `users.manage`; the `POST` routes also need the CSRF header:
  - `GET /api/ui/v1/admin/demo`;
  - `POST /api/ui/v1/admin/demo/seed`, `/clear` (body `{"confirm": "DEMO"}`).

## 0.49.131 — 2026-10-14

New interface (React) — ACS (TR-069), phase 6k
- **ACS administration in `/app/admin/acs`**, in the *ACS* tab of the new administration area:
  - **Status:** link state with GenieACS, the ACS URL the CPEs must use (port 7547) and the GenieACS panel port;
  - **ACS panel:** *Open ACS panel* opens it in a new tab, reserved to NSM administrators, then the GenieACS login;
  - **TP-Link CPE setup guide** (CPE, network, templates, checks in the panel, link in NSM) and the security advice, in four languages;
  - **Provisioning templates:** the presets with their condition, the NSM base provision script, and *Install base templates*. When GenieACS is not linked yet, the page says how to link it.
- Installing the base templates is now a shared function (`acs_admin.install_base_templates`), used by both interfaces; the classic page behaves as before.
- **API.** All routes need `users.manage`; the `POST` route also needs the CSRF header:
  - `GET /api/ui/v1/admin/acs`;
  - `POST /api/ui/v1/admin/acs/templates`.

## 0.49.130 — 2026-10-14

New interface (React) — branding, phase 6j
- **Branding in `/app/admin/branding`**, in the *Branding* tab of the new administration area:
  - **Visual identity:** platform name, tagline and the three colours (primary, sidebar, highlight), each with *Default* to restore it.
  - **Logo:** PNG, JPEG or WebP up to 2 MB, with preview and *Remove logo* (with confirmation). Size and format are checked before upload and again on the server.
- **The new interface now uses the branding.** The sidebar shows the logo next to the platform name, and the three brand colours apply to the new interface as on the classic pages, set as CSS custom properties (allowed by the content security policy). Changes show up at once, without reloading.
- Saving the branding, the logo upload and removal and the colour reset are now shared functions (`ui_extension.save_branding`/`store_logo`/`clear_logo`, `workflow_ui.reset_color`), used by both interfaces; the classic forms behave as before.
- **API.** All routes need `users.manage`; the `POST` routes also need the CSRF header:
  - `GET /api/ui/v1/admin/branding`;
  - `POST /api/ui/v1/admin/branding`, `/logo` (multipart), `/logo/remove`, `/colors/{primary|sidebar|highlight}/reset`.

## 0.49.129 — 2026-10-14

New interface (React) — NVD source and RouterOS catalogue, phase 6i
- **NVD advisories in `/app/admin/integrations/nvd`**, with sub-tabs between the configurable integrations (those not yet migrated open the classic pages):
  - configuration: enable, update interval, API key (encrypted, kept when empty, removable, never sent back to the browser);
  - source status: last outcome, last success, mode, next attempt, imported advisories, open matches, last read, unparseable records. *Update now* and *Full reload* are offered when the source is enabled;
  - matches with the inventory, with *Recompute now* and the matching rules;
  - **coverage per vendor**: devices, assessable, without model, without readable version, NVD products. On the classic page this table was placed inside the page title and did not render; here it is in its own section.
- **RouterOS catalogue in `/app/integrations/routeros`** (`firmware.read`): latest version per channel with security fixes as evidence, observed releases with changelog. *Refresh now* is for administrators and reports channels read, new releases and devices with an update.
- The integrations hub links to both pages in the new interface.
- NVD settings, sync and matching and the catalogue refresh are now shared functions (`advisory_admin.save_source`/`sync_now`/`match_now`, `routeros_catalog.refresh_now`), used by both interfaces; the classic forms behave as before.
- **API.**
  - `GET /api/ui/v1/admin/integrations/nvd` (`users.manage`);
  - `POST /api/ui/v1/admin/integrations/nvd`, `/sync`, `/match` (`users.manage` + CSRF header);
  - `GET /api/ui/v1/integrations/routeros` (`firmware.read`);
  - `POST /api/ui/v1/integrations/routeros/refresh` (`users.manage` + CSRF header).

## 0.49.128 — 2026-10-14

New interface (React) — syslog server, phase 6h
- **Syslog server administration in `/app/admin/syslog`**, in the *Syslog* tab of the new administration area.
- **Overview:**
  - receiver state;
  - lines stored today and from how many devices;
  - lines received, stored and discarded since the last restart;
  - recognition keys;
  - discarded lines by reason;
  - a warning when no allowed network is configured.
- **Settings:**
  - address for the devices, retention of info/notice/debug, allowed networks (one CIDR per line, invalid ones reported), automatic configuration on MikroTik devices with the agent;
  - key-only mode stays mandatory;
  - *Configure all MikroTik devices now* queues the configuration and reports how many devices were queued or skipped.
- **Discarded senders:** count, last line and reason, with *Remove from the list*.
- **Protection:** dedicated database account, container, TLS, integrity, and the host firewall script built from the allowed networks. The per-vendor instructions remain on the classic page and in each device's Syslog tab.
- Saving the settings, clearing a sender and configuring every MikroTik are now shared functions (`device_logs.save_syslog_settings`/`clear_unknown_source`, `mikrotik_syslog_config.configure_all_devices`), used by both interfaces; the classic forms behave as before.
- **API.** All routes need `users.manage`; the `POST` routes also need the CSRF header:
  - `GET /api/ui/v1/admin/syslog`;
  - `POST /api/ui/v1/admin/syslog/settings`, `/unknown/clear`, `/configure-all`.

## 0.49.127 — 2026-10-14

New interface (React) — integrations hub, phase 6g
- **Integrations in `/app/integrations`**, also reachable from the sidebar of the new interface. One card per data source, with the connector's real state, as on the classic hub:
  - MikroTik agent (registered, without heartbeat);
  - UISP (linked devices, last sync, consecutive errors);
  - public API (active keys);
  - RouterOS catalogue;
  - NVD advisories (imported, last update);
  - GenieACS (linked and pending CPEs);
  - cnMaestro (Cambium devices, linked);
  - Zabbix (version, devices pushed, link to the Zabbix frontend).
- **Links.** Administrators get *Configure* or *Manage* on each connector, which opens its configuration on the classic page. The agent fleet, firmware, vulnerability and API key links open the new interface.
- The hub state is now a shared function (`integrations_hub.hub_state`), used by both interfaces; the classic page behaves as before.
- **API.** `GET /api/ui/v1/integrations` (any signed-in user, as the classic hub); connector secrets are never returned.

## 0.49.126 — 2026-10-14

New interface (React) — notifications, phase 6f
- **Notifications in `/app/admin/notifications`**, in the *Notifications* tab of the new administration area.
- **E-mail server (SMTP):**
  - server, port, encryption, user, password, sender, sender name and portal address;
  - the password is encrypted and is kept when the field is left empty; the stored value is never sent back to the browser.
- **Delivery status (24 hours):** sent, queued (with those stuck for over an hour), failed, and active recipients. *Send test e-mail* goes to the given address or to the administrator's e-mail and shows the outcome; the last test is also shown.
- **Telegram and Slack:**
  - the Telegram bot token is encrypted and kept when the field is left empty;
  - saving verifies the bot and shows its name or the error;
  - Slack needs no setup.
- **Delivery log:** the last 100 messages, filtered by all, queued, sent or failed, with recipient, channel, category, severity, attempts and error. *Queue failed deliveries again* puts them back in the queue.
- SMTP settings, the test e-mail, requeueing and the Telegram bot are now shared functions (`notification_delivery.save_smtp_settings`/`test_smtp_send`/`requeue_failed`, `notification_chat.configure_bot`), used by both interfaces; the classic forms behave as before.
- **API.** All routes need `users.manage`; the `POST` routes also need the CSRF header:
  - `GET /api/ui/v1/admin/notifications`;
  - `POST /api/ui/v1/admin/notifications/smtp`, `/smtp/test`, `/telegram`, `/retry`.

## 0.49.125 — 2026-10-14

New interface (React) — system status, phase 6e
- **System status in `/app/admin/system`**, in the *System* tab of the new administration area. It is read-only and shows the same data as the classic page:
  - version, schema revision, database size, free backup space;
  - the worker with its heartbeat and each task's last success, duration, last error and consecutive failures;
  - connectors and data sources;
  - capacity: backup archive, 30-day growth, estimated days left, largest tables, retention per data set;
  - encryption of secrets with the current and previous key;
  - platform database dumps and restore drills.
- The same warnings as the classic page appear when something needs action, for example little free space, a stopped worker, secrets to rotate or unreadable, an old dump or an overdue drill.
- Retention entries and secret groups now carry a stable `key` (and the retention days), so the new interface can show them in every language; the classic page is unchanged.
- **API.** `GET /api/ui/v1/admin/system` (`users.manage`).

## 0.49.124 — 2026-10-14

New interface (React) — API keys, phase 6d
- **API keys in `/app/admin/api-keys`**, in the *API Keys* tab of the new administration area.
- **Create** a key for all customers or one, with an expiry of 30, 90 or 365 days or none, and the read permissions to grant (inventory is always included). The token is shown once, with a copy button; the platform keeps only its fingerprint, and the API answers with `Cache-Control: no-store`.
- **Each key shows:**
  - prefix, permissions, customer, creation, expiry, last use with address, number of requests;
  - its state: active, being replaced, expiring, expired, replaced, revoked;
  - the same review hints as the classic page (expiring, never used, unused for 90 days, no expiry);
  - a summary of the keys that need attention appears above the list.
- **Rotate** with an overlap period (revoke now, 24 hours, 7 days); the new token is shown once.
- **Revoke and delete** with confirmation; a key must be revoked before it can be deleted.
- Key creation, revocation, deletion and rotation are now shared functions (`api_keys.create_key`/`revoke_key`/`delete_key`, `api_key_lifecycle.rotate`), used by both interfaces; the classic forms behave as before. The duplicated creation code in `api_key_scope_extension` now uses the same function.
- **API.** All routes need `users.manage`; the `POST` routes also need the CSRF header:
  - `GET /api/ui/v1/admin/api-keys`;
  - `POST /api/ui/v1/admin/api-keys`;
  - `POST /api/ui/v1/admin/api-keys/{id}/rotate`, `/revoke`, `/delete`.

## 0.49.123 — 2026-10-14

New interface (React) — users, phase 6c
- **Administration in `/app/admin/users`**, also reachable from the sidebar of the new interface. The administration tabs are the same as on the classic page; the ones not yet migrated open the classic pages.
- **Accounts:** each account shows its role, status, e-mail, last sign-in, two-step verification and visible customers.
- **Actions** (same rules as the classic page):
  - change role;
  - choose visible customers (none = all);
  - disable or re-enable;
  - sign out every session;
  - reset two-step verification;
  - an administrator cannot demote, disable, sign out or reset themselves from here.
- **Create a user** with a temporary password; it must meet the platform requirements, and a duplicate username is refused.
- **Also on the page:** failed sign-ins in the last 24 hours, with the throttling limits and remaining block time, and the description of each role.
- User creation, role and status changes, customer scope, session sign-out and second-factor reset are now shared functions (`main.create_user_record`/`toggle_user_record`/`change_user_role_record`, `customer_scope.set_user_scope`, `login_security.admin_revoke`, `two_factor.admin_reset_record`), used by both interfaces; the classic forms behave as before.
- **API.** All routes need `users.manage`; the `POST` routes also need the CSRF header:
  - `GET /api/ui/v1/admin/users`;
  - `POST /api/ui/v1/admin/users`;
  - `POST /api/ui/v1/admin/users/{id}/role`, `/toggle`, `/scope`, `/sessions/revoke`, `/2fa/reset`.

## 0.49.122 — 2026-10-14

New interface (React) — reports, phase 6b
- **Reports in `/app/reports`**, also reachable from the sidebar of the new interface.
- **Generate:** a PDF or CSV report for all customers or one, over a period of up to 366 days. The checks are the same as on the classic page: the period cannot end in the future and the end cannot precede the start. After generation the page shows the SHA-256 and a download link.
- **Schedules:**
  - list with frequency, format, scope, external recipients and the outcome of the last run;
  - create, suspend or resume, change recipients, delete (with confirmation);
  - archived reports stay after a schedule is deleted.
- **Archive:** newest first, with author (or *NSM schedule*), period, format, size, SHA-256 and summary. Downloads use the classic route, which re-verifies the SHA-256.
- Report generation and schedule actions are now shared functions (`reports.generate_checked`, `report_schedules.create_schedule`/`toggle_schedule`/`set_recipients`/`delete_schedule`), used by both interfaces; the classic forms behave as before.
- **API.** All `POST` routes need `reports.generate` and the CSRF header:
  - `GET /api/ui/v1/reports` (`reports.read`);
  - `POST /api/ui/v1/reports/generate`;
  - `POST /api/ui/v1/reports/schedules`;
  - `POST /api/ui/v1/reports/schedules/{id}/toggle`, `/recipients`, `/delete`.

## 0.49.121 — 2026-10-14

New interface (React) — activity log, phase 6a
- **Activity log in `/app/events`**, also reachable from the sidebar of the new interface. Audit events are listed newest first.
- **Filters**, the same as the classic page, all kept in the address:
  - text search, which also looks inside the details;
  - device: name, serial, MAC or IP;
  - event type, severity, outcome, customer, from/to date.
- **Rows:** each row shows the readable event name and its code, severity and outcome, the device with its manufacturer icon (or the customer), the user who acted, the source and the details.
- *Export CSV* downloads the classic export with the same filters.
- **API.** `GET /api/ui/v1/audit/events` (`audit.read`), built on the classic query (`activity_worklists._audit_query`). Customer scope applies as on the classic page.

## 0.49.120 — 2026-10-14

New interface (React) — incident detail, phase 5c
- **Incident detail in `/app/incidents/<id>`**, opened from the new incident list.
- **Header and summary:** severity, status, duration, customer, site, start, resolution, author.
- **Status:** writers can change it from the page; the resolution time is required to be after the start and not in the future, as on the classic page.
- **Devices involved:** shown with their manufacturer icon; writers can choose them among the customer's devices.
- **Root cause:**
  - the confirmed cause is highlighted, and every hypothesis shows its status, origin, evidence and rationale;
  - suggested correlations from the timeline can be recorded as hypotheses;
  - confirm and reject require a rationale, as on the classic page; reopen is also available;
  - confirming a new cause sends the previous one back to *to verify*.
- **Timeline:**
  - events recorded around the incident, filtered by everything, facts or operator notes;
  - every device is shown with its manufacturer icon;
  - the period before the start is marked;
  - writers can add a note or an action taken, now or at a past time.
- The evidence export stays on the classic page (*Export evidence* opens it).
- Status, notes, devices, hypotheses and decisions are now shared functions in `incidents`, used by both interfaces; the classic forms behave as before. Rejected input carries a code, so the new interface shows the message in the user's language.
- **API.** All actions need `incidents.write` and the CSRF header:
  - `GET /api/ui/v1/incidents/{id}` (`incidents.read`);
  - `POST /api/ui/v1/incidents/{id}/status`, `/notes`, `/devices`, `/hypotheses`, `/hypotheses/{hid}/decision`.
- **Phase 5 complete.** Action Center and incidents are now in the new interface.

## 0.49.119 — 2026-10-14

New interface (React) — incident list, phase 5b
- **Incidents in `/app/incidents`**, also reachable from the sidebar of the new interface. Incidents are listed newest first.
- **Filters:**
  - status chips with counts: active (the default), open, investigating, monitoring, resolved, all;
  - text search on title and description;
  - customer filter.
  - Every filter stays in the address.
- **Rows:** each row shows the severity, the customer, how long the incident lasted (or has lasted so far) and the confirmed root cause. The devices involved appear with their manufacturer icon; the first three are listed and the rest are counted.
- The incident detail and the *New incident* form still open on the classic pages.
- The list filters and counts are now shared functions in `incidents` (`list_scope`, `status_counts`, `status_filter`), used by both interfaces; the classic page behaves as before.
- **API.** `GET /api/ui/v1/incidents` (`incidents.read`). Customer scope applies as on the classic page.

## 0.49.118 — 2026-10-14

New interface (React) — Action Center, phase 5a
- **Action Center in `/app/action-center`**, also reachable from the sidebar of the new interface. Active issues (open and in charge) are listed most severe first, then newest.
- **Filters:**
  - severity chips with counts;
  - category chips, counted within the chosen severity;
  - status (active, open, in charge, resolved) and customer filters.
  - Every filter stays in the address.
- **Rows:** each row shows the device with its manufacturer icon, the customer, the category and when the issue was opened.
- **Actions:**
  - *Take in charge* (for users with `issues.ack`) works as the classic button and is audited the same way (`ISSUE_ACKNOWLEDGED`);
  - *Open incident* leads to the classic incident form for that device.
- Taking an issue in charge is now a shared function (`acknowledge_issue_record`), used by both interfaces; the classic page behaves as before.
- **API.** `GET /api/ui/v1/action-center` (`issues.read`), `POST /api/ui/v1/action-center/{id}/ack` (`issues.ack`, CSRF header). Customer scope applies as on the classic page.

## 0.49.117 — 2026-10-14

New interface (React) — vendor icon on every device
- **Every device shows its manufacturer icon again**, as on the classic pages: same symbols from `/static/brand-icons.svg`, same brand colours, the generic icon when the manufacturer is not known. For manually added devices, the declared manufacturer is used.
- **Where it appears:** device list and device header; Monitoring, MikroTik agents, backup overview, firmware, exposed services, vulnerabilities (affected and unassessed devices), access, lifecycle, compliance; customer graphs (devices with most traffic); diagnostics (neighbors and DHCP leases linked to NSM).
- Every device returned by the new-interface API now carries `brand` (`symbol`, `color`, `label`, `known`), computed by `vendor_cpe.icon` like the classic pages.

## 0.49.116 — 2026-10-14

New interface (React) — compliance, phase 4d
- **Compliance in `/app/compliance`**, also reachable from the sidebar of the new interface. It shows the result of each control (firmware without security updates, no unhandled severe CVE, recent backup, restore test, vendor support, NSM agent active, approved configuration, cleartext services disabled) per device, worst first.
- **Matrix:**
  - one cell per control: compliant, non-compliant, no evidence, exception, not applicable;
  - the cell shows the evidence on hover and opens the classic result page, where exceptions are handled.
- **Outcomes with counts:** non-compliant (the default, without exceptions), in exception, no evidence, compliant, all. Customer and control filters stay in the address.
- *Evaluate now* (for users who manage compliance) recalculates every device with the same rules as the classic button and reports the outcome. Baselines are still managed on the classic page.
- The compliance matrix and the evaluation are now shared functions in `compliance`, used by both interfaces; the classic page behaves as before.
- **API.** `GET /api/ui/v1/compliance` (`compliance.read`), `POST /api/ui/v1/compliance/evaluate` (`compliance.manage`, CSRF header).
- **Phase 4 complete.** Vulnerabilities, exposed services, access, lifecycle and compliance are now in the new interface.

## 0.49.115 — 2026-10-14

New interface (React) — access and lifecycle, phase 4c
- **Access in `/app/access`**, also reachable from the sidebar of the new interface. It shows failed logins found in the device syslog over 1 hour, 24 hours, 7 or 30 days:
  - the devices with most failures (opening their Syslog tab);
  - the source addresses, marking public ones and those that tried several devices;
  - the latest brute-force alerts.
- **Lifecycle in `/app/lifecycle`**, also in the sidebar:
  - devices out of vendor support (end of life, end of support), those ending within a year, and those without catalog data;
  - the last group is shown per vendor/model with *Add to catalog*;
  - search, customer and vendor filters in the address, EOL/EOS dates, replacement state (to handle, planned, exception…), linked to the device remediation and to the EOL/EOS catalog.
- The lifecycle filters and counters are now shared functions in `lifecycle_drilldown`, used by both interfaces; the classic page behaves as before.
- **API.** `GET /api/ui/v1/access`, `GET /api/ui/v1/lifecycle` (`security.read`).

## 0.49.114 — 2026-10-14

New interface (React) — exposed services, phase 4b
- **Exposed services in `/app/exposure`**, also reachable from the sidebar of the new interface. It holds the same fleet view as the classic page:
  - every device with what it exposes to the Internet, worst first;
  - the check comes from the configuration read by the MikroTik agent (raw and filter rules, port forwards) or from outside on the public IP.
- **Views with counts:** with exposed services (the default), to set up (with the reason: missing IP, agent to update…), no exposure, check in progress, all; customer filter. Views stay in the address.
- **Per device:**
  - exposed services with protocol/port and severity (sensitive port forwards included);
  - worst severity, check mode, target IP, shared or private/CGNAT address, last check.
  - The name opens the device Exposure tab.
- A banner warns when devices expose critical services.
- **API.** `GET /api/ui/v1/exposure` (`security.read`), built on `device_exposure.fleet`.

## 0.49.113 — 2026-10-14

New interface (React) — vulnerabilities, phase 4a
- **Vulnerabilities in `/app/vulnerabilities`**, also reachable from the sidebar of the new interface. It holds the same list as the classic page:
  - CVEs matching the devices, highest CVSS first;
  - severity chips with counts (severe, critical, high, medium, low, unclassified);
  - open / resolved / all, search on CVE, vendor, product or description, customer and vendor filters, and a device filter (`?device=`);
  - everything stays in the address, 100 CVEs at a time.
- **CVE page in `/app/vulnerabilities/<id>`:**
  - severity and CVSS, description, source, dates, affected versions, links to NVD, the vendor advisory and CISA;
  - the devices involved with installed and fixed version, open or resolved (with why it was closed), filtered by customer;
  - the devices of the product that cannot be assessed, with the reason.
  - The state of each finding opens the classic page, where the fix or the risk acceptance is handled.
- The list filters and severity counters are now shared functions in `security_drilldown`, used by both interfaces; the classic page behaves as before.
- The new-interface tests accept a missing frontend build for the `/app` pages, as in the CI integration job.
- **API.** `GET /api/ui/v1/vulnerabilities`, `GET /api/ui/v1/vulnerabilities/{advisory_id}` (`security.read`).

## 0.49.112 — 2026-10-14

New interface (React) — translations loaded per language, phase 3e
- **Faster first load.** The four translations no longer travel in the initial bundle. The interface downloads only the language in use (about 10 KB compressed) before showing the first page, and fetches another language only when the user switches to it. The initial JavaScript drops from about 153 KB to 122 KB compressed.
- No untranslated keys appear while switching: the language changes only once its file has arrived. The tests still check that the four languages have exactly the same keys.
- **Phase 3 complete.** Monitoring, MikroTik agents, backup overview and firmware are now in the new interface.

## 0.49.111 — 2026-10-14

New interface (React) — firmware worklist, phase 3d
- **Firmware in `/app/firmware`**, also reachable from the sidebar of the new interface. It holds the same worklist as the classic Firmware page.
- **States with counts:** to update (the default), security update, critical, unknown state, up to date, all. Search on device, vendor, model, serial or version, plus customer and vendor filters; everything stays in the address. Devices load 100 at a time (*Load more*).
- **Per device:**
  - customer and site, vendor, model and channel;
  - installed and recommended version (highlighted when newer), with the readiness note;
  - firmware state in the user's language, last check.
- **Actions, with the same rules as the classic page:**
  - *Check now* queues the read-only RouterOS check (MikroTik online). The button reports whether the check was queued, was already queued or needs the agent.
  - *Prepare upgrade* (MikroTik online with the modern agent, a newer recommended version and permission to execute firmware) and *Plan* open the classic upgrade plan.
  - Links to *Suggestions* and *Vendor catalogs*.
- The list filters and state counters are now shared functions in `firmware_worklist`, used by both interfaces; the classic page behaves as before.
- **API.** `GET /api/ui/v1/firmware` (`firmware.read`; `q`, `customer`, `vendor`, `state`, `offset`), `POST /api/ui/v1/firmware/{device_id}/check` (CSRF header).

## 0.49.110 — 2026-10-14

New interface (React) — backup overview, phase 3c
- **Backup in `/app/backups`**, also reachable from the sidebar of the new interface. It holds the same worklist as the classic Backup page.
- **Cards:** protected devices, devices blocked (policy present but method not executable), devices without a policy, open backup alerts, active policies.
- **Views with counts:** needs attention (last run failed, blocked, never run), last failed, blocked, never run, without policy, protected, all. Search on device, serial, IP or customer and a customer filter; everything stays in the address.
- **Per device:**
  - protection state in the user's language with the backup method;
  - effective policy;
  - last backup with outcome, time and error.
  - The name opens the device Backup tab.
- **Coverage per customer** (protected, blocked, without policy, alerts) linked to the customer page, and the **policies** with their scope and schedule in words. Users allowed to configure backups can edit a policy or create one (*+ Policy*) on the classic forms.
- The protection state of the fleet is now built by shared functions in `backup_workspace_capabilities`, used by both interfaces; the classic page behaves as before.
- **API.** `GET /api/ui/v1/backups` (`backup.read`).

## 0.49.109 — 2026-10-14

New interface (React) — MikroTik Agent fleet, phase 3b
- **Agents in `/app/agents`**, also reachable from the sidebar of the new interface. It is the same worklist as the classic Agent fleet:
  - agents needing attention by default, with suspect installations first;
  - chips with counts: healthy, offline, old heartbeat, no credential, pending, suspect installation, expired token, transport not detected, all;
  - search (name, IP, MAC, serial, model), customer, transport and installation filters, all kept in the address.
- **Per agent:** health with its first reason (the others in the tooltip), installation check, pending tokens, transport and version, next version available and the state of a running update, last heartbeat and source address. The name opens the device Agent tab.
- **Summary:** total agents, healthy, modern and legacy, and how many installed agents can be updated (devices still waiting for their first pairing are not counted).
- The fleet rows are now built by shared functions in `agent_fleet`, so the agent-update reasons added at start-up apply to both interfaces; the classic page behaves as before.
- **API.** `GET /api/ui/v1/agents` (`devices.read`).

## 0.49.108 — 2026-10-14

New interface (React) — fleet Monitoring, phase 3a
- **Monitoring in `/app/monitoring`**, also reachable from the sidebar of the new interface. It is the same worklist as the classic page:
  - devices needing attention by default, the worst first;
  - chips with counts: offline, agent without heartbeat, CPU ≥ 80%, free memory < 10%, no reply to the NSM ping, packet loss ≥ 20%, all;
  - filters by customer and vendor, all kept in the address.
- **Per device:** state and attention badges, agent heartbeat age, CPU and free memory (in red beyond the thresholds), uptime, last NSM ping (RTT and loss, or why it is off). The name opens the device Monitor tab.
- **NSM ping for the fleet or one customer:** how many devices are monitored and how many could be. Users who may change devices can enable or disable it after a confirmation; devices without a management IP are counted and skipped. The change is audited like the classic button.
- The worklist rows and the bulk switch are now shared functions in `fleet_monitoring`, used by both interfaces; the classic page behaves as before.
- **Lighter first load.** The new sections (Monitoring from now on) load only when opened; the initial bundle is about 149 KB compressed.
- **API.** `GET /api/ui/v1/monitoring`, `POST /api/ui/v1/monitoring/icmp` (CSRF header).

## 0.49.107 — 2026-10-14

New interface (React) — customer graphs and availability, phase 2c
- **Graphs tab in `/app/customers/<id>/graphs`** for the whole customer or one site (selector, or *Graphs* from the Sites tab). Range 24 hours, 7 days, 30 days or 90 days; site and range stay in the address.
- **Six uPlot charts**, each with gaps where data is missing, values under the cursor and a current/min/avg/max legend:
  - aggregated WAN traffic of the monitored interfaces;
  - devices sending telemetry (whole numbers on the axis);
  - average and maximum CPU;
  - radio signal (UISP, cnMaestro, MikroTik);
  - latency and packet loss from the NSM ping.
- **Sites compared** over the last 24 hours (devices online, average in/out, peak; devices without a site on their own row). Clicking a site narrows every chart to it.
- **Devices with most traffic** (24 hours) linked to their Monitor tab, and **availability over 30 days**: average, devices below the target, the 15 least available with downtime, outages and RTT.
- Counter charts (devices, errors) now use whole-number steps on the axis, in the device Monitor tab too.
- **API.** `GET /api/ui/v1/customers/{id}/graphs/summary` (`monitoring.read`). The charts reuse `/api/v1/customers/{id}/graphs`.
- **Phase 2 complete.** Customer list, customer page with overview, sites and graphs in the new interface; Backup, Security and Activity of the customer still use the classic pages.

## 0.49.106 — 2026-10-14

New interface (React) — customer page, phase 2b
- **Customer page in `/app/customers/<id>`**, opened from the customer list and from the device breadcrumb. It has the header (code, sites, devices, notes), buttons shown only when allowed (Incidents, Manage customer, + Device) and the same tabs as the classic page.
- **Overview:**
  - six cards: devices online, devices with severe CVEs, firmware to update, open issues, devices protected by backup (with blocked and without-policy counts), devices out of support;
  - quick links to the device list filtered on the customer (offline, severe CVEs, to update, EOL);
  - the latest open issues (linked to their device) and recent activity.
- **Sites:** each site with address and notes, devices with online count and offline devices (both open the filtered device list), and links to graphs and edit. A search box appears when there are more than five sites; devices without a site are counted.
- The *Devices* tab opens the device list filtered on the customer. Graphs, Backup, Security and Activity still use the classic pages.
- **API.** `GET /api/ui/v1/customers/{id}` (`customers.read`; customers outside the user's scope are not found).

## 0.49.105 — 2026-10-14

New interface (React) — customer list, phase 2a
- **Customers in `/app/customers`**, also reachable from the sidebar of the new interface.
- **Per-customer health on one row:**
  - devices with the online count, offline devices, firmware to update, devices with severe CVEs;
  - open Action Center alerts, with backup alerts noted apart.
  - Each number opens the device list already filtered (customer + status, firmware or severe CVEs); alerts open the Action Center of the customer.
- **Chips** for all, with alerts, with offline devices, with severe CVEs, with firmware to update, each with its count. Text search on name and code.
- **Sorting** on every column: counters start from the largest. Filters and sorting stay in the address, so links and the back button restore the same view.
- Users limited to some customers see only theirs; *+ Add customer* appears only with permission.
- **API.** `GET /api/ui/v1/customers` (`customers.read`; `q`, `filter`, `sort`, `dir`).

## 0.49.104 — 2026-10-14

New interface (React) — Diagnostics tab, phase 1d
- **Diagnostics tab in `/app/devices/<id>/diagnostics`** for MikroTik: ping, traceroute, neighbors, DHCP lookup, device log and support snapshot.
- **Launching a diagnostic** uses the same checks as the classic page:
  - allow-listed operations only; target, source and query are validated;
  - the device must be online and the user needs permission to change devices;
  - traceroute is hidden on RouterOS 6; the support snapshot needs a modern agent or legacy 0.49.3+.
- **Recent results** (latest 25), refreshed every 3 seconds while something runs. Each has an outcome in the user's language (loss and average latency, hops reached, neighbors, leases, serious log lines) and expands to its details:
  - ping replies with min/avg/max and jitter;
  - traceroute hops with loss and times;
  - neighbors and DHCP leases linked to the NSM device when the MAC is known;
  - device log by severity.
- JSON export for every completed diagnostic; the support snapshot opens its classic result page.
- **API.** `GET /api/ui/v1/devices/{id}/diagnostics`, `POST …/diagnostics/{diagnostic}` (CSRF header, JSON body `target`/`source`/`query`).
- **Phase 1 complete.** Overview, Monitor, Syslog, Backup, Agent and Diagnostics now open in the new interface. Exposure, Configuration, Firmware, Compliance and Activity still use the classic pages.

## 0.49.103 — 2026-10-14

New interface (React) — Agent tab, phase 1d
- **Agent tab in `/app/devices/<id>/agent`** for MikroTik.
- **Cards:** installation state, transport, agent version with credential state, last heartbeat (marked when stale). Installation problems are shown with their explanation; a critical state also names the RouterOS log command to check.
- **Agent update:** installed and latest version, outdated or current, automatic or one-time reinstall, last update state, RouterOS permissions, script drift. *Update to x.y.z* queues the self-update (`devices.enroll`); while an update is running the page refreshes every 15 seconds.
- **Installation check:** token → one-shot pairing → per-device credential → heartbeat after installation, each with its time.
- **Transport and pairing details, effective functions** (heartbeat, diagnostics, snapshots, backup, telemetry, reboot, RouterOS upgrade, syslog, automatic update), credential metadata and recent enrollments.
- *Regenerate / reinstall agent* posts the classic form. The one-shot install command is still shown only on the classic page and never goes through the new API.
- **API.** `GET /api/ui/v1/devices/{id}/agent`, `POST …/agent/update` (CSRF header). The credential exposes only its dates and state, never the key or its hash.

## 0.49.102 — 2026-10-14

New interface (React) — Backup tab, phase 1d
- **Backup tab in `/app/devices/<id>/backups`**, for every vendor.
- **Run bar:** backup method of the device and *Back up now*, with the same rules as the classic page (MikroTik, online, agent enrolled, effective policy, nothing already queued). When the button is unavailable, the reason is shown in the user's language. While a backup is queued the page refreshes every 15 seconds.
- **Summary:** last successful backup, effective policy with its schedule in words (e.g. "every Monday at 02:30"), archive size and readable exports, last restore test.
- **Archive:** the last 100 runs with their files, filterable by file type and outcome. For each file:
  - size, SHA-256, restore-test result;
  - *Open / Diff* for `.rsc` exports, *Download*, *Restore test* (classic pages);
  - *Delete* with confirmation, for users allowed to configure backups; audited like the classic page.
- **API.** `GET /api/ui/v1/devices/{id}/backups`, `POST …/backups/run`, `DELETE …/backups/artifacts/{artifact_id}` (CSRF header).
- The immediate MikroTik backup is now queued by one shared function used by both the classic form and the new interface; behaviour is unchanged.

## 0.49.101 — 2026-10-14

New interface (React) — Syslog tab, phase 1d
- **Syslog tab in `/app/devices/<id>/syslog`**, for every vendor.
- **Today's counters:** critical/error, warning, other lines since midnight (UTC), and the last line received with its source address.
- **Live log.** The newest lines arrive every 5 seconds. The tail pauses while the browser tab is hidden or when the user presses *Pause*. Filters by severity and text; older lines load in blocks of 200. *Export CSV* keeps the active filters.
- **Device logins** (last 24 hours): failed and successful logins, the addresses with most failures, the latest 15 events.
- **Integrity check** of the warning/error hash chain on demand, showing where the chain breaks.
- **Syslog setup:** receiver state, device key and how the device is recognised, last agent configuration job. *Configure syslog with the agent* queues the job with the same rules as the classic page (`devices.write`, agent version/profile, NSM address). Each refusal reason has its own translated message. Manual setup stays on the classic page.
- **API.** `GET /api/ui/v1/devices/{id}/syslog`, `GET …/syslog/integrity`, `POST …/syslog/configure` (CSRF header). Reading never creates the device syslog key.
- UI errors from the API can now carry a code (`detail: {code, message}`) so messages are translated.

## 0.49.100 — 2026-10-14

New interface (React) — Monitor tab, phase 1c
- **Monitor tab in `/app/devices/<id>/monitor`**, with charts drawn by uPlot (fast on 30 days of samples). A single range selector (1 hour, 24 hours, 7 days, 30 days) drives every chart; data refresh every minute.
- **Resources.** CPU and memory used, 0–100%.
- **Traffic.** In (area) and out (line) for the chosen monitored interface, with the 95th percentile as a dashed line and a table with current, average, maximum, 95th percentile and volume. Errors and drops have their own chart; with agents older than 0.49.20 a note explains why it is missing.
- **Latency and loss** (ping from NSM), when ICMP monitoring is on: average/maximum/minimum RTT and packet loss, with target and range summary.
- **Wireless signal**, when the device has wireless interfaces: signal (average, best and worst client), connected clients and CCQ.
- **Charts.** Gaps where samples are missing (no invented lines), legend with current/min/avg/max, values under the mouse cursor, dates and numbers in the user's language, light and dark palettes.
- **Settings stay classic for now:** the *Interfaces and settings* button opens the classic Monitor page (monitored interfaces, ICMP thresholds).
- **Lighter first load.** The Monitor tab and uPlot download only when the tab is opened (about 25 KB compressed).
- Texts in Italian, English, French and Spanish.

## 0.49.99 — 2026-10-14

New interface (React) — device overview, phase 1b
- **Device page in `/app/devices/<id>`**, opened from the new device list. It has breadcrumbs (devices › customer › site) and the same action buttons as the classic page (reboot, open incident, manage), shown only when the user is allowed.
- **Tabs.** The same tabs as the classic page, according to the vendor (Monitor, Agent, Diagnostics… for MikroTik; UISP for Ubiquiti; cnMaestro; ACS). *Panoramica* is in the new interface; the others still open the classic page.
- **Overview.**
  - Five cards: status and last contact, firmware with the recommended version, open vulnerabilities (critical/high), backup method and last success, ping from NSM.
  - Identity: identity, management/LAN IP, address seen by NSM, MAC, serial, architecture, RouterBOOT, uptime, data source, lifecycle, agent.
  - Open Action Center issues.
  - Refreshed every minute.
- **UI API.** `GET /api/ui/v1/devices/{id}` returns all of the above in one call, with the tabs available for the device and the actions allowed for the user. A device of another customer answers 404 to a scoped user.
- Texts in Italian, English, French and Spanish.

## 0.49.98 — 2026-10-14

New interface (React) — device list, phase 1a
- **Apparati in `/app/devices`.** The first migrated section; the menu entry now opens it in the new interface.
  - Chips with counts: all, online, offline, to be enrolled, firmware to check, severe CVEs, out of support.
  - Text search (name, identity, IP, MAC, serial, model) and customer, site and vendor filters.
  - Sortable columns.
  - The filters live in the address, so a filtered list can be shared or bookmarked.
- **Performance.** Rows are loaded in blocks of 100 while scrolling and only the visible ones are drawn (virtualized), so thousands of devices stay fluid. A device opens its classic page until the device detail is migrated.
- **UI API.**
  - `GET /api/ui/v1/devices`: same filters as the classic inventory, plus `sort`/`dir`, `offset`/`limit` (max 200), counts per status within the customer/site/vendor scope, and severe CVEs per device.
  - `GET /api/ui/v1/devices/facets`: customers, sites and vendors for the filters.
  - Both respect the user's customer scope.
- Texts in Italian, English, French and Spanish; last contact shown as relative time in the user's language.

## 0.49.97 — 2026-10-14

New interface (React) — foundation, phase 0 of the gradual migration (decided with the operator)
- **What it is.** A new React interface served under **`/app`** from the same origin as NSM. It reuses the same session, login, two-factor authentication, idle timeout, permissions and customer scope. Sections arrive one at a time; those not migrated yet open the classic page, and the classic sidebar links to the preview (*Nuova interfaccia (anteprima)*).
- **Languages.** Italian, English, French and Spanish (i18next). The choice is saved per user (migration 0041, `user_preferences.language`); without a choice the browser language is used. Light and dark theme are shared with the classic interface.
- **Stack.** React 19, TypeScript, Vite, TanStack Router and TanStack Query.
  - Plain CSS with the same design tokens as `app.css`; no inline scripts or styles, so the production CSP stays unchanged.
  - Dependencies are pinned with a lockfile. The initial bundle is about 124 KB gzip.
- **UI API `/api/ui/v1`.** `GET /session` returns user, permissions, preferences, branding and CSRF token; `PATCH /session/preferences` saves language and theme.
  - Every change requires the session CSRF token in `X-CSRF-Token`.
  - Permission checks stay on the server.
- **Build and CI.** The Docker image builds the interface in a Node stage and copies it to `app/static/ui`. `/app/*` returns the shell (`no-cache`), and hashed assets are served from `/static/ui`. A new CI job `frontend` runs typecheck, unit tests, production build and an inline-script/style check.

## 0.49.96 — 2026-10-09

Customer graphs — availability over 30 days
- The *Grafici* tab of a customer, or of one site, shows **Disponibilità (ultimi 30 giorni)** from the ping of NSM:
  - weighted average and devices below 99%;
  - the 15 least available devices, with availability, downtime, outages and average RTT.
- Same calculation as the report section. Without the ICMP monitor the panel explains how to enable it.

## 0.49.95 — 2026-10-09

Reports — availability measured by the ping from NSM
- **New report section.** Periodic and on-demand reports have **10. Disponibilità (ping da NSM)** (*Apparati* becomes section 11). For each device with the ICMP monitor in the period:
  - availability %, downtime, number of outages and the longest one;
  - packet loss and average RTT;
  - worst first, with the weighted average and the devices below 99%.
- **How it is counted.** A round (3 echoes every 2 minutes) is up when at least one reply arrives. The 10-minute consolidated points count for the rounds they represent.
  - Devices without the monitor are not listed: NSM never invents an availability figure.
- **CSV.** The export has a new last column, `availability_pct`.

## 0.49.94 — 2026-10-09

Monitoring — latency and loss thresholds per device
- **Thresholds.** The *Latenza e perdita* panel accepts two optional thresholds: average RTT in ms and loss in %. They are evaluated over the last 30 minutes, with at least 5 rounds.
- **Alert.** When exceeded, *Latenza o perdita oltre soglia* opens in the Action Center with a notification and the measured values. It is updated while the problem lasts, and closes when the last 30 minutes are back within the thresholds or the thresholds are removed.
- The new *Latenza* category has its label in the Action Center and is a connectivity candidate for incidents.

## 0.49.93 — 2026-10-09

Vulnerabilities — Huawei and Cisco version schemes (SEC-05)
- **Huawei VRP** versions such as `V200R019C10SPC800` (with optional `SPH` hot patch) are read and compared by version, release, customization, service pack and patch. Before, they were not interpretable, so these devices could not be evaluated. Dotted versions of other Huawei products still work.
- **Cisco versions.**
  - IOS `15.2(4)M3` / `12.2(55)SE12`, ASA `9.12(4)18`, NX-OS `9.3(8)` and IOS-XE `17.3.4a` are parsed with their rebuild numbers and letters.
  - Versions of different release trains (e.g. `M` and `E`) are never ordered against each other, so they never produce a false match.
- **Cisco OS family** (`ios`, `ios_xe`, `ios_xr`, `nx-os`, ASA) comes from the firmware text, else from the model (ISR4/Cat9 → IOS-XE, Nexus → NX-OS, ASA/Firepower → ASA, Catalyst/ISR classic → IOS). Nothing is inferred from the brand alone.

## 0.49.92 — 2026-10-09

Incidents — ping evidence from NSM
- **Timeline.** A new *Misura (ping da NSM)* source lists, for each involved device with the ICMP monitor, every period without replies: start, approximate duration, target, failed rounds, and when it answered again. These entries are observed facts.
- **Correlation candidates.** A ping outage between one hour before and 15 minutes after the start is a *connectivity* candidate with medium confidence. As always, it becomes a root cause only after the operator confirms it.
- Issues of the categories *Raggiungibilità* and *Errori interfacce* are connectivity candidates; *Esposizione* issues are security candidates.

## 0.49.91 — 2026-10-09

Monitoring — alert on interface errors (MON-01)
- **What it checks.** Every 10 minutes the worker sums the receive and transmit errors of each monitored interface over the last hour (Agent 0.49.20+).
- **Opening.** At 50 errors or more (`INTERFACE_ERROR_ALERT_PER_HOUR`, `0` disables) the device gets *Errori sull'interfaccia* in the Action Center, with a notification. It lists the interfaces and points to the Monitor page: check cable, port, duplex or radio signal.
- **Closing.** One issue per device; it closes by itself when every interface is below the threshold.
- **Drops never alert.** Queues and shaping drop packets on healthy links; drops stay on the graph.
- The Action Center shows Italian labels for the *Esposizione*, *Raggiungibilità* and *Errori interfacce* categories.

## 0.49.90 — 2026-10-09

Monitoring — ping from NSM on the fleet page and for a whole customer
- **Operazioni → Monitoring** shows the ICMP state of every device in a *Ping da NSM* column (last RTT and loss, *non risponde*, *non attivo*).
  - Two new views: *Non rispondono al ping* (three silent rounds) and *Perdita pacchetti* (last round with at least 20% loss). They also count in *Da verificare*.
- **Bulk switch.** *Attiva su tutti* / *Disattiva su tutti* turns the ICMP monitor on or off for every device of the selected customer, or of all customers.
  - Devices without a management IP are skipped and counted, with a hint to set the address on the device page.
  - Audited as `ICMP_MONITOR_BULK`.

## 0.49.89 — 2026-10-09

Monitoring — every MikroTik interface can be monitored (fix requested by the operator)
- **Bug.** On some routers only the bridges could be picked as monitored interfaces; no Ethernet LAN ports, no PPP, nothing else. The agent collector read the interfaces in two groups, each inside a single `:do on-error`. One interface whose counters could not be read stopped its whole group. It also skipped ports without link and all dynamic interfaces (PPP server sessions), and its size cap was small.
- **Agent 0.49.22** reads each interface in its own `:do on-error`, in this order:
  1. WAN clients;
  2. Ethernet ports, also without link;
  3. every other static interface (VLAN, bridge, wireless, tunnels, bonding);
  4. active dynamic interfaces (`<pppoe-user>` sessions).
  
  Caps rise to 2400 characters (legacy header) and 12000 (modern). NSM keeps up to 128 interfaces. The agent reaches routers through the automatic update.
- **Picker.** The *Interfacce monitorate* list groups each interface (WAN, Ethernet, VLAN, Bridge, Wireless, Tunnel, Sessione PPP, Altro) and orders them that way.

## 0.49.88 — 2026-10-09

Diagnostics — download as JSON evidence (MTK-05)
- Every completed diagnostic (support snapshot, ping, traceroute, neighbors, DHCP, logs) has a **Scarica JSON** button on its result page. Use it to attach the snapshot to a ticket.
- The file contains the device identity (name, identity, model, serial, RouterOS, management IP), the job (type, parameters, status, times, error) and the stored result.
- The file's SHA-256 is returned in `X-Content-SHA256` and recorded in the audit event `DIAGNOSTIC_EXPORTED`. Read permission is enough, within the user's customer scope.

## 0.49.87 — 2026-10-09

Customer and site graphs — latency, loss and MikroTik radios
- The *Grafici* tab of a customer (or one site) adds:
  - **Latenza (ICMP da NSM)**: average and worst round-trip time of the devices with the ICMP monitor;
  - **Perdita pacchetti**: loss per slot over all their rounds.
- **Segnale radio** now also includes the MikroTik radios read by the agent (worst client of each AP), next to UISP and cnMaestro.

## 0.49.86 — 2026-10-09

Monitoring — wireless signal of MikroTik radios (MON-01)
- **Agent 0.49.21** (modern, legacy 7.12 and RouterOS 6) reads the wireless registration table at every heartbeat:
  - `/interface wireless` (RouterOS 6 and the v7 *wireless* package): signal and CCQ;
  - `/interface wifi` (RouterOS 7.13+ *wifi* package): signal.
- **Routers without a radio are safe.** RouterOS refuses to load a script that names a menu of a missing package. Both readers are therefore compiled at run time with `:parse` inside `:do on-error`, and RouterOS 6 gets only the *wireless* reader.
  - A router without radios sends `v1;`, and nothing is stored.
- **Per interface.** NSM aggregates per heartbeat: registered peers (one on a CPE), min/avg/max signal and average CCQ (`device_wireless_samples`, migration 0040; 90-day retention; 10-minute consolidation after 7 days).
- **Monitor page.** New *Segnale wireless* panel with an interface selector:
  - signal chart (dBm): a single line on a CPE; average with best and worst client on an AP;
  - connected clients and CCQ chart;
  - current value in the header.

## 0.49.85 — 2026-10-09

Monitoring — latency and packet loss measured from NSM (MON-01, any vendor)
- **ICMP from the NSM server**, like the Zabbix/Cacti ping item. Enabled per device, it sends 3 echo requests every 2 minutes to the management IP, or to an address you choose (IPv4/IPv6; loopback, link-local and multicast refused).
  - It works for every manufacturer, with or without agent, when NSM reaches the address (public IP, VPN or management network).
  - It uses Linux unprivileged ping sockets, which Docker enables by default, so the worker keeps `cap_drop: [ALL]`.
- **Charts.** New *Latenza e perdita* panel on the MikroTik Monitor page and on the overview of other devices:
  - round-trip time (average area, min/max lines, legend table) and packet loss %;
  - ranges 1h/24h/7d/30d.
- **Alerts.** Three consecutive rounds without reply open *Apparato non raggiungibile da NSM (ICMP)* in the Action Center, with a notification. The next reply closes it, and so does disabling the monitor.
- **Storage.** `device_ping_samples` (migration 0039), 90-day retention; the consolidation after 7 days averages RTT and sums packets.
- If the host does not allow ping sockets, the panel says so and nothing is stored. `ICMP_MONITOR=0` turns the feature off.

## 0.49.84 — 2026-10-09

Documentation — capabilities and roadmap brought in line with the code (audit of half-done work)
- `IMPLEMENTED_CAPABILITIES.md` still said legacy backup was not implemented and did not mention the newer agent features. It now covers:
  - automatic and legacy self-update, observed policies, 2-minute heartbeat, several jobs per heartbeat, the Agent tab capabilities and the RouterOS 6 onboarding command;
  - the FTP backup, the legacy support snapshot, interface errors and drops, and the consolidation.
- `ROADMAP.md`: MTK-02 (legacy backup) marked done; MTK-03 report integration done; MTK-05 legacy support snapshot done.

## 0.49.83 — 2026-10-09

Monitoring — interface errors and drops (MON-01)
- **Agent 0.49.20** (modern, legacy 7.12 and RouterOS 6) adds the RouterOS `rx-error`, `tx-error`, `rx-drop` and `tx-drop` counters to every heartbeat. It sends only interfaces with a non-zero counter, so the payload stays small (`metrics.iferrs` / `X-NSM-Iferr`). It reaches paired routers through the automatic update.
- **Stored per sample.** NSM stores the errors and drops of each monitored interface in the interval since the previous sample (migration 0038). Counter resets are ignored.
  - Consolidation after 7 days sums them per 10-minute slot.
- **Monitor page.** New *Errori e drop* chart under the traffic graph (Cacti/Zabbix style), with the totals for the selected range. Devices with an older agent show when the data will arrive.
- The traffic API returns `rx_errors`, `tx_errors`, `rx_drops`, `tx_drops` per point and `stats.errors`.

## 0.49.82 — 2026-10-09

MikroTik Agent — legacy agents run several jobs per heartbeat; support snapshot on legacy (audit of half-done work)
- **Several jobs per heartbeat.** **Legacy Agent 0.49.19** polls again after each completed job, up to 8 jobs per heartbeat. Queued operations (snapshots, diagnostics, syslog, backup) no longer wait one heartbeat each. It reaches paired routers through the automatic update.
- **Support snapshot on RouterOS 6.48/6.49 and 7.12.** Before, the request failed on legacy agents ("non ancora disponibile"). For agents with structured snapshots (0.49.3+), NSM now splits it into the read-only sections the agent already runs: resources, addresses, routes, interfaces, PPP, DHCP, logs.
  - It assembles them in the modern support snapshot shape (50 rows per table, like the modern agent).
  - A section that never arrives is listed as missing when the snapshot expires; the snapshot still closes with what it has.
- The *Support snapshot* card in Diagnostics is shown for those legacy agents too.

## 0.49.81 — 2026-10-09

MikroTik Agent — capabilities and texts that match what legacy agents do (audit of half-done work)
- **Agent tab, *Capability effettive*.** Four new rows, for modern and legacy agents:
  - *Riavvio remoto* and *Aggiornamento RouterOS*: from the privilege profile; a read-only legacy agent is told to reinstall once;
  - *Configurazione syslog*: from the agent eligibility;
  - *Aggiornamento agent automatico*: from the update policy.
  
  The backup row names the real method (for example *Agent MikroTik legacy (FTP)*), no longer always "Backup HTTPS". On RouterOS 6 the diagnostics row says traceroute is not available.
- **Legacy backup messages.** Export-only and not executable no longer say "the binary backup requires RouterOS 7.13+". They now say what the full FTP backup needs: the receiver (`./manage.sh legacy-backup-enable`), agent 0.49.16+ and the policies.
- **Other texts.** The Agent fleet filter reads *Legacy 6.x / 7.12*. The workspace legacy note covers RouterOS 6.48/6.49. ROADMAP MON-01 now reflects the 2-minute sampling and the consolidation.

## 0.49.80 — 2026-10-09

Onboarding — command for RouterOS 6.48/6.49
- **Fix.** The guided pairing command refused anything but RouterOS 7 and contained v7-only syntax (`/system/device-mode/get`). RouterOS 6 rejects a whole command line that contains it. So routers with 6.48/6.49 could not be added from the panel, although NSM installs the RouterOS 6 legacy agent.
- **Change.** The onboarding panel shows a second command, *Router con RouterOS 6.48 / 6.49*, using the same one-shot token. It checks the version (and DNS when NSM uses a hostname), then fetches the same bootstrap.
- The RouterOS 7 command, verified on real routers, is unchanged.

## 0.49.79 — 2026-10-09

Syslog — configured automatically when a MikroTik is added through the agent (requested by the operator)
- **At enrollment.** When a MikroTik completes the agent pairing, NSM queues the syslog configuration right away. This covers modern agents, legacy 7.12 and RouterOS 6. The agent applies it at its first heartbeat, seconds after the bootstrap:
  - logging action `nsm` toward NSM, with topics critical, error, warning and account;
  - the device key as prefix, so its logs are accepted from the first line.
- **Default on.** The *Configura automaticamente* setting (Amministrazione → Syslog) is already enabled by default and now also covers enrollment. Turning it off stops both.
- **NSM address.** Without a configured IPv4 address, enrollment uses the address the router used to reach NSM. When neither is usable, the device records `SYSLOG_AGENT_CONFIG_SKIPPED` with the reason.
  - Loopback, unspecified and link-local addresses are never sent to routers.
- **Fix.** The worker's periodic auto-configuration skipped legacy agents, because the worker does not load the web wiring. It now applies the legacy eligibility too.
- The onboarding panel and the Syslog settings say that syslog is configured right after pairing.

## 0.49.78 — 2026-10-09

MikroTik Agent — heartbeat every 2 minutes (requested by the operator)
- **New installations** schedule the agent every **2 minutes** instead of 5. Jobs (reboot, syslog, diagnostics, updates) start within 2 minutes; graphs and offline detection are finer.
- **Installed agents.** **Agent 0.49.18** aligns its own scheduler to 2 minutes at every run, and reaches paired routers through the automatic update. Read-only agents cannot change the scheduler and keep 5 minutes.
- **Server load.** A heartbeat is one small POST; legacy agents add one job poll. 300 routers make about 2.5 requests per second.
- **Storage.** Telemetry is consolidated like an RRD archive (`telemetry_rollup.py`, hourly). Full 2-minute resolution is kept for 7 days. Older CPU/memory and traffic samples become one 10-minute point (average of CPU and bit/s, newest counters and memory).
  - Over the 90-day retention a series stores about 17,000 points instead of the 25,900 of the 5-minute heartbeat.

## 0.49.77 — 2026-10-09

MikroTik Agent — legacy agents update themselves (requested by the operator)
- **Self-update on RouterOS 6.48/6.49 and 7.12.** Legacy **Agent 0.49.17** runs the `agent_self_update` job, like modern agents do. Paired routers get new versions without a new token.
  - The router downloads the source NSM generates for that device.
  - It checks that the download ends with the expected marker (no truncation) and that RouterOS parses it (`:parse`).
  - It keeps the old script as `nsm-agent-heartbeat-prev`, then replaces its own script. The new version runs at the next heartbeat; NSM verifies it from the reported version.
  - An agent that answers without running the handler counts as a failure, and nothing changes.
- **Automatic updates.** The worker queues the update for online agents that can apply it: modern 0.49.0+, and legacy 0.49.17+ with the `write` policy.
  - One update at a time. After a failure, at most one attempt a day.
  - `AGENT_AUTO_UPDATE=0` turns it off.
- **7.12 → 7.13+.** When a router with a legacy agent moves to RouterOS 7.13+, the update installs the **modern agent**. The first modern heartbeat switches the transport (`MIKROTIK_AGENT_TRANSPORT_SWITCHED`).
- **Observed permissions.** The legacy agent reports the RouterOS policies of its script (`X-NSM-Agent-Policy`). NSM derives the profile from them (`legacy-ops-v1` or `legacy-read-v1`).
  - FTP backups also require `ftp`, `policy` and `sensitive`. Agents installed before 0.49.16 lack the last two.
  - RouterOS does not let a script raise its own policies. Read-only agents, and legacy agents before 0.49.17, need **one last reinstall**; after that, updates are automatic.
- **Agent tab.** New *Aggiornamento agent* panel: installed and latest version, automatic or one-time reinstall, last update state, RouterOS permissions, and an *Aggiorna ora* button.

## 0.49.76 — 2026-10-09

Inventory — onboarding through UISP and cnMaestro (requested by the operator)
- **Cambium is its own vendor** in *Nuovo apparato*: *Cambium (cnMaestro)*. Before, it was one of the generic manufacturers; it is removed from the manual list.
- **Ubiquiti (UISP) and Cambium (cnMaestro)**: the device must **already exist in the console**. NSM looks it up by **MAC or serial** when the device is created.
  - **Found once**: the device is created already linked. Name, model, serial, firmware, IP and status come from the console; for Cambium also network and tower, with statistics from the cnMaestro sync. Audited (`UISP_DEVICE_ONBOARDED`, `CNMAESTRO_DEVICE_LINKED`).
  - **Not found, ambiguous, connector missing or unreachable**: the device is **not created**, and the message says to add it to the console first.
- Ubiquiti accepts the serial as an alternative to the MAC. Customer and Site stay those chosen in NSM.

## 0.49.75 — 2026-10-09

Backup — full backups for legacy RouterOS (6.48/6.49 and 7.12) through an FTP receiver (requested by the operator)
- **What changes.** Old RouterOS cannot post a large file from a script (7.12 reads at most 60 KB, RouterOS 6 only 4 KB). The **legacy Agent 0.49.16** now uploads the **encrypted binary backup** and the **.rsc export** with `/tool fetch upload=yes mode=ftp` to an upload-only FTP receiver of NSM. Before, 7.12 had the export only up to 60 KB and RouterOS 6 had no backup.
  - **Archive.** Files are archived like every other backup: run, artifacts, SHA-256, audit, retention. Legacy devices become **protetti** in backup coverage.
  - **Encryption.** The binary backup is encrypted with the job's backup password, kept encrypted in NSM.
  - **Secrets.** On RouterOS 6 the export uses `hide-sensitive`.
- **Receiver** (`app/legacy_ftp_server.py`, Docker profile `legacyftp`, `./manage.sh legacy-backup-enable <IPv4 of NSM>`):
  - one-time account per job; only the password's SHA-256 is stored; three wrong passwords close the connection;
  - only the two expected files of the job can be stored, once each, up to 64 MB; no listing, download or other commands;
  - passive mode only; the data connection must come from the router's address;
  - port 2121 and passive 30100–30109. Open them only from the routers' networks.
- **Agent permissions.** The `legacy-ops-v1` profile gains the `policy` and `sensitive` policies needed by `/system backup save`. Legacy agents must be reinstalled (0.49.16+) to use this.
- `manage.sh` adds Compose profiles without overwriting the existing ones (`acs` and `legacyftp` together).

## 0.49.74 — 2026-10-09

Syslog — only lines carrying the device key (operator decision)
- **Key mandatory, by default and with no exceptions.** The receiver stores a line only when it carries the `NSM-<key>` of a known device: MikroTik logging prefix, Cisco origin-id, or device name for other vendors.
  - Every line without a key is discarded (reason *riga senza chiave NSM*). This also applies to lines from the device's own address or from a single, unambiguous address.
  - An unknown key is discarded as before.
- **Removed.** Attribution by source address and hostname, the per-device *Accetta solo/anche log senza chiave* switch, the *Hostname atteso* field, and the association of discarded senders to a device. In *Mittenti scartati* an address can only be removed from the list.
- **Allowed networks stay** as a first filter before the key is read.
- **The Syslog tab and the admin page** say that only lines with the key are accepted. They also say what to do when a MikroTik has not confirmed the key yet: configure syslog with the agent, or apply the manual commands.

## 0.49.73 — 2026-10-09

Security — exposure check also on private and CGNAT addresses (requested by the operator)
- **Private or CGNAT address as target.** In the *Esposizione* tab the operator can now give a private (RFC 1918) or CGNAT (100.64.0.0/10) address for devices behind the customer's NAT or reached through a VPN or management network. Before, only public IPs were accepted.
- **Warning on the page.** When the address is not public, a visible warning explains when the result is valid. The device must have direct access to the WAN, not sit behind another router or NAT, and the NSM server must reach it on that address. Otherwise the check is distorted: the upstream router answers, or nothing does.
- **Warning on the results.** Results obtained on a non-public IP carry a note on the device page and in *Security → Esposizione*.
- **Still refused.** Loopback, link-local, multicast and reserved addresses. The management IP is used automatically only when it is public; a private one must be confirmed by the operator.

## 0.49.72 — 2026-10-09

Monitoring — graphs per customer and per site (MON-01)
- **New *Grafici* tab on every customer**, for the whole customer or a single site (chips, and a *Grafici* button on each site row).
- **Charts** (Cacti/Zabbix style, 24h / 7d / 30d / 90d):
  - **Traffico WAN aggregato**: the sum of in/out bit/s of the monitored interfaces of the devices in scope. These are the WAN/PPPoE interfaces chosen on each device's Monitor page, so LAN traffic is not counted twice.
  - **Apparati con telemetria**: how many devices sent data in each time slot; a drop is an outage.
  - **CPU apparati**: average and maximum.
  - **Segnale radio**: average and worst, from UISP and cnMaestro.
- **How it is computed.** Slots are computed in SQL. A slot without data is a gap, never a zero.
- **Tables.**
  - *Apparati con più traffico* (last 24 hours), with a link to the device traffic graph.
  - For the whole customer, *Confronto sedi*: devices, online, average in/out and peak per site.
- The renderer gains an integer *count* unit for device counts.

## 0.49.71 — 2026-10-08

Administration — users limited to some customers (delegated administration)
- **What it is.** In *Amministrazione → Utenti*, each non-admin user has **Clienti visibili**. With no selection the user sees all customers, as before. With a selection the user sees only those customers.
- **Central enforcement.** While a limited user is served, every ORM query of the request is filtered (SQLAlchemy `do_orm_execute` + `with_loader_criteria`). It applies to:
  - the assigned customers;
  - every row with a `customer_id` of those customers (devices, sites, incidents, issues, notifications, audit, reports…);
  - every row of their devices (backups, jobs, telemetry, logs…).
- **What changes for the user.** Lists, counters, dashboard, search, Security and Monitoring pages, reports and APIs change accordingly. URLs of other customers' objects answer 404 (pages and actions).
- **Who is not filtered.** Administrators are never limited. Worker and agent endpoints are never filtered, because the filter is bound to the user's request.
- **New customers.** A customer created by a limited user is added to that user's scope.
- **Audit.** Changes are audited (`USER_CUSTOMER_SCOPE_CHANGED`). Migration `0037`.

## 0.49.70 — 2026-10-08

Integrations — cnMaestro for Cambium radios (VEND-02, step 1)
- **New connector *Integrazioni → cnMaestro*** (On-Premises or Cloud, read-only).
  - **Connection.** URL, client id and client secret of the cnMaestro *API Client*, with OAuth2 client credentials. The secret is encrypted. Test, *Sincronizza ora*, and automatic sync every 15 minutes.
  - **Linking.** NSM devices whose manufacturer is *Cambium Networks* are linked by MAC and, without a MAC, by serial. NSM then updates the model, firmware, management IP (only when missing), status and last seen. Customer and Site stay those of NSM. Linking is audited.
  - **Statistics.** Signal, SNR, downlink/uplink throughput, CPU, memory, connected SMs/clients and uptime. They are read defensively, because the field names differ between ePMP, PMP 450 and cnPilot.
  - **History.** Kept 90 days, with at most one sample every 4 minutes. The last sample of an offline device is kept.
- **Device *cnMaestro* tab.** Link state, network/tower, current values and Cacti-style graphs (signal, SNR, throughput, CPU/memory, stations).
- **Integrations hub.** A *cnMaestro (Cambium)* card. The connector is also in the global search.
- Endpoints follow the cnMaestro API v1; field names must be confirmed on a real cnMaestro. Migration `0036`.

## 0.49.69 — 2026-10-08

Firmware — catalogs for the other manufacturers (VEND-01)
- **New page *Firmware → Cataloghi altri produttori*.** There is one catalog per brand present in the inventory; a brand without devices has no catalog. MikroTik keeps the RouterOS catalog.
- **Ubiquiti** is filled automatically from official sources:
  - the latest version that UISP reports for each model;
  - Ubiquiti's public update service (`fw-update.ubnt.com`) for the airOS platforms found in the inventory (`XC`, `WA`, `XW`…, read from the installed firmware string). It is refreshed daily and on demand; to be confirmed from the deployed instance.
- **TP-Link, Tenda, Cambium, Huawei and the other brands.** Operators enter releases from the official download page, which is linked on the page: version, models (pattern such as `archer c6*`), date, link to the release notes, and whether the release fixes security issues. NSM never estimates versions.
- **Evaluation.** Every device of the brand is compared with the newest release for its model or platform. The state appears in the firmware worklist: *update available*, *security update* or *current*. Devices managed by UISP keep the UISP view.
- **Audit.** Adding and deleting releases is audited. Migration `0035`.

## 0.49.68 — 2026-10-08

Ubiquiti — operations through UISP (UBNT-08, step 2)
- **Riavvio.** Sent to UISP after typing `RIAVVIA` (permission *firmware.execute*). NSM records the uptime before the request and verifies the reboot on the device uptime: *verificato* when it restarts, *non verificato* after 20 minutes. Audited.
- **Backup della configurazione.**
  - **How it works.** UISP creates the backup; NSM downloads the new file and archives it like the MikroTik exports (BackupRun + artifact, SHA-256, audit), in the device backup archive.
  - **When.** On demand (*Esegui backup ora*), or by policy when the policy enables *Configurazione dal connector*.
  - **Protection.** Ubiquiti devices linked to UISP with that option now count as **protetti** in backup coverage.
- **Interfacce.** Name, type, state (disabled or unplugged), speed, MTU, rx/tx and errors, read on demand.
- **Eventi UISP.** The last 50 device events.
- **Errors.** Reboot and backup need a UISP token with write permission. 401/403 and 404 answers are explained, and a failed backup is recorded with its reason.
- Endpoints follow the UISP API v2.1 documentation; field names must be confirmed on a real console.

## 0.49.67 — 2026-10-08

Syslog — automatic configuration also with the legacy agent (RouterOS 7.12 and 6.x)
- **MikroTik Agent 0.49.15.** The legacy agent installed with the `legacy-ops-v1` profile configures remote syslog like the modern one:
  - action `nsm` toward the NSM receiver;
  - topics critical, error, warning and account;
  - the device key `NSM-<key>` as prefix.
- **How to use it.** From the *Syslog* tab (*Configura syslog con l'agent*), for all devices from *Amministrazione → Syslog*, or automatically.
- **Requirements.** `/system logging` needs the `write` policy, so read-only legacy agents (`legacy-read-v1`) and agents older than 0.49.15 must be reinstalled from the Agent tab. Until then the Syslog tab shows the manual commands, already with the key.
- **Safety.**
  - A legacy agent without the handler answers "success" with no output; that answer is recorded as a failure.
  - Strict mode is enabled only after the router confirms the configured key.

## 0.49.66 — 2026-10-08

ACS / TR-069 — fixes found in the review of unfinished work
- **The device overview no longer says "Gestione tramite ACS non ancora disponibile".** GenieACS is integrated. TR-069 devices show an *ACS / TR-069* panel: GenieACS ID, last Inform and last check, or what to do to complete the association.
- **TR-069 for every brand.** The *ACS* tab and the GenieACS association were limited to the vendor *TP-Link / TR-069*. They are now also available for Huawei, ZTE and other CPEs entered as *Altro produttore* (types router, ONT, wireless CPE, other) and for any device already managed through TR-069. Switches, APs and OLTs do not get the tab.

## 0.49.65 — 2026-10-08

Monitoring — Cacti/Zabbix-style graphs everywhere (reported by the operator)
- **One renderer for every time-series graph** (`static/rrd_chart.js`). Before, only interface traffic looked like Cacti/Zabbix; the CPU and memory graphs of the Monitor page and the UISP graphs still used the old drawing (tiny labels, points spread by index instead of time, no statistics).
  - **Time axis.** A real time axis with ticks on round times (minutes, hours, days, depending on the range and the width).
  - **Gaps.** Missing samples show as gaps instead of interpolated lines.
  - **Readability.** Labels sized to the screen, and a dashed grid.
  - **Hover.** A crosshair with a tooltip showing every series.
  - **Legend.** A Cacti-style table under each graph: *Attuale / Min / Media / Max*.
- **CPU and memory**: filled areas on a fixed 0–100% scale.
- **Traffic**: inbound as an area, outbound as a line, and the 95th percentile as a dashed line.
- **UISP**: signal in dBm drawn as a line with its own scale; capacity and resources with the legend.

## 0.49.64 — 2026-10-08

Syslog — fix: device key in the manual configuration (reported by the operator)
- **The commands in the *Syslog* tab now carry the device key.** Before, they had no identifier, so a device configured by hand was recognised only by IP.
  - **MikroTik**: every `/system logging add` line has `prefix=NSM-<key>`. The same key is set by the agent's automatic configuration.
  - **Cisco IOS**: `logging origin-id string NSM-<key>`.
  - **Other vendors**: put `NSM-<key>` in the device name (hostname / device name, e.g. `CPE-Rossi-NSM-<key>`). The receiver finds the key anywhere in the line, hostname included.
- **Key created when the tab is opened**, stable afterwards. It is shown in the tab header.
- **Manual strict mode.** For devices configured by hand, *Accetta solo log con la chiave* discards the lines without the key from that device. It is audited (`SYSLOG_STRICT_CHANGED`). MikroTik devices with the agent keep the automatic strict mode.
- The admin page shows the commands with the placeholder `NSM-<chiave del dispositivo>`.

## 0.49.63 — 2026-10-08

Reports — delivery (REP-04)
- **External recipients for scheduled reports.** A schedule can have up to 20 external e-mail addresses (management, auditors, the customer), set when it is created or changed later in *Report*. Each generated report is sent to them as an attachment through the same outbox, with the same retries.
- **Delivery evidence.** Every report delivery leaves an audit event, `REPORT_DELIVERED` or, after the last attempt, `REPORT_DELIVERY_FAILED`, with the report, recipient, channel and error. The evidence stays with the audit log, not with the outbox retention.
- Invalid addresses are refused, and changes to the recipients are audited (`REPORT_SCHEDULE_RECIPIENTS_CHANGED`). Migration `0034`.

## 0.49.62 — 2026-10-08

Integrations — Zabbix (ZBX-01, step 2)
- **Zabbix problems shown in NSM.** Every 5 minutes the worker reads, with one `trigger.get` call, the triggers in PROBLEM state on the hosts NSM pushed (monitored, not dependent, Zabbix 5.0 – 7.x).
  - **Device.** The header shows a badge *N problemi* with the worst severity, on every tab. The overview lists the problems: severity, name, since when, acknowledged or not.
  - **Monitoring.** A *Problemi Zabbix* panel lists the devices with open problems, worst first, with the count still to acknowledge.
  - **Integrazioni → Zabbix.** Shows the open problems and when they were last read.
  - **When Zabbix cannot be reached**, the last list is kept and marked as not updated. Devices are rewritten only when their problems change.

## 0.49.61 — 2026-10-08

Security — exposed services across the fleet (SCAN-01, step 3)
- **New page *Security → Esposizione*.** Every device with its exposure state:
  - **Con servizi esposti** (default view): sorted by severity, with the exposed services and port forwards;
  - **Da configurare**: no public IP to check, or the agent needs an update. The table says what is missing;
  - **Senza esposizioni**, and **Tutti**.
  - KPIs (exposed, critical, clean, to configure, pending), a customer filter, and a link to each device's *Esposizione* tab. The page is also in the global search.
- **Security newsletter.** Devices whose *Servizi critici esposti sulla WAN* issue opened in the period are listed with their services and the open total.
  - When there are no new CVEs, the newsletter is still sent for the new exposures.
- **Evidence report.** Section 3 *Vulnerabilità* adds the current exposure state of the devices in scope: counts and a table of the exposed devices with their services, severity and verification method. Section numbers do not change.

## 0.49.60 — 2026-10-08

Security — exposure check (SCAN-01, step 2)
- **Check from outside for every manufacturer.** Devices without the MikroTik Agent check (Ubiquiti, TP-Link, Cambium, generic devices, and MikroTik without the modern agent) are checked from the NSM server on their public IP.
  - **Target.** The IP given by the operator in the *Esposizione* tab (WAN/PPPoE address or public IP of the customer NAT) or, when it is public, the management IP. Private, CGNAT and reserved addresses are refused and never probed.
  - **Ports.** A fixed list per manufacturer, no port sweep:
    - common: FTP 21, SSH 22, Telnet 23, web 80/443/8080/8443, DNS 53 (open resolver), SNMP 161 (community `public`), SSDP 1900;
    - TR-069 7547 on CPE brands (TP-Link, Huawei, ZTE, Tenda, …);
    - Winbox 8291, API 8728/8729 and bandwidth-test 2000 on MikroTik;
    - Ubiquiti discovery 10001/udp.
  - **Probes.** One TCP connection per port. On UDP, one harmless request: a recursive DNS query, an SNMP read of `sysDescr.0`, an SSDP search, a UBNT discovery.
  - **When.** When the device is added, then every 24 hours, and with *Verifica ora* (at most once every 10 minutes). At most 4 devices per worker cycle. Each check is written to the audit log.
  - **Results.** They use the same tab, Action Center issue and *security* notification as the MikroTik check.
  - **Shared IP.** When the IP is shared (customer NAT), the result is shown, but no issue is opened on the device behind the NAT, because the answer comes from the edge router.
  - **Configuration.** `EXPOSURE_EXTERNAL_CHECK=0` disables the check. The check runs from the NSM server, so when the server is inside the same network, the result can differ from what the Internet sees.

## 0.49.59 — 2026-10-08

Security — exposure check
- **RouterOS raw firewall analysed** (requested by the operator).
  - **MikroTik Agent 0.49.14** adds `/ip firewall raw` to the firewall snapshot.
  - **Order.** The `raw/prerouting` table runs before connection tracking and the filter, so NSM evaluates it first:
    - a raw `drop` for traffic from the WAN (also on the service port) makes the service *protetto*, even when the filter would accept it;
    - raw `accept`/`notrack` only end the raw table, so the filter decides;
    - drops limited to source lists (blacklists) do not protect;
    - jumps or unknown interface lists make the verdict *da verificare*.
  - **Port forwards.** A forward whose public port is dropped in raw (before `dst-nat`) is shown as *protetto (raw)* and does not raise the alert.
  - **Page.** The Esposizione tab shows how many filter and raw rules were read. The "no drop" warning also considers raw/prerouting.

## 0.49.58 — 2026-10-08

ACS / TR-069
- **Integrated GenieACS linked automatically.**
  - When the integrated stack is enabled (`acs-enable`, `GENIEACS_INTERNAL_NBI_URL`) and no GenieACS is configured, NSM connects by itself to `http://genieacs-nbi:7557` on the internal Docker network. No URL needs to be typed.
  - An external GenieACS configured by the operator is never replaced; the form on the page is now *GenieACS esterno (NBI)*.
  - When the integrated stack is not active, the page says how to enable it.

## 0.49.57 — 2026-10-08

Operations
- `manage.sh` run from the Git clone (e.g. `~/nsm-platform`) now switches to the installation directory (`PLATFORM_DIR`, default `/srv/network-platform`). Before, commands such as `acs-enable` failed with `secrets/bootstrap.env: file not found`.

## 0.49.56 — 2026-10-08

ACS / TR-069
- **New *ACS* tab in Amministrazione.**
  - **Apri pannello ACS** opens GenieACS through Caddy on port 7080 (`ACS_UI_PORT`).
  - Caddy lets through only NSM administrators with a valid session (`forward_auth` to `/internal/acs-ui/auth`); others are sent to the NSM login or refused. GenieACS keeps its own login.
- **CPE setup guide**: TP-Link CWMP settings (ACS URL, periodic inform, connection request), network notes, verification in the panel, association in NSM, and authentication with `cwmp.auth`.
- **Base provisioning templates**: one button creates or updates in GenieACS, through the NBI:
  - the provision `nsm-base` (5-minute periodic inform; refresh of device info and WAN/PPPoE address; TR-098 and TR-181);
  - the presets `nsm-tplink` and `nsm-all-cpe`.

## 0.49.55 — 2026-10-08

Shared addresses (customer NAT) across services
- **Device header.** A device whose management IP is shared with other devices shows the badge *condiviso (N)*, listing the other devices.
- **Port forwards (Esposizione).**
  - From the MikroTik NAT rules already in the firewall snapshot, NSM finds the `dst-nat` rules that publish internal addresses on the WAN.
  - On the router: rule number, public port, destination and sensitive services (HTTP, SSH, Telnet, RDP, Winbox, SNMP…). Forwards of management services or of all ports raise the *Servizi critici esposti* alert.
  - On the device behind the NAT: *Raggiungibile da Internet tramite port forward*, which is the only way it is actually exposed.
- **Zabbix.**
  - Hosts sharing one address get the tag `nsm_shared_ip` and are listed in the sync result, since Zabbix on that address reaches only the edge router.
  - *Integrations → Zabbix* lists them with an *IP per Zabbix* per device (for example the LAN IP via VPN or Zabbix proxy), which is used instead of the management IP.

## 0.49.54 — 2026-10-08

Syslog — server hardening
- **Dedicated database account.**
  - `update.sh` generates `SYSLOG_DB_PASSWORD`; the `migrate` service creates and refreshes the role `nsm_syslog` on every deploy (`app/db_roles.py`).
  - The receiver can only insert and read log lines and access events, update the counters of discarded senders, and read devices and settings.
  - Users, backups, credentials and the rest of the database are out of reach.
- **Hardened container.**
  - New launcher `app.syslog_main`; filesystem read-only (`/tmp` in memory); limits of 512 MB RAM, 1 CPU and 128 processes; non-root user without capabilities.
  - Secrets the receiver never needs (`APP_SECRET_KEY`, `ENCRYPTION_MASTER_KEY`, …) are removed from its environment.
- **Host firewall.**
  - `./manage.sh syslog-firewall` generates the `DOCKER-USER` rules from the allowed networks: the published ports bypass ufw, so these rules are the only effective filter. `--apply` applies them.
  - The script refuses to run while no network is configured.
  - The admin page shows the generated rules.
- **TLS (RFC 5425)**: optional listener on 6514 when a certificate and key are mounted (`SYSLOG_TLS_CERT`, `SYSLOG_TLS_KEY`), for devices that support it.
- **Log integrity.**
  - Warning, error and critical lines are chained with SHA-256 per device (migration 0033); the worker writes the chain head into the audit log every day.
  - **Verifica integrità** in the device Syslog tab shows the first altered line, or a whole chain that no longer matches the audit anchor.
  - The incident PDF reports the integrity of the logs it quotes.

## 0.49.53 — 2026-10-08

Syslog — certain identification (decided with the operator)
- **Per-device syslog key.** With **MikroTik Agent 0.49.13** the router logs with the prefix `NSM-<key>` (a per-device key, stripped before storing).
  - Lines are recognised with certainty even when the WAN address changes, when several routers share the customer's NAT, or when routers sit in the same network.
  - Agents configured before the update are reconfigured once automatically.
- **Strict mode** (default on). Once the router has confirmed its key, keyless lines from its address are discarded, which blocks spoofing. On an address shared with a strict router, a keyless line is accepted only when its hostname matches exactly one other device.
- **Allowed networks.** The admin lists CIDR networks and packets from elsewhere are discarded before being read. While the list is empty, only addresses currently tied to a device are accepted, and a warning asks to configure it.
- **Certain or discarded.** Without a key the address must lead to exactly one device:
  - addresses learned from the agent count only while it sends heartbeats (30 minutes), so an address returned to the ISP pool no longer points at our router;
  - on shared addresses the hostname must match exactly one device; other vendors have an *expected hostname* in the Syslog tab;
  - lines that cannot be attributed are **discarded and not stored**, and the old "accept unknown senders" option is gone;
  - only counters and the reason remain (network, key, strict, ambiguous, unknown, rate, quota), never the content;
  - migration 0032 deletes lines stored without a device and the content samples of unknown senders.
- **Limits.**
  - Global rate limit of 2,000 lines per second and a daily quota of 200,000 lines per device.
  - Rate tables capped (most recently used kept) and the unknown-sender table capped, so spoofed traffic cannot exhaust memory nor lock out real devices.
- **Pages.**
  - The admin page shows lines discarded by reason, the allowed networks and strict mode.
  - The device Syslog tab shows strict mode, the expected hostname and the addresses tied with certainty.

## 0.49.52 — 2026-10-08

Security
- **Exposed services on the WAN, MikroTik** (SCAN-01 step 1, as decided: the Agent checks the router itself).
  - **Collection (Agent 0.49.12).** New read-only snapshot section `services`: `/ip service` (with custom ports and allowed addresses), remote DNS, SNMP (only whether the default `public` community is enabled, never the community names), SOCKS, web proxy, bandwidth-test, UPnP and MAC-Winbox.
  - **Evaluation.** NSM evaluates each active service against the firewall `input` rules in order, for traffic arriving on the WAN (PPPoE/LTE/tunnel clients, the interface with a public IP, interface lists such as `WAN` or `!LAN`). The verdict is *esposto*, *limitato* (allowed source addresses or lists), *protetto* or *da verificare* (custom lists, jump), with severity: Telnet, FTP, SOCKS and proxy are critical; Winbox, plain API, HTTP, open DNS resolver and SNMP with `public` are high. NSM warns when the input chain has no drop rule.
  - **When it runs.** As soon as the device is connected (agent 0.49.12+), then every 24 hours, and on demand from the new **Esposizione** tab of every device. No packet is sent to the device.
  - **Alerts.** Critical or high services exposed open an Action Center issue (*Servizi critici esposti sulla WAN*) and a *Sicurezza* notification; the issue closes itself when a later check is clean.
- Devices from other manufacturers show the Esposizione tab with the external check announced (SCAN-01 step 2).

## 0.49.51 — 2026-10-08

Integrations
- **Zabbix** (ZBX-01): NSM sends its devices to Zabbix 6.x and 7.x, as decided with the operator.
  - **Hosts.** Each device with a management IP becomes a host with technical name `nsm-…` and visible name *device · customer*, in the group `<prefix>/<customer>` (created when missing).
  - **Interface.** One interface, either Zabbix agent or SNMPv2 with a community macro.
  - **Templates.** Chosen per manufacturer (MikroTik, Ubiquiti, others). They are added and never unlinked, so templates linked by hand in Zabbix are kept.
  - **Tags and inventory.** Tags `source=nsm`, `nsm_device_id`, `nsm_customer` and `vendor`; inventory with vendor, model, serial, MAC, firmware and site.
  - **Updates.** When the IP or name changes in NSM, the Zabbix host is updated. Hosts of deleted or excluded devices are disabled, never deleted.
  - **API versions.** The version is detected with `apiinfo.version`:
    - Zabbix 7.2+ receives the token in the `Authorization: Bearer` header;
    - Zabbix 5.4–7.1 receives it in the `auth` field;
    - password login uses `username` (5.4+) or `user` (older).
  - **Settings.** *Integrations → Zabbix* sets URL, API token or username/password (encrypted), TLS check, group prefix, interface, templates per manufacturer and scope (all customers or selected ones). It offers *Test connessione*, *Sincronizza ora*, and an automatic sync every 15 minutes.
  - **Visibility.** Integrations hub card, *Zabbix* row in the system health page, and a *Dati in Zabbix* link in the device header.

## 0.49.50 — 2026-10-08

Reports and incidents
- **Syslog in the operational evidence report**: new section *8. Accessi e log di sicurezza*.
  - Devices that send syslog; failed and successful logins.
  - Access alerts by type; most targeted devices and source addresses (public or private).
  - Devices with the most error and critical lines.
  - When no syslog is received the section says it cannot be evaluated, instead of showing zeros.
  - Compliance and the device list move to sections 9 and 10.
- **Incident evidence**: the incident PDF adds *4. Log syslog degli apparati*, with the access events and the warning, error and critical lines of the incident devices inside the incident window.
- **Incident from an access alert**: *Security → Accessi* has *Apri incidente*, which pre-fills device, title, severity and description from the alert. The incident form accepts these values as pre-fill.

## 0.49.49 — 2026-10-08

UISP
- **UISP graphs** (UBNT-08 step 1): the device UISP tab shows the history read from UISP as charts.
  - Charts: radio signal (dBm), link capacity downlink/uplink, CPU and RAM, connected stations.
  - Ranges of 24h, 7 days, 30 days and 90 days, averaged into at most 400 points.
  - Sync interruptions show as holes; charts without data are not shown.
- New API `/api/v1/devices/{id}/uisp-metrics` and a reusable time-series chart component (`static/series_chart.js`).

## 0.49.48 — 2026-10-08

GUI
- **New interface icons** (GUI-02): menu, notification bell, theme, search and sidebar controls now use a consistent outlined icon set in Material style.
  - The set was drawn for NSM and is self-hosted as an SVG sprite (`static/ui-icons.svg`, `ui_icon()` helper).
  - It is CSP-safe and follows light and dark theme.
- **More polished look**:
  - active menu item marked with a side bar;
  - notification badge on the bell, which nudges on hover;
  - gradient brand mark and soft sidebar light;
  - hover shadow on the cards.
- **ISP/WISP background**: a light line drawing (radio tower, radio links to client sites, fibre backbone) at very low contrast.
  - It is fixed at the bottom right, so it never sits under text at readable contrast.
  - It is disabled with high-contrast preferences and when printing; animations respect *reduced motion*.

## 0.49.47 — 2026-10-08

Logs
- **Syslog configured by the MikroTik Agent** (LOG-01 step 2). **Agent 0.49.11** can configure remote syslog toward NSM:
  - it creates or updates its own logging action `nsm` and the rules *critical*, *error*, *warning* and *account* (logins, which feed the access alerts);
  - re-running is idempotent and the operator's other logging rules are untouched.
- **Ways to start it:**
  - from the device Syslog tab, with *Configura syslog con l'agent* and the status of the last request;
  - for every MikroTik from *Amministrazione → Syslog*;
  - automatically by the worker (default on) once the NSM IPv4 address is set. Agents updated later get configured too, and a new address re-configures everyone.
- **Not covered:** legacy agents (RouterOS 7.12 and 6.x) keep the manual commands shown in the tab.

## 0.49.46 — 2026-10-08

Security
- **Access alerts from syslog** (LOG-01 step 3). Received lines are recognised as failed logins (wrong user or password) or successful logins, with user, source address and service.
  - Vendors covered: RouterOS, OpenSSH/Dropbear (airOS, Cambium, Mimosa, Linux CPEs), Cisco IOS, Juniper, FortiGate, Huawei VRP and generic web logins.
  - Every recognised line becomes an access event (migration 0031).
- The worker raises Action Center alerts and notifications in the *Sicurezza* category (e-mail, Telegram and Slack, according to each user's preferences):
  - **brute force**: 5 or more failed logins in 10 minutes from the same address (high when the address is public);
  - **successful login after failures** from the same address (critical: possible guessed password);
  - **successful login from a new public address** not seen in 90 days (warning).
  - Repeated alerts are grouped for 6 hours.
- **Device Syslog tab**: an *Accessi al dispositivo* panel with 24-hour counters, the addresses with the most failures and the latest accesses; failed and successful logins are highlighted in the live log.
- **Security → Accessi**: alerts, most attacked devices and source addresses across the fleet (1h, 24h, 7 days, 30 days).

## 0.49.45 — 2026-10-07

Logs
- **Integrated syslog server** (LOG-01 step 1): new Docker service `syslog` on UDP/TCP 514.
  - Parses RFC 3164, RFC 5424 and the RouterOS format (topics give the severity).
  - Each line is matched to its device by sender IP; with shared NAT the hostname picks the device.
  - Each sender is rate limited; unknown senders are counted, not stored.
  - Warning, error and critical lines are kept for the whole life of the device; info, notice and debug lines are kept for up to 90 days to limit storage.
- **Syslog tab on every device** (any vendor):
  - live log updated every 5 seconds, with pause, severity and text filters, older lines on demand and CSV export;
  - counters for the day;
  - remote syslog configuration for the device's own manufacturer, shown inline.
- **Amministrazione → Syslog** (admin only):
  - server address for the devices, retention, acceptance of unknown senders;
  - receiver status;
  - unknown senders, which can be assigned to a device;
  - instructions for about 18 manufacturers.
- The **Server syslog** connector is added to the system health page.

## 0.49.44 — 2026-10-07

Inventory
- **Fix: MikroTik devices without IP in the device list** (INV-07). Agent-managed devices now get a management IP:
  - the public address configured on the router (PPPoE/WAN);
  - otherwise the address NSM sees the heartbeat from, which also works with older agents;
  - a value typed by an operator is never overwritten.
- The device lists show the management IP with its scope (public, private, CGNAT) and the LAN IP. The device header adds *IP LAN* and *Visto da NSM*.
- **MikroTik Agent 0.49.10** reports the RouterOS IP addresses with every heartbeat: as a metric on modern agents, as the `X-NSM-Addrs` header on legacy agents.

Roadmap
- GUI-02: Material icons for menu and notifications, more attractive GUI with an optional ISP/WISP background.

## 0.49.43 — 2026-10-07

Monitoring
- **Interface traffic graphs** (MON-01) on the MikroTik Monitor page, in Cacti/Zabbix style: in/out bit/s, 1h/24h/7d/30d ranges, current/average/maximum/95th percentile/volume.
  - Counter resets and missing heartbeats show as gaps.
  - WAN interfaces (PPPoE client, LTE, tunnel clients, else ether1) are monitored by default, and the list can be changed per device.
  - Retention is 90 days (migration 0029).
- **MikroTik Agent 0.49.9** sends the interface byte counters with every heartbeat: as a metric on modern agents, as the `X-NSM-Ifaces` header on 7.12 and RouterOS 6 legacy agents. Modern agents self-update; legacy agents need a reinstall to start sending counters.

- **Last telemetry is never lost** (MON-02): retention keeps the newest sample of every device and interface (MikroTik metrics, interface traffic, UISP metrics), so an offline or unreachable device still shows the last data received; the Monitor page shows its date.

Roadmap
- LOG-01: integrated syslog server with live per-device logs, configured by the agent.
- DUDE-01: integration with MikroTik The Dude through the RouterOS API (features to be evaluated).

## 0.49.42 — 2026-10-07

GUI
- **Global search**: compact suggestions grouped by pages, CVEs, devices, customers and sites, with counts and *vedi tutti*; device status dot, manufacturer and matched field; pages filtered by permission; CVE results with severity and exposed devices; recent searches; `/` and Ctrl+K shortcuts; highlight no longer splits values.
- **Manufacturer icons** instead of the two-letter vendor badges (Simple Icons, CC0, self-hosted sprite; generic device icon for brands without a published icon).

## 0.49.41 — 2026-10-07

Security
- **Multi-vendor CVE correlation (SEC-05)**: about 30 common manufacturers available by default (Ubiquiti, TP-Link, Cambium, Mimosa, Tenda, Huawei, ZTE, Teltonika, Fortinet, Juniper, Cisco…); NVD queried only for the brands and models in the inventory, with the full history for newly added products; generic firmware version comparison; never exposure by brand alone. *Produttore* field for manually added devices, coverage per manufacturer on the NVD page, CVE counts for every evaluable brand.
- Roadmap: added MON-01 (telemetry graphs, WAN/PPPoE traffic), ZBX-01 (Zabbix API) and GUI-SEARCH (global search bar).

## 0.49.40 — 2026-10-07

Notifications
- **Vulnerability newsletter** (daily or weekly, chosen in the profile): new CVEs on the inventory with severity, CVSS and number of exposed devices, plus open totals; skipped when there is nothing new.
- **Scheduled report delivery (REP-04)**: generated reports reach the users subscribed to *Report*, as e-mail attachment or link on Telegram/Slack.
- **Worker error alerts**: three consecutive failures of a periodic task raise a high-level notification, and its recovery an informative one.
- Migration 0028.

## 0.49.39 — 2026-10-07

Notifications
- **Telegram and Slack channels**: Telegram bot configured by the administrator (encrypted token, verified on save) with per-user chat ID, and the bot answers `/start` with the chat ID; per-user Slack Incoming Webhook (encrypted, only `hooks.slack.com`). Same level/category filters, outbox and retries as e-mail.

## 0.49.38 — 2026-10-07

Security
- **Two-factor authentication (TOTP)**:
  - setup from the profile with a QR code (Google Authenticator, Microsoft Authenticator, Aegis and similar);
  - ten one-time recovery codes;
  - a second login step with replay protection and throttling;
  - disable with password and code, and administrator reset;
  - 2FA status column on the users page.
  - Migration 0027; new dependency `segno` (pure-Python QR code).
- Roadmap: added UBNT-08 (full UISP management), VEND-01 (firmware catalogs for other vendors), VEND-02 (cnMaestro) and SEC-05 (multi-vendor CVE correlation).

## 0.49.37 — 2026-10-07

Notifications
- **E-mail notifications**: SMTP configuration in *Amministrazione → Notifiche* (encrypted password, test message, delivery log, re-queue); each user sets e-mail address, minimum level and categories in the profile; every in-app notification is queued for the matching users and delivered by the worker with retries and backoff; failed messages stay visible instead of being lost. Migration 0026.

## 0.49.36 — 2026-10-07

TR-069 / ACS
- **GenieACS integrated in the Docker stack** (optional profile `acs`): MongoDB 7 plus the cwmp, nbi, fs and ui services from one image built from the official `genieacs` 1.2.16 npm package, non-root with privileges dropped; ACS URL `:7547` and file server `:7567` published, NBI internal, UI on the host loopback only. `./manage.sh acs-enable` prepares secrets and profile; *Usa GenieACS integrato* points the NSM connector at it. `update.sh` and `install_core.sh` deploy the `genieacs/` build context. Procedure in `docs/OPERATIONS.md`.

## 0.49.35 — 2026-10-07

Development
- **Release discipline guard**: `APP_VERSION` must match the first CHANGELOG entry; entries are unique, consecutive, dated and non-empty; the agent version is documented; Alembic migrations form one numbered chain without gaps, with a single head and a downgrade each. Rules in `docs/DEVELOPMENT_WORKFLOW.md`.

## 0.49.34 — 2026-10-07

Operations / security
- **Deployment hardening**: `no-new-privileges` and `cap_drop: [ALL]` on migrate/api/worker (the image already runs as non-root uid 10001, so behaviour is unchanged); Caddy adds `Cross-Origin-Opener-Policy` and `X-Permitted-Cross-Domain-Policies`; new guard test for published ports, privileges, image user and security headers.
- `docs/OPERATIONS.md`: hardening review with what is in place and the per-installation actions (HTTPS with HSTS and secure cookies, Docker network trust, host firewall).

## 0.49.33 — 2026-10-07

Operations
- **Connector health** on *Amministrazione → Sistema*: one row per data source (UISP, NVD, GenieACS, RouterOS catalog, MikroTik agents) with state, last success and error detail.

## 0.49.32 — 2026-10-07

Operations
- **Capacity and retention** on *Amministrazione → Sistema*: largest tables, backup archive size and 30-day growth, estimated days until the backup volume is full (warning under 90 days), retention applied to each data set.
- `docs/OPERATIONS.md`: capacity/retention table and sizing rule, least-privilege deployment guidance (host, network, roles, API keys, MikroTik agent policies, UISP/GenieACS/NVD credentials).

## 0.49.31 — 2026-10-07

Security / operations
- **Encryption master-key rotation**: retired keys in `ENCRYPTION_PREVIOUS_KEYS` stay readable; `./manage.sh rotate-secrets` re-encrypts connector credentials and MikroTik binary-backup passwords with the new `ENCRYPTION_MASTER_KEY`; *Amministrazione → Sistema* shows secrets per key state and warns when some are unreadable. Rotation procedure in `docs/OPERATIONS.md`.
- Roadmap: the P0 completion-idempotency gate is marked as merged (#112–#116).

## 0.49.30 — 2026-10-07

TR-069 / ACS
- **GenieACS connector (ACS-01..03, read-only)**: NBI configuration with encrypted Basic/Bearer credentials for a reverse proxy and connectivity test; *ACS* tab on TP-Link/TR-069 CPE with lookup by serial number then MAC, preview, explicit association (NSM Customer/Site preserved) and refresh by GenieACS ID; identity, firmware, hardware, IP and last Inform normalized from TR-098/TR-181; integrations hub card with real state. Ported from the unmerged Core 0.27 foundation branch onto the current CSP-safe UI.

## 0.49.29 — 2026-10-07

Ubiquiti
- **UBNT-06 step 1, firmware state from UISP**: installed and latest firmware, latest on the same major, compatibility and pre-release are read at every sync; Ubiquiti devices get the recommended version and *update available* / *current* state and appear in the firmware worklist; new *Firmware da UISP* panel on the UISP tab. No state is inferred when UISP does not expose the latest version.

## 0.49.28 — 2026-10-07

Security
- **Login throttling**: 5 failed logins for the same username from the same address, or 30 from one address, within 15 minutes block further attempts with `429` until the window passes; the account is never locked. Failed and throttled logins are audited and listed on *Amministrazione → Utenti*. Migration 0025.
- **Session lifetime**: idle timeout (`SESSION_IDLE_MINUTES`, default 120); a password change closes every other session; *Esci dalle altre sessioni* (profile) and *Disconnetti* (admin users); the login page explains why the session ended.

## 0.49.27 — 2026-10-07

Security
- **API-key lifecycle**: rotation with a grace period (24 h, 7 days or immediate revoke) keeping name, scopes, Customer and validity length; usage evidence per key (request count, last client IP); review states for expired, expiring (14 days), never used, unused for 90 days and keys without expiry. Expired keys are no longer shown as *Attiva*. Migration 0024.

GUI
- Code blocks (`json-preview`: API authentication examples, raw diagnostic output) had dark text on a dark background; the text is now readable.

## 0.49.26 — 2026-10-07

Operations
- **Worker task isolation**: every periodic worker task runs in isolation; an error in one task (for example an unreachable UISP or NVD) is logged and recorded and no longer skips the remaining maintenance, verification and sync tasks of that cycle.
- **Worker observability**: heartbeat and last outcome of each task in Redis; `/health` adds a `worker` field (status code unchanged); new *Amministrazione → Sistema* page with version, schema revision, database size, backup-volume free space, worker tasks, platform database dumps and restore drills.
- **Restore tooling**: `./manage.sh restore-drill` (non-destructive restore test into a temporary database, recorded as evidence) and `./manage.sh restore-db <dump>` (typed confirmation, safety dump, restore, migrations); runbook `docs/OPERATIONS.md`.

GUI
- `alert warning` and `alert danger` boxes, used on 20+ pages, now have their own styles.

## 0.49.25 — 2026-10-07

MikroTik
- **MTK-05 structured diagnostics**: ping (loss, min/avg/max RTT, jitter, per-packet table), traceroute (hop table, destination reached), neighbors and DHCP leases linked to NSM Devices by MAC, logs newest first with levels; legacy agent output is parsed into the same views. Per-target history of the previous 10 ping/traceroute runs and a one-line outcome in the recent diagnostics list (last 25 diagnostics, no longer crowded out by other jobs).

## 0.49.24 — 2026-10-07

MikroTik
- **MTK-04 step 5, RouterOS upgrade suggestions**: *Firmware → Suggerimenti RouterOS* shows every MikroTik behind its channel head with the next safe step (ready for a plan, legacy upgrade, readiness check, plan open, soak, blocked with reason), security first; non-security releases wait 7 days on the channel. Bulk readiness checks and plan creation (max 25, every gate re-checked, refused plans leave no draft); plans still need the pre-upgrade backup and approval.

## 0.49.23 — 2026-10-07

Security
- **Route-level RBAC guard**: a test walks every registered route; anonymous requests must end on the login page and writes by the read-only auditor must be refused, except an explained allow-list (own notifications read, read-only firmware check). The review found no exposed route.

## 0.49.22 — 2026-10-07

MikroTik
- **MTK-04 RouterOS release catalog**: channel heads and release notes from upgrade.mikrotik.com every 6 hours, security classification with evidence, device firmware state from the catalog when readiness is missing or stale, security escalation from release notes and open CVEs; catalog page and firmware-tab line.
- **Agent scheduler `start-time=startup`** (new installations): avoids the RouterOS 7.24.0–7.24.4 bug where schedulers with default start date/time were not triggered, and sends a heartbeat right after every reboot.

## 0.49.21 — 2026-10-07

Ubiquiti
- **UBNT-02 UISP monitoring**: CPU, RAM, signal, link capacity, uptime, frequency and stations from the UISP overview at every sync, with 90-day history; UISP tab with current/min/avg/max over 24 h; signal in the customer device list; missing values stated, never zero.

## 0.49.20 — 2026-10-07

Ubiquiti
- **UBNT-03 bulk onboarding from UISP**: list of UISP devices with their NSM state, explicit Customer/Site, preview, fresh re-read at confirmation, creation or association of up to 200 devices per batch; *Importa da UISP* on the customer device list.

## 0.49.19 — 2026-10-07

GUI
- **CVE column in the customer device list**: open CVEs per device with the highest severity and the critical/high still to handle, linking to the vulnerability list filtered on that device (new `device` filter); `0` only when an advisory source is loaded and the device is evaluable, `—` otherwise.

## 0.49.18 — 2026-10-07

Lifecycle
- **LIFE-03** EOL/EOS remediation: replacement plan with target date, justified exception with expiry, replacement Device or decommissioning, history and audit; Action Center issue while to handle, overdue or after an expired exception; *Gestione* column in the EOL/EOS worklist; report section and CSV column.

## 0.49.17 — 2026-10-07

MikroTik Agent 0.49.8
- **Bounded modern snapshots**: configuration sections and the support snapshot read rows by id up to a per-menu limit and halve the rows sent until the JSON body fits the 64 KiB RouterOS `http-data` limit (previously a large routing table, firewall or lease list made the upload fail and the snapshot was lost).
- **RouterOS 7.13 – 7.16**: modern agent variant without `json.no-string-conversion` (a 7.17 option that made the whole script fail to load on earlier 7.x) and with `/file read` compiled at run time; chosen at enrollment and self-update.

## 0.49.16 — 2026-10-07

MikroTik Agent 0.49.7
- **`.rsc` backup for RouterOS 7.12 legacy agents**: export read with `/file get contents` (up to ~60 KB) and archived with SHA-256; policies reduced to the export format for these agents; older legacy agents and RouterOS 6 stay explicitly not protected.

## 0.49.15 — 2026-10-07

Fix
- **Pages behind the production CSP**: Caddy sends `default-src 'self'`, which blocks inline scripts, `on*` handlers and inline style attributes, so the new-device vendor fields (Ubiquiti MAC never visible), the backup-policy form, bulk actions, theme init, copy buttons, clickable rows, delete confirmations and the dashboard donut/bar charts did not work in production. All behaviours moved to `static/forms.js` / `static/theme-init.js` driven by `data-*` attributes; a test forbids inline scripts, handlers and style attributes.
- **New device**: one MAC/serial field pair per vendor section (the three `primary_mac` inputs overrode each other), inactive sections disabled, Ubiquiti MAC required; validation errors return to the form with a message instead of a JSON page.
- **UISP connection test**: the error names the cause (invalid/self-signed certificate with the *Verifica certificato TLS* hint, connection refused, DNS, timeout, HTTP/HTTPS mismatch); hint next to the TLS checkbox for local UISP consoles.

## 0.49.14 — 2026-10-07

MikroTik Agent 0.49.6
- **Legacy agents (6.48/6.49, 7.12) can reboot and upgrade RouterOS**: profile `legacy-ops-v1`, fixed handlers acknowledged by NSM before acting, upgrade target from a fresh readiness (same major only), verification by the version reported after the reboot; Firmware tab panel with typed confirmation. Legacy agents must be reinstalled once.

## 0.49.13 — 2026-10-07

MikroTik
- **RouterOS 6.48/6.49 support**: compatibility family `routeros-6-legacy` and a v6 variant of the legacy agent (no v7-only syntax, validated before it is handed out): enrollment, heartbeat, telemetry, diagnostics (ping, neighbours, DHCP, logs), firmware readiness and structured snapshots; traceroute refused with an explicit message.

## 0.49.12 — 2026-10-07

MikroTik Agent 0.49.5
- **Controlled reboot** from the GUI (*Riavvia* on the device header): reason and typed confirmation, agent acknowledgement before `/system reboot`, verification by uptime reset on the next heartbeat, explicit failure after 15 minutes, history and audit.

## 0.49.11 — 2026-10-07

MikroTik Agent 0.49.4
- **Modern backup uploader rewrite** (supersedes #105, #108, #118): privilege profile ops-v2 adds `policy` and `sensitive`, required by RouterOS for `/system backup save` from a scheduled script (reinstall the agent once); settled non-empty file also under `flash/`; progress guard; step-level errors with operator explanation; temporary files always removed; empty artifacts refused server-side.

## 0.49.10 — 2026-10-07

MikroTik Agent 0.49.3
- **RouterOS 7.12.x structured snapshots** (ex #104): resources, interfaces, addresses, routes, firewall/NAT, DHCP leases, PPP/tunnels and logs over the allow-listed `rows-v1` legacy transport, with bounded per-menu collection and a 64 KiB wire cap; legacy agents must be reinstalled (older ones get an explicit reinstall hint).

## 0.49.9 — 2026-10-07

Lifecycle
- **LIFE-01/02** Lifecycle catalog per vendor model (EOL/EOS separate, source and verification date, CSV import/export) and exact model/alias correlation with Devices; ambiguous and unmatched models stay explicitly unknown; manual values with source; *In scadenza* and *Senza dato lifecycle* worklists.

## 0.49.8 — 2026-10-07

Compliance
- **COMP-04** Compliance tab on every device, summary on the customer security tab, report section *8. Compliance* and CSV columns.

## 0.49.7 — 2026-10-07

Compliance
- **COMP-03** Non-compliance handling: take in charge, exceptions with justification and expiry, automatic clearing when compliant again, history, Action Center issue per device with unhandled failures.

## 0.49.6 — 2026-10-07

Compliance
- **COMP-01/02** Inherited, versioned compliance baselines (global → vendor → customer → site → device) and eight evidence-based controls with pass / fail / no evidence / not applicable results; `/compliance` matrix and baseline editor; evaluation every 30 minutes.

## 0.49.5 — 2026-10-07

Incidents
- **INC-04** *Incidenti* section in the periodic evidence report; hashed PDF evidence export of a single incident (timeline, hypotheses, confirmed root cause) stored in the report archive.

## 0.49.4 — 2026-10-07

Incidents
- **INC-03** Candidate correlations (heuristic, with reason and confidence), hypotheses with evidence snapshot, root cause only by explicit operator confirmation with justification; root cause column in the incident list.

## 0.49.3 — 2026-10-07

Incidents
- **INC-01/02** Incidents per Customer with involved Devices, status lifecycle and a deterministic timeline built from recorded evidence (audit, Action Center, backups, agent jobs, vulnerability changes) plus operator notes shown separately; *Apri incidente* from every Device.

## 0.49.2 — 2026-10-07

Security
- **SEC-04** Evidence reports carry security remediation: source provenance with stale warning, findings by state, unhandled critical/high, resolutions with reason and mean time to resolve, active exceptions with justification, open critical/high table; CSV adds per-device unhandled and excepted counts.

## 0.49.1 — 2026-10-07

Security
- **SEC-01** NVD CVE API 2.0 ingestion for RouterOS: full then incremental loads, backoff, Action Center issue on repeated failures, admin page *Integrazioni → Advisory NVD* (#134).
- **SEC-02** Device ↔ advisory matching on the installed RouterOS version, explicit *non valutabili*, automatic resolve/reopen with evidence (#134).
- **SEC-03** Remediation lifecycle (planned, in progress, exception with expiry), finding page with history and firmware plan link, Action Center issue per device with unhandled critical/high CVEs (#135).

GUI
- Device and Customer shells with tabs, backup archive redesign, devices list with quick filters (#129, #130, #131).
- Single stylesheet, readable dark theme, mobile fixes (#132).
- Operational lists share quick chips and pagination; backup overview as a per-device worklist; content-hashed static assets (#133).

Operations and fixes
- Firmware plans no longer stuck or resurrected; no downgrade plans (#117, #123).
- Backup retry after a partial attempt and stale attempt reports (#119, #121).
- UISP periodic sync (#120); restore-test evidence (#122); Agent self-update and RouterBOOT stuck-state recovery (#124, #125).
- Evidence reports PDF/CSV with archive and schedules (#126, #127).

## 0.49.0

Baseline before the changes above.

# Core 0.9 — UX, Backup Center & MikroTik hardening

## UX / UISP-like
- Live global search with suggestions while typing for IP, MAC, device alias/identity, customer, site, serial and model.
- Click suggestion to open the relevant customer/site/device record directly.
- Keyboard navigation: Up/Down, Enter, Escape.
- Responsive desktop/tablet/mobile navigation and compact dense layouts inspired by network-management tools.
- Customer list without inline creation form; dedicated add/edit pages and contextual top actions.
- Dedicated site/device management pages and clearer destructive-operation confirmations.

## Branding
- Keep per-user dark mode persistence.
- Keep custom logo and GUI palette.
- Individual reset-to-default action for every configurable color.
- Continue extending theme coverage to all major GUI components.

## Customer / site / device lifecycle
- Create/edit/delete customer.
- Customer deletion cascades associated devices/sites only after explicit confirmation and audit event.
- Create/edit/delete site without deleting devices; devices are reassigned to no site when appropriate.
- Move one or multiple devices between customers and sites with validation and audit trail.
- Delete one or multiple devices with strong confirmation and audit events.
- Keep discovered/observed technical identity separate from operator-editable alias and organization assignment.

## Backup Center — primary product core
- No raw cron in normal GUI.
- Human-readable scheduling presets and internal cron translation.
- Explicit policy scope hierarchy: device > site > customer > vendor > global.
- Policy create/edit/enable-disable/delete/clone.
- Clone policy and retarget it to another device/site/customer/vendor.
- Vendor-aware backup capabilities instead of universal binary/text switches.
- MikroTik: encrypted `.backup` plus `.rsc` export when selected.
- Ubiquiti/UISP, TR-069 and future vendors expose only capabilities that actually exist.
- Pre-firmware backup option.
- Retention daily/weekly/monthly, retry policy and integrity SHA256.
- Backup history with status, size, hash, source policy and error detail.
- Backup artifact archive with secure download and deletion.
- Storage cleanup when devices/customers are intentionally deleted.
- Manual `Backup now` action.
- Effective-policy calculation must use hierarchy and be shared by scheduler/manual/MikroTik agent paths.

## Demo / QA
- Reversible Demo Lab available from Administration.
- Demo customers, sites, multi-vendor devices, policies, runs and backup artifacts.
- Demo data never created automatically in production.
- CI integration tests for migrations, audited CRUD, live search, backup policy CRUD/clone, artifact download/delete and MikroTik backup transport.

## MikroTik first real integration
- Outbound-only enrollment over HTTPS.
- One-time short-lived enrollment token.
- Per-device permanent credentials; never global shared credential.
- Inventory discovery: identity, board/model, serial, architecture, RouterOS, RouterBOOT, packages, MACs, software-id, uptime.
- Heartbeat and predefined jobs only; no arbitrary remote shell.
- Real encrypted backup transport over HTTPS with binary `.backup` and text `.rsc` where policy requires them.
- Job and backup status visible in device and Backup Center UI.
- Next phases: scheduled backup worker, firmware inventory/check/remediation, monitoring telemetry and restore-test evidence.

## Engineering rules
- Non-destructive Alembic migrations.
- `pg_dump` before migrations/deploy.
- CI must be green before promotion to `deploy`.
- Runtime secrets/data/logs/backups are never committed to source control.
- Audit significant administrative, destructive, backup, enrollment and automatic changes.
- Preserve generic multi-vendor product identity; no operator brand hard-coded in source.

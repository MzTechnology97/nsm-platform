# Changelog

NSM Core follows `0.49.x`: every release promoted to `deploy` increases the
patch number in `app/app/entrypoint.py` (`APP_VERSION`, shown in the sidebar).
MikroTik Agent versions are independent (`mikrotik_agent_generation.py`).

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

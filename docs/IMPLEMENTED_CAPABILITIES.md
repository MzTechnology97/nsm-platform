# NSM Platform — Implemented Capabilities

Status: **living implementation source of truth**  
Snapshot: **2026-09-30**

This document describes behavior present in `main`. It intentionally separates **implemented**, **physically/vendor validated**, **validation pending** and **not implemented** states.

An open PR does not count as implemented in `main`.

## 1. Core application and inventory

### Implemented

- Customer → optional customer-local Site → Device hierarchy.
- Server-side validation prevents assigning a Device to a Site owned by another Customer.
- Device identity separates editable operator alias from observed device identity.
- Customer list and customer profile workspaces.
- Customer tabs/workspaces for Devices, Sites, Backups, Security and History.
- Global Device inventory with server-side filtering and pagination.
- Device management/edit/delete workflows.
- Customer/Site create/edit/delete workflows.
- Customer-scoped bulk Device move/delete workflows.
- CSV Device import with template, dry-run and validation.
- Global search/suggestions across supported Customer/Site/Device identifiers.
- Dynamic dashboard summary.
- Customer/device drill-down links from attention counters and worklists.

### Important boundary

A Site is customer-local. UISP or another connector may retain external organization/site metadata, but that metadata must not silently redefine NSM ownership.

## 2. Authentication, users, permissions and branding

### Implemented

- Session-based web authentication.
- User administration foundation.
- Baseline role/permission framework.
- Permission checks for Customer, Device, backup, firmware, security, administration and related actions.
- Self-service password change.
- User UI theme preference.
- Configurable portal branding and logo support.
- Audit events for material user/admin changes where implemented.
- API-key administration with revoke/delete lifecycle.
- API-key scopes and Customer scoping for the public read-only API.

### Still incomplete

- mature delegated/scoped administration across all operator personas;
- comprehensive least-privilege review for every future module;
- enterprise identity-provider integrations.

## 3. Audit, Action Center and notifications

### Implemented

- append-oriented audit event foundation;
- filtered/paginated audit event UI;
- CSV audit export;
- Action Center issue model/worklist;
- issue acknowledgement workflow;
- in-app Notification Center;
- per-user unread/read state;
- single notification read and read-all actions;
- contextual browser feedback for normal success/stale/error cases.

### Design rule

Audit history, notifications and Action Center are different concepts:

- **Audit** records material history/evidence;
- **Notifications** surface user-visible events;
- **Action Center** represents attention items requiring operator action or acknowledgement.

## 4. Browser feedback and operator UX

### Implemented

A shared contextual-feedback pattern now covers the principal browser actions that previously could escape to raw FastAPI JSON pages. Current covered areas include:

- MikroTik Agent reinstall/update-facing browser flows;
- RouterOS firmware and RouterBOOT actions;
- RouterOS upgrade planning/approval/staging/activation/cancel paths;
- manual backup actions;
- backup-policy actions/forms;
- Backup Explorer stale/view/delete failures;
- MikroTik configuration baseline selection;
- UISP association/refresh;
- Device CRUD;
- Customer/Site CRUD;
- customer Device bulk actions;
- CSV import validation;
- API-key administration;
- Action Center acknowledgement;
- Notification Center read actions.

Expected browser failures remain in the application using one-shot success/info/warning/error messages and safe redirects where appropriate.

### Machine API boundary

Agent endpoints, connector contracts, upload endpoints and the public API retain structured HTTP/JSON behavior. The browser UX layer must not globally rewrite machine errors into HTML.

## 5. Backup framework

### Implemented

- global/vendor/customer/site/device policy scopes;
- effective policy precedence: Device → Site → Customer → Vendor → Global;
- capability-aware backup coverage;
- readable schedule model;
- scheduler foundation;
- retry and stale-run maintenance;
- retention settings/foundation;
- pre-firmware backup controls where applicable;
- backup run/evidence records;
- SHA-256 artifact evidence;
- backup failure/recovery integration with operational attention mechanisms where implemented;
- global Backup Center;
- Customer-scoped backup view;
- device-scoped Backup Explorer.

### Device-scoped Backup Explorer

Implemented capabilities:

- `Backup now` action for eligible MikroTik Devices;
- list artifacts belonging only to the selected Device;
- authenticated download;
- delete;
- `.rsc` text preview;
- comparison/diff between compatible same-Device text exports;
- binary backup preview blocked rather than rendered;
- cross-Device artifact isolation.

## 6. MikroTik onboarding and Agent architecture

### Implemented

- short-lived one-time enrollment token;
- outbound RouterOS bootstrap/enrollment;
- per-Device persistent credential after enrollment;
- no global shared RouterOS Device secret;
- modern and legacy Agent transport families;
- RouterOS compatibility resolver/capability foundation;
- heartbeat and inventory refresh;
- allow-listed job queue;
- Agent status/health view;
- Agent Fleet worklist and filters;
- installation verification/recovery states;
- Agent reinstall flow without deleting the Device;
- in-place Agent update/self-update foundation with rollback behavior covered by smoke tests;
- stale pending job expiry;
- stale delivered non-backup job expiry; running jobs keep domain-owned timeouts (backup maintenance, self-update reconciliation);
- expired self-update jobs move the Device update state to `expired` (audited, visible in Agent Fleet) and no longer block a new update; late self-update reports for a terminal job are acknowledged without changes;
- backup jobs excluded from generic job expiry where the backup lifecycle owns timeout/retry behavior.

### Security boundary

The Agent does not implement arbitrary remote command execution. Jobs are explicit server-defined operations with authenticated Device-scoped execution.

## 7. MikroTik compatibility status

### RouterOS 7.24.4 stable — modern family

Physically validated:

- clean modern enrollment;
- repeated heartbeat;
- modern Agent installation/scheduler behavior;
- SHA-512 fingerprint generation in the required hexadecimal representation;
- corrected non-interactive generated source behavior.

Implemented in `main`:

- telemetry;
- structured configuration snapshots;
- diagnostics;
- support snapshot foundation;
- backup transport;
- firmware readiness/upgrade workflow;
- Agent update/self-update foundation.

Validation still open for some backup hardening items tracked in focused PRs; see `ROADMAP.md`.

### RouterOS 7.20.7 long-term — modern family

The generated-source compatibility blocker found during earlier testing has been corrected in `main`.

A fresh physical clean-enrollment and feature-by-feature revalidation is still required before this point release is marked physically accepted.

### Modern snapshot size bounds (Agent 0.49.8)

- configuration sections read rows by id (`find` + `get`) up to a per-menu limit (routes, interfaces, firewall filter/NAT 300; addresses, leases, PPP 500; tunnel clients 100) and never materialize a whole table;
- before uploading, the agent halves the rows sent per list until the JSON body is at most 60 000 bytes (RouterOS caps `http-data` at 64 KiB); results carry `total`, `limit` and `truncated`;
- the support snapshot follows the same rule with 50 rows per table.

### RouterOS 7.13 – 7.16 — modern family, early variant

- `json.no-string-conversion`, used by every modern JSON call, exists only from RouterOS 7.17 and RouterOS checks options when a script is loaded: 7.13 – 7.16 receive the modern source without it (numeric-looking strings may travel as JSON numbers; NSM stringifies agent values);
- `/file read` is compiled at run time with `:parse`, so a release without it fails only the backup at step `read` (with the advice to move to 7.17+) instead of the whole agent;
- the variant is chosen from the RouterOS version at enrollment and at self-update; a validator refuses to hand out an early source that still contains 7.17-only constructs.

### RouterOS 6.48 / 6.49 — legacy family, v6 source

- compatibility family `routeros-6-legacy`: same bodyless enrollment, header heartbeat and rows-v1 jobs as 7.12;
- RouterOS validates every command when a script is loaded, so v6 gets its own source: ping reports `sent=10;received=N` (v6 `/ping` has no `as-value`), snapshot sections iterate `print as-value` rows with the same bounded output, and NSM refuses to hand out a v6 source that still contains a v7-only construct (`:serialize`, `/file read`, `as-value` on ping/traceroute, `get` without property, v7 menu paths);
- traceroute is refused server-side with an explicit message (not scriptable on v6);
- releases older than 6.48 stay unvalidated (fail closed);
- legacy agents 0.49.6+ (profile `legacy-ops-v1` = `ftp,reboot,read,write,test`) support the controlled reboot and the RouterOS upgrade below; backup is not available on legacy families; physical acceptance on 6.49 is tracked in issue #128.

### RouterOS 7.12.1 — legacy family

Physically validated:

- legacy/bodyless enrollment;
- heartbeat;
- basic inventory/current state.

Implemented in `main`:

- legacy transport;
- historical telemetry ingestion path.

Physical history validation is still pending.

Structured configuration snapshots are in `main` from Agent 0.49.3 (legacy agents must be reinstalled); physical acceptance on 7.12.1 is tracked in issue #128.

Legacy RouterOS backup transport is **not implemented**.

## 8. MikroTik telemetry and monitoring

### Implemented

- normalized current Agent state;
- CPU/memory/uptime telemetry foundation;
- metric sample persistence/history;
- modern historical telemetry;
- legacy telemetry ingestion implementation;
- Agent stale/health states;
- interface-health worklist derived from configuration snapshots;
- network-health and policy-health foundations/workspaces.

NSM monitoring is intentionally lightweight and does not attempt to replace a full NMS.

## 9. MikroTik configuration and configuration history

### Implemented for supported modern Agents

- unified configuration workspace;
- resources snapshot;
- interfaces;
- IP addresses;
- routes;
- firewall/NAT-related structured data where supported;
- PPP/tunnel/current operational sections where supported;
- filtering/search within supported views;
- configuration/export history;
- approved baseline selection;
- drift detection/evaluation;
- contextual stale-baseline handling;
- `.rsc` preview and diff through backup/export tooling.

### RouterOS 7.12.x legacy snapshots

- same sections as the modern Agent (resources, interfaces, IP addresses, routes, firewall filter/NAT, DHCP leases, PPP/tunnel clients, warning/error logs) over the fixed allow-listed `rows-v1` plain-text transport; the server only selects a section, never sends commands;
- bounded collection: rows are read by id up to a per-menu limit (routes/firewall/interfaces 300, addresses/leases/PPP 500, tunnel clients 100) and the wire output stays below the 64 KiB RouterOS `http-data` limit; larger tables are counted and flagged as truncated instead of loading the router;
- available only with legacy Agent 0.49.3+: an older legacy Agent gets a *reinstall* hint and its snapshot jobs fail explicitly instead of being reported as empty successes.

## 10. MikroTik diagnostics

### Implemented foundations

- ping diagnostic job;
- traceroute diagnostic job;
- neighbor-related diagnostic collection;
- DHCP/log diagnostic views/jobs where supported;
- bounded structured job/result history;
- support-snapshot foundation on supported modern Agents;
- contextual GUI handling for human-triggered actions;
- **structured results (MTK-05)** for modern (`result.data`) and legacy agents (the `:tostr` output is parsed back into rows): ping with sent/received, loss, min/avg/max RTT, jitter and per-packet table; traceroute hop table with reached/not-reached; neighbors and DHCP leases linked to NSM Devices by MAC; logs newest first with level badges; the raw output stays available;
- **history**: every ping/traceroute result lists the previous 10 runs towards the same target on the same Device; the diagnostics page lists the last 25 diagnostics with a one-line outcome (loss and average RTT, hops, events).

### Still incomplete

- broader bounded advanced diagnostics;
- complete real-device validation matrix across supported RouterOS families.

## 11. MikroTik backup transport

### Implemented for supported modern Agents

- RouterOS `.backup` creation with per-job password;
- optional `.rsc` text export;
- per-job backup secret encrypted at rest;
- secret delivered only to the authenticated Device for the active job;
- outbound HTTPS artifact upload;
- Base64 chunk transport;
- strict upload offsets;
- artifact size bounds;
- RouterOS file-read normalization using `/file read ... as-value`;
- rejection of zero-length upload chunks server-side;
- server-side SHA-256 verification;
- final archive move/storage;
- a backup job re-queued for retry can only be used by the attempt that receives it on the next heartbeat: Agent backup endpoints refuse `pending` jobs, and a late completion from the abandoned attempt is ignored (`BACKUP_STALE_ATTEMPT_REPORT_IGNORED`);
- automatic retry after an Agent/upload timeout regenerates every format: an artifact completed by the previous attempt stays archived until the retry's replacement upload finishes, then is superseded (soft-deleted with audit reference);
- terminal cleanup of incomplete upload state and per-job backup secret;
- authenticated artifact access through NSM.

### Uploader hardening (Agent 0.49.4)

- privilege profile **ops-v2** (`ftp,reboot,read,write,policy,test,sensitive`): RouterOS only runs `/system backup save` from a scheduled script with `policy` and `sensitive`; agents installed with ops-v1 must be reinstalled (a script cannot raise its own policies) and their failure is explained as such;
- the uploader waits up to 30 s for the file to exist with a stable non-zero size, finds it also under `flash/`, refuses empty files (server-side too), and fails when a chunk read returns no data or the server offset does not advance;
- every failure states the step reached (`backup-save`, `export`, `wait-file`, `file-empty`, `read …@offset/size`, `upload-chunk …`, `upload-finish …`); NSM adds an operator explanation and stores the step and uploaded bytes in the job result;
- job-specific temporary `.backup`/`.rsc` files (also under `flash/`) are removed after success and after any failure;
- self-update keeps the running script policies for the previous-known-good copy, so an ops-v1 agent can still self-update.

Physical acceptance on RouterOS 7.24.4 is tracked in issue #128.

### Restore-test evidence (MTK-03)

- operator-recorded restore tests per archived artifact (method: isolated lab, spare device, configuration review; target label/RouterOS version; result passed/partial/failed; notes; execution time);
- NSM re-verifies file presence, size and SHA-256 at recording time; a test cannot be recorded as passed against a non-intact artifact;
- artifact identity (filename, type, SHA-256, size, source RouterOS version from `.rsc` header) is copied onto the record, so evidence survives retention/deletion;
- failed/partial tests open a device-scoped Action Center issue resolved by the next passed test; every record emits `BACKUP_RESTORE_TEST_RECORDED`;
- Backup Explorer shows the latest restore result per artifact and per Device; device history page `/devices/{id}/backups/restore-tests`;
- NSM never restores a production Device automatically.

### RouterOS 7.12 legacy `.rsc` export (Agent 0.49.7)

- RouterOS 7.12 has no `/file read` chunking nor base64 conversion; the legacy agent exports the configuration, waits for a settled file, reads it with `/file get … contents` (RouterOS limit ~60 KB), removes it and posts the text to NSM, which checks the declared size and archives it with SHA-256 like any other artifact;
- capability *Solo export .rsc*: policies are reduced to the `.rsc` format for these agents, binary backups are never requested; an export above 60 KB fails with the size and the remedy (RouterOS 7.13+);
- older legacy agents and RouterOS 6 (script file reads limited to 4 KB) stay *not protected* and queued backups fail with the reason.

### Not implemented

- binary `.backup` for legacy RouterOS (needs an SFTP/FTP receiver; RouterOS `fetch` uploads files only over (S)FTP);
- report integration of restore-test evidence.

## 12. MikroTik firmware and RouterBOOT

### RouterOS upgrade with legacy agents (6.48/6.49, 7.12)

- Firmware tab panel *Aggiornamento RouterOS con agent legacy*: target taken from a firmware readiness younger than 24 h, only newer versions of the same major (6 → 7 is not automated), typed `AGGIORNA <target>` confirmation and explicit acknowledgement that a recent export exists (legacy agents do not run NSM backups);
- the agent re-checks the update channel and refuses if the latest version differs from the approved target, acknowledges the job to NSM and only then runs `/system package update install` (download + reboot); a success report from the script is not a verification;
- NSM marks the upgrade verified when a heartbeat after the acknowledgement reports the target version, otherwise fails it after 30 minutes; a 7.12 router upgraded to 7.13+ is then flagged *migration required* (reinstall the modern agent);
- audit `LEGACY_FIRMWARE_UPGRADE_QUEUED/ACCEPTED/VERIFIED/FAILED`; the controlled reboot is available to legacy agents through the same acknowledgement protocol.

### Implemented foundation

- customer-aware firmware worklist;
- firmware readiness Agent operation;
- RouterOS version ordering (`7.21beta3 < 7.21rc1 < 7.21 < 7.21.1`): an update is reported/recommended only when the channel build is newer than the installed one, and the upgrade planner refuses downgrades or unparseable versions;
- safe upgrade-plan model/workflow;
- explicit approval step;
- download-only package staging flow;
- activation/reboot flow;
- Agent acknowledgement/completion paths;
- post-action state foundation;
- explicit execution windows for Agent-owned staging (60 min) and activation (15 min) jobs; the worker closes a plan whose phase job expired or disappeared instead of leaving it active forever;
- operator cancellation during staging/queued activation withdraws undelivered jobs, refuses cancellation once activation reached the Agent, and late Agent reports can no longer move a cancelled/expired plan;
- RouterBOOT lifecycle/action foundations;
- RouterBOOT recovery: a flash job the Agent never ran closes the workflow as failed (staging offered again), an undelivered reboot returns to `staged` (reboot offered again), and a reboot with no post-reboot reading fails after the verify timeout;
- contextual GUI feedback around browser actions;
- pre-upgrade backup gates/controls where applicable;
- **controlled reboot** (Agent 0.49.5, `firmware.execute`): *Riavvia* on every MikroTik header and `/devices/{id}/reboot` with mandatory reason and typed `RIAVVIA` confirmation; the Agent acknowledges the job to NSM before running `/system reboot` (no acknowledgement, no reboot); NSM marks it verified only when a later heartbeat reports an uptime shorter than the time since the acknowledgement, otherwise fails it after 15 minutes; refused while backup, firmware, RouterBOOT or self-update jobs are running; modern agents with an operational profile only (legacy agents are read-only); history and audit (`DEVICE_REBOOT_QUEUED/ACCEPTED/VERIFIED/FAILED`).

### RouterOS release catalog (MTK-04)

- every 6 hours the worker reads the head of each channel from `upgrade.mikrotik.com` (7 stable/long-term/testing/development, 6 stable/long-term) with its release notes; a release is flagged *security* when its notes mention security fixes, the matching lines are kept as evidence; unreadable channels are reported, not guessed;
- every MikroTik Device is compared with the head of its channel (from the last readiness, default stable; RouterOS 6 → 6.x channels): the catalog sets the recommended version and firmware state when the device readiness is missing or older than 24 h, and escalates an available update to *security update* when the notes say so or an open CVE is fixed at or below the head;
- *Integrazioni → Catalogo RouterOS* (manual refresh for admins) and a catalog line on the Device firmware tab.

### RouterOS upgrade suggestions (MTK-04 step 5)

- *Firmware → Suggerimenti RouterOS* lists every MikroTik behind its channel head with the next safe step: *ready for a plan* (modern agent, online, readiness younger than 6 h that sees the newer version, no active plan), *legacy upgrade ready*, *readiness check needed*, *plan already open*, *in soak* (non-security releases are proposed after 7 days on the channel, security releases at once) or *blocked* with the reason;
- security suggestions come first; bulk actions queue read-only readiness checks or create plans for up to 25 selected Devices, re-checking every gate; a refused plan (no backup policy, backup already running) leaves no draft behind; plans still queue the pre-upgrade backup and wait for approval: nothing is upgraded from this page.

### Still incomplete

- complete security-advisory-to-target-version automation;
- comprehensive physical validation across supported hardware/version families.

## 13. Ubiquiti / UISP

### Monitoring (UBNT-02)

- at every association, refresh and periodic sync NSM reads the UISP device overview: CPU, RAM, signal, downlink/uplink capacity, uptime, frequency, connected stations; implausible or missing values are dropped and shown as *non esposto da UISP*, never as zero;
- current values on the Device; history in `uisp_metric_samples` (at most one sample every 4 minutes, 90 days retention, cleaned by the worker);
- UISP tab: current, min/average/max over 24 h and recent samples; customer device list shows the signal under the status.

### Bulk onboarding (UBNT-03)

- *Integrazioni → UISP → Onboarding dispositivi* (and *Importa da UISP* on a customer's device list): every UISP device with its NSM state — *Nuovo*, *Da associare* (an unlinked Ubiquiti record with the same MAC), *Già in NSM* (same UISP id), *Non importabile* (no stable id, no MAC, MAC repeated in UISP or used by another NSM Device);
- the operator selects up to 200 devices and chooses the NSM Customer and optional Site explicitly (UISP sites are shown, never imported); preview before confirmation;
- at confirmation the UISP list is read again: changed or vanished devices are skipped and reported; new Devices are created with type from the UISP role, existing records are associated and keep their Customer; `devices.write` required; `DEVICE_ADDED`, `UISP_DEVICE_ASSOCIATED` and `UISP_BULK_ONBOARDING` audit events.

### Implemented

- administrator-configurable read-only UISP connector;
- connector credential encrypted at rest;
- connector test;
- current UISP Network device endpoint integration used by runtime code;
- MAC normalization/matching for association;
- candidate preview;
- Device association;
- stable external UISP Device identifier;
- manual Device refresh;
- normalized identity/model/serial/MAC/IP/firmware/status/last-seen fields;
- audit/contextual GUI behavior around association/refresh;
- NSM Customer/Site ownership preserved as authoritative;
- periodic worker synchronization of associated Devices (configurable 5–1440 min, default 15) from one bounded UISP device-list read, plus admin **Sincronizza ora**;
- matching only by stored UISP Device ID: no automatic association by MAC, MAC mismatch blocks the update and raises a conflict Action Center issue;
- Devices missing from UISP are flagged (`sync_state=missing`, Action Center) without changing their NSM state;
- exponential backoff on connector failures (max 6 h) and a connector Action Center issue after 3 consecutive failures, resolved on the next success;
- audit only for material observed changes (identity/model/serial/MAC/IP/firmware), not for routine status churn.

Validation pending: periodic sync against a real UISP instance (the consumed endpoint is the same one used by manual refresh).

### Not implemented yet

- historical monitoring normalization;
- bulk onboarding/association;
- backup/snapshot workflow through UISP capabilities;
- UISP-backed diagnostics;
- Ubiquiti firmware execution workflow;
- automated Ubiquiti CVE/lifecycle correlation.

These future features must only be exposed when the actual vendor API capability exists and is verified.

## 14. TR-069 / ACS-managed CPE

### Implemented foundation only

- vendor/onboarding model can represent TP-Link/other CPE;
- management-source/capability model can represent TR-069/ACS dependency;
- unsupported ACS-dependent actions can be shown as unavailable rather than falsely executable.

### Not implemented

- production ACS connector;
- ACS Device discovery/association;
- TR-098/TR-181 normalization profiles;
- vendor parameter profiles;
- monitoring/history through ACS;
- TR-069 diagnostics;
- capability-aware backup/restore through ACS;
- firmware deployment/verification through ACS.

Until an actual connector is selected and implemented, roadmap documentation should stay technology-neutral where possible rather than accumulating unused third-party API references.

## 15. Vulnerability and security management

### Implemented foundation

- security advisory/vulnerability data model;
- Device-impact model;
- vulnerability worklist/drill-down UI;
- severity/status filtering foundations;
- public read-only vulnerability API;
- Customer security drill-down foundations;
- Action Center integration model for security findings.

### Advisory ingestion (SEC-01)

- source: **NVD CVE API 2.0**, read-only, for `cpe:2.3:o:mikrotik:routeros:*` (`virtualMatchString`); optional API key stored encrypted;
- first run is a full load, later runs read only CVEs modified since the last success (`lastModStartDate`/`lastModEndDate`, 2 h overlap, ≤ 119 days, otherwise full load again);
- pages of up to 2000 records with the documented pause between requests (6.5 s without key, 1 s with key);
- idempotent upsert by CVE id; an unchanged record only refreshes `fetched_at`; provenance kept on the advisory (`source`, NVD `vulnStatus`, published/modified, fetched);
- records that cannot be normalized are skipped and listed in the admin page (bounded list) instead of aborting the run;
- transport/HTTP failures back off exponentially (max 24 h); after 3 consecutive failures one Action Center issue is opened and resolved by the next success; audit events for configuration, runs and failures;
- worker cycle: ingestion when due (default every 6 h), matching every 15 min;
- requires outbound HTTPS from the app and worker containers to `services.nvd.nist.gov` (manual *Aggiorna ora* runs in the app, scheduled runs in the worker).

### Device ↔ advisory matching (SEC-02)

- normalized rules from NVD configurations: exact version (with `rc`/`beta` update), start/end including/excluding bounds, AND configurations limited to hardware models;
- only MikroTik Devices are evaluated (product RouterOS); never by brand or product alone;
- version comparison with `routeros_version` (pre-releases before releases);
- explicit states: affected → open finding with fixed version, matched range, confidence (`high`; `medium` when the source gives no upper bound) and evidence; not affected / not applicable → no finding; unknown (no or unparseable version, unknown model for a hardware-limited rule, CVE without versions) → listed as *Non valutabili* on the CVE page, never counted as exposed;
- findings that stop matching (upgrade, CVE rejected by NVD) are resolved with the evidence of the version; a later match reopens them; manually entered advisories are never changed by the matcher.

### Remediation lifecycle (SEC-03)

- states: open → planned → in_progress, exception (justification + expiry ≤ 1 year, returns to open when it expires), resolved;
- operators with `security.remediate` (Technician, Admin) change status with a note on the finding page (`/security/findings/{id}`); read-only roles see status and history only;
- source-managed findings (NVD) are resolved only by evidence (installed version out of range, CVE rejected); findings of manual advisories can be closed by the operator with a mandatory note;
- every change — operator or automatic (detected, reopened, resolved, exception expired) — is kept in `vulnerability_history` with actor, note and details, also after resolution;
- link to the Device firmware plan, pre-labelled with the fixed version when known;
- one Action Center issue per Device while it has critical/high findings still *open* (not planned, in progress or excepted); updated with the count and resolved automatically.

### Not implemented yet

- other advisory sources and non-MikroTik product mappings;

## 15b. Incidents and timeline (INC-01 / INC-02)

### Implemented

- Incident per Customer with optional Site, involved Devices, severity, start and resolution time, status open → investigating → monitoring → resolved (reopen allowed); audit events for creation, status, notes and device changes;
- permissions: `incidents.read` (all roles), `incidents.write` (Technician, Operator, Admin);
- timeline rebuilt on demand from recorded evidence in the window *start − 6 h … (resolution or now) + 1 h*: audit events of the involved Devices and Customer-level events, Action Center issues opened/resolved, backup runs, agent jobs completed/failed/expired, vulnerability state changes;
- operator notes (*Nota* / *Azione eseguita*) with their own timestamp, shown with a distinct style and label; filters *Tutto / Fatti osservati / Note operatore*;
- deterministic ordering (time, source, record id); per-source cap with an explicit truncation notice;
- entry points: sidebar *Incidenti*, *Apri incidente* on every Device header, *Incidenti* on the Customer header.

### Root cause workflow (INC-03)

- candidate correlations computed from the timeline only, always labelled *euristiche, da verificare*, with reason and confidence: change shortly before the start (configuration, firmware, RouterBOOT, agent update, restore: medium within 1 h, low within 6 h), Action Center issue opened within 15 min of the start (medium), failed backup/job within 30 min (low, probably a symptom), critical/high vulnerability opened before the start (low);
- an operator records a candidate as a hypothesis (re-derived server side, with an evidence snapshot) or writes one, with a category (configuration, firmware, hardware, power, connectivity, upstream, security, manual intervention, other);
- only an explicit confirmation with supporting evidence makes a hypothesis the root cause; confirming another replaces it, rejecting clears it; every decision keeps author, time and note and is audited;
- incident list shows the confirmed root cause category or *da confermare*.

### Reporting (INC-04)

- periodic evidence reports (PDF section *7. Incidenti*): incidents overlapping the period, by severity and status, resolved in the period with mean hours to resolve, confirmed vs pending root causes and confirmed causes by category, table of incidents; only operator-confirmed root causes are reported;
- *Esporta evidenza PDF* on the incident page (`reports.generate`): summary, involved Devices, confirmed root cause with the stated evidence, all hypotheses with origin and decision, full timeline marked *Fatto* / *Nota*; stored in the report archive with SHA-256, downloads re-verify integrity, `INCIDENT_EVIDENCE_EXPORTED` and `REPORT_GENERATED`-equivalent audit;
- exports listed on the incident page and in *Audit → Report* with a link back to the incident.

### Boundary

- heuristics never become facts or root causes by themselves.

## 15c. Compliance baseline (COMP-01 / COMP-02)

### Implemented

- baselines with scope global, vendor, customer, site or Device; each lists the controls it sets (*Attivo* / *Disattivato* with parameters), the others are inherited; the effective baseline of a Device merges every enabled applicable baseline from global to Device; every save creates a new version, results record baseline and version;
- controls, decided only from evidence NSM holds:
  - *Firmware senza update di sicurezza* (strict mode: any update fails);
  - *Nessuna CVE grave non gestita* (critical/high still *Aperta*; optional: exceptions fail) — MikroTik only, unknown without an advisory source or with an unknown version;
  - *Backup recente riuscito* (max age) and *Restore test superato* (max age) — not applicable when the vendor has no executable backup method;
  - *Apparato supportato dal vendor* (EOS fails, EOL optional);
  - *Agent NSM attivo* (heartbeat age) — MikroTik only;
  - *Configurazione approvata senza drift* — MikroTik only;
  - *Servizi in chiaro disabilitati* (telnet, FTP, optional www) from the latest RouterOS export: compliant only with explicit `disabled=yes`, explicit `disabled=no` fails, missing values are *nessuna evidenza* because the compact export omits defaults;
- results: *Conforme*, *Non conforme*, *Nessuna evidenza*, *Non applicabile*; missing vendor capability is never a failure;
- `/compliance` (device × control matrix, quick filters, customer/control filters, *Valuta ora*), `/compliance/baselines` (list, create, edit), one-click default global baseline; worker re-evaluates every 30 minutes; audit events for baseline changes and evaluations;
- permissions: `compliance.read` (all roles), `compliance.manage` (Technician, Admin).

### Findings lifecycle (COMP-03)

- a non-compliance is a failing result; its evaluated status is never overwritten by hand;
- `compliance.manage` users can take it in charge (note) or grant an exception with justification and expiry within one year; the failure is then shown *in eccezione* and no longer counted as to handle; exceptions can be revoked and expire by themselves;
- when a later evaluation is no longer failing, acknowledgement and exception are cleared automatically;
- every evaluation change and operator decision is kept in `compliance_result_history` (also after the result is compliant again) and audited;
- one Action Center issue per Device with failing results not in exception, updated with the count and resolved automatically;
- result page `/compliance/results/{id}` linked from every cell of the matrix; *In eccezione* quick filter.

### Views and reports (COMP-04)

- *Compliance* tab on every Device: each control with result, evidence and handling (exception expiry, taken in charge, *Gestisci*);
- Customer *Sicurezza* tab: evaluated Devices, Devices with unhandled non-compliance, results by state, most failing controls;
- periodic evidence report section *8. Compliance*: evaluated Devices, results by state, failures by control, active exceptions with reason and expiry; scopes without a baseline are stated as not evaluable; *no evidence* and *not applicable* are explicitly not compliance;
- CSV appends `compliance_failed_controls` and `compliance_exceptions` per Device; archive summary stores the number of Devices with unhandled non-compliance.

## 16. Lifecycle / EOL / EOS

### Implemented foundation

- lifecycle fields/status model;
- customer/vendor/state/search drill-down UI;
- lifecycle worklist foundations.

### Lifecycle catalog (LIFE-01)

- *EOL / EOS → Catalogo lifecycle*: one record per vendor model with EOL (end of sale/life) and EOS (end of support) kept separate, aliases, mandatory source (name and optional link) and the date the source was checked; a record without dates means *supported when the source was checked*;
- entry form and CSV import/export (`vendor,model,aliases,eol_date,eos_date,source,source_url,evidence_date,notes`); an import is validated as a whole and writes nothing when any row is invalid; models and aliases must identify one record only;
- no vendor publishes a machine-readable EOL feed for these product lines, so the catalog is maintained from vendor pages/bulletins and every value keeps its provenance; NSM never invents a date.

### Device correlation (LIFE-02)

- Devices are matched only on exact normalized model (whitespace/case; MikroTik `RouterBOARD xxx` = `RBxxx`) or a declared alias; a partial match is *ambigua* with the candidate models listed and is never applied;
- origin shown on every Device: *Da catalogo*, *Impostato manualmente*, *Corrispondenza ambigua*, *Modello non in catalogo*, *Modello non rilevato*; without a match the state is explicitly *unknown*, never supported;
- per-Device manual value with mandatory source and verification date (`lifecycle.manage`), never overwritten by the catalog until handed back to it; values present before the catalog are kept as manual;
- the catalog is re-applied on every change and hourly by the worker, so supported models become EOL/EOS when their dates pass; every status change is audited (`LIFECYCLE_STATUS_CHANGED` with source and evidence date);
- worklist chips *In scadenza 12 mesi* and *Senza dato lifecycle* (grouped by model, with *Aggiungi al catalogo*).

### Remediation and replacement evidence (LIFE-03)

- every EOL/EOS Device gets a remediation record: *Da gestire*, *Sostituzione pianificata* (target date and plan), *In eccezione* (justification, expiry within one year), *Sostituito* (replacement Device of the same customer) or *Dismesso* (note); decisions need `lifecycle.manage`, are kept in a history with author and note and audited (`LIFECYCLE_REPLACEMENT_PLANNED`, `LIFECYCLE_EXCEPTION_GRANTED`, `LIFECYCLE_DEVICE_REPLACED`, `LIFECYCLE_DEVICE_DECOMMISSIONED`, `LIFECYCLE_REMEDIATION_REOPENED`);
- one Action Center issue per Device (`lifecycle`, critical for EOS) while it is still to handle, its planned replacement is overdue or its exception expired; resolved automatically otherwise; re-evaluated with the catalog and by the worker;
- Device lifecycle page shows the decision panel (read-only without `lifecycle.manage`); the EOL/EOS worklist has a *Gestione* column;
- evidence reports: counts by remediation state, overdue plans, Devices still to handle, active exceptions with reason and expiry; CSV column `lifecycle_remediation`.

## 17. Public read-only API

### Implemented

API-key protected read-only routes include:

- Customers;
- Devices;
- individual Device detail;
- backup operational data;
- vulnerability data;
- firmware data.

The API supports scoped permissions and Customer isolation. Browser API-key management and machine-facing API error contracts are intentionally separate.

API-key lifecycle (`app/api_key_lifecycle.py`, migration 0024):

- every authenticated request records time, client IP and a request counter on the key;
- *Ruota* issues a new key with the same name, scopes, Customer and validity length (shown once); the old key stays valid for 24 h or 7 days, or is revoked at once, and points to its replacement (`API_KEY_ROTATED` audit event, without secrets);
- review states on *Amministrazione → API Keys*: expired but still active, expiring within 14 days, in replacement, never used after 30 days, unused for 90 days, no expiry; a summary lists what needs action.

## 18. Deployment and operations

### Implemented

Default container stack:

- PostgreSQL;
- Redis;
- one-shot Alembic migration service;
- FastAPI/Uvicorn API service;
- worker service;
- Caddy front end.

Deployment/update behavior includes:

- logical PostgreSQL `pg_dump` before validated update/migration;
- Alembic migrations;
- application/container rebuild/restart;
- health verification;
- protection against using the runtime directory itself as the destructive update source;
- backup-storage permission preparation/repair for the non-root application runtime user;
- systemd-based auto-update tooling;
- host-specific auto-update configuration outside Git;
- sanitized deployment failure report retained locally by default;
- public-repository-safe default that does not publish runtime diagnostics remotely.

Backup, restore and observability (runbook: [`OPERATIONS.md`](OPERATIONS.md)):

- `./manage.sh backup-db`, non-destructive `./manage.sh restore-drill` (restores the latest dump into a temporary database, checks tables, schema revision and devices, drops it and appends the result to `restore-drills.jsonl`) and `./manage.sh restore-db <dump>` (typed confirmation, safety dump first, then restore and migrations);
- every periodic worker task is isolated (`app/worker_status.py`): an exception is logged and recorded and the other tasks keep running; the worker writes a heartbeat and the last outcome of each task to Redis;
- `/health` adds a `worker` field (alive, heartbeat age, failing tasks) without changing its status code;
- *Amministrazione → Sistema* (admins): version, schema revision, database size, free space on the backup volume, worker heartbeat and task outcomes, platform dumps (stale after 7 days) and restore drills (overdue after 90 days).

## 19. Public repository safety

### Implemented

- documented public-data-safety policy;
- deterministic synthetic fixture conventions;
- automated repository/test guard for high-signal secrets and deployment-derived fixture mistakes;
- RFC 5737 documentation networks in public test/demo data where applicable;
- locally administered MAC fixtures;
- synthetic Customer/Device/identity labels.

A production identifier must never be copied into a test merely because it reproduces a bug. Reproduce the behavior with synthetic data and retain sensitive evidence outside the public repository.

## 20. Reports

### Implemented (REP-01 / REP-02)

- manual operational evidence report at `/audit/reports` for all Customers or one Customer and a date range (max 366 days, not in the future);
- PDF (dependency-free writer, A4, deterministic output) with sections: inventory, firmware, vulnerabilities, lifecycle EOL/EOS, backup coverage/runs/restore tests, Action Center, device list;
- CSV with one evidence row per Device (identity, firmware, lifecycle, open vulnerabilities, backup readiness, last successful backup, last restore test);
- sections without data are stated as **not evaluable** (e.g. no advisory data is never reported as zero vulnerabilities); "protected" requires an executable backup method, not just a policy;
- immutable archive in PostgreSQL (included in `pg_dump`) with SHA-256, size, scope, period and summary; `REPORT_GENERATED` / `REPORT_DOWNLOADED` audit events;
- downloads re-verify SHA-256 and block tampered content (`REPORT_INTEGRITY_FAILED`);
- permissions: `reports.read` to browse/download, `reports.generate` to create;
- explicit disclaimer: evidence supports NIS2-oriented programs, it does not attest compliance.

### Security evidence in reports (SEC-04)

- vulnerability section states the advisory source (NVD, advisory count, last update) and warns when the automatic source has not updated for more than 7 days; manual-only data is stated as such;
- open findings by remediation state (open, planned, in progress, exception) and critical/high still unhandled;
- findings resolved in the period by reason (version upgraded, manual, CVE rejected) and mean days to resolve;
- active exceptions with expiry and the justification recorded when granted;
- table of open critical/high findings (CVE, device, installed, fixed in, state), bounded to 300 rows;
- MikroTik devices whose version cannot be assessed are counted, never reported as not vulnerable;
- CSV appends `unhandled_severe_vulnerabilities` and `vulnerabilities_in_exception` per Device (existing columns unchanged).

### Scheduled reports (REP-03)

- monthly / quarterly / annual schedules per scope (all Customers or one Customer) and format;
- each schedule generates the last closed calendar period (application timezone) exactly once (`last_period_end` idempotency);
- failures retry with exponential backoff (15 min → max 6 h), are audited (`REPORT_SCHEDULE_FAILED`) and open one Action Center issue after 3 consecutive failures, resolved by the next success;
- create / suspend / delete schedules from `/audit/reports` (`reports.generate`), audited; deleting a schedule keeps its archived reports.

### Not implemented yet

- delivery channels (REP-04);
- incident/compliance sections (depend on INC/COMP features).

## 21. Approved but not yet implemented product areas

The following are approved roadmap areas, not current runtime capabilities:

- Incident Timeline / Root Cause workflow;
- Compliance Baseline/evaluation/remediation workflow;
- Scheduled Executive / NIS2-oriented report generation and delivery (manual generation and archive are implemented);
- mature delegated RBAC;
- production DR/restore validation program;
- additional vendor/connector capabilities beyond those explicitly described above.

See [`ROADMAP.md`](ROADMAP.md) for order, PR sizing and acceptance gates.

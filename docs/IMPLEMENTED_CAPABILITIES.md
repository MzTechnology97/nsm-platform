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

### RouterOS 7.12.1 — legacy family

Physically validated:

- legacy/bodyless enrollment;
- heartbeat;
- basic inventory/current state.

Implemented in `main`:

- legacy transport;
- historical telemetry ingestion path.

Physical history validation is still pending.

Structured configuration snapshot parity is **not yet in `main`**; it is under physical acceptance in the active focused implementation PR described in `ROADMAP.md`.

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

### Legacy gap

RouterOS 7.12.x structured snapshot parity is not in `main` yet. The active implementation uses a fixed allow-listed plain-text transport and requires physical acceptance before merge.

## 10. MikroTik diagnostics

### Implemented foundations

- ping diagnostic job;
- traceroute diagnostic job;
- neighbor-related diagnostic collection;
- DHCP/log diagnostic views/jobs where supported;
- bounded structured job/result history;
- support-snapshot foundation on supported modern Agents;
- contextual GUI handling for human-triggered actions.

### Still incomplete

- richer result visualization for all diagnostic types;
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

### Validation pending

Focused modern-Agent hardening for no-progress upload handling and cleanup of RouterOS temporary files after failed uploads is still under physical acceptance and therefore not described as merged capability.

### Restore-test evidence (MTK-03)

- operator-recorded restore tests per archived artifact (method: isolated lab, spare device, configuration review; target label/RouterOS version; result passed/partial/failed; notes; execution time);
- NSM re-verifies file presence, size and SHA-256 at recording time; a test cannot be recorded as passed against a non-intact artifact;
- artifact identity (filename, type, SHA-256, size, source RouterOS version from `.rsc` header) is copied onto the record, so evidence survives retention/deletion;
- failed/partial tests open a device-scoped Action Center issue resolved by the next passed test; every record emits `BACKUP_RESTORE_TEST_RECORDED`;
- Backup Explorer shows the latest restore result per artifact and per Device; device history page `/devices/{id}/backups/restore-tests`;
- NSM never restores a production Device automatically.

### Not implemented

- legacy RouterOS 7.12.x backup transport parity;
- report integration of restore-test evidence.

## 12. MikroTik firmware and RouterBOOT

### Implemented foundation

- customer-aware firmware worklist;
- firmware readiness Agent operation;
- safe upgrade-plan model/workflow;
- explicit approval step;
- download-only package staging flow;
- activation/reboot flow;
- Agent acknowledgement/completion paths;
- post-action state foundation;
- explicit execution windows for Agent-owned staging (60 min) and activation (15 min) jobs; the worker closes a plan whose phase job expired or disappeared instead of leaving it active forever;
- operator cancellation during staging/queued activation withdraws undelivered jobs, refuses cancellation once activation reached the Agent, and late Agent reports can no longer move a cancelled/expired plan;
- RouterBOOT lifecycle/action foundations;
- contextual GUI feedback around browser actions;
- pre-upgrade backup gates/controls where applicable.

### Still incomplete

- authoritative automated vendor firmware catalog/intelligence;
- complete security-advisory-to-target-version automation;
- comprehensive physical validation across supported hardware/version families.

## 13. Ubiquiti / UISP

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

### Not implemented yet

- automated authoritative advisory ingestion pipeline;
- reliable vendor/product/version matching at production maturity;
- automated remediation lifecycle from source advisory to confirmed fixed state;
- full evidence/report integration.

## 16. Lifecycle / EOL / EOS

### Implemented foundation

- lifecycle fields/status model;
- customer/vendor/state/search drill-down UI;
- lifecycle worklist foundations.

### Not implemented yet

- automated authoritative EOL/EOS source ingestion;
- complete model/version correlation;
- mature remediation/replacement workflow and evidence automation.

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

## 19. Public repository safety

### Implemented

- documented public-data-safety policy;
- deterministic synthetic fixture conventions;
- automated repository/test guard for high-signal secrets and deployment-derived fixture mistakes;
- RFC 5737 documentation networks in public test/demo data where applicable;
- locally administered MAC fixtures;
- synthetic Customer/Device/identity labels.

A production identifier must never be copied into a test merely because it reproduces a bug. Reproduce the behavior with synthetic data and retain sensitive evidence outside the public repository.

## 20. Approved but not yet implemented product areas

The following are approved roadmap areas, not current runtime capabilities:

- Incident Timeline / Root Cause workflow;
- Compliance Baseline/evaluation/remediation workflow;
- Scheduled Executive / NIS2-oriented report generation and delivery;
- complete report/evidence archive;
- mature delegated RBAC;
- production DR/restore validation program;
- additional vendor/connector capabilities beyond those explicitly described above.

See [`ROADMAP.md`](ROADMAP.md) for order, PR sizing and acceptance gates.

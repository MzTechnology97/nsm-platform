# NSM Platform — Implementation Queue

Status: living, version-independent queue

This document lists **remaining implementation work as focused PR-sized objectives**. It is intentionally concise: detailed specifications belong in dedicated feature documents/PRs.

## Operating rule

- One implementation PR = one coherent, testable feature/vertical slice.
- Do **not** open every future PR simultaneously.
- Keep one primary implementation flow active; additional work starts only after the current item is merged or explicitly BLOCKED.
- Documentation/design PRs may exist in parallel because they do not modify runtime behavior.
- Completed behavior moves to `IMPLEMENTED_CAPABILITIES.md` and disappears from this queue.
- Customer-edge/device-management work has priority; ISP backbone/core management is out of current scope.

## Status notation

- `ACTIVE` — implementation currently being developed.
- `BLOCKED` — cannot proceed; PR must contain blocker and next action.
- `READY FOR TEST` — implementation complete enough for required validation.
- `READY FOR MERGE` — validation complete and CI green.
- `QUEUED` — future focused PR; do not open until it becomes the next objective.
- `SPEC` — specification/documentation PR only.

---

## 1. MikroTik

### MT-01 — RouterOS compatibility resolver and real-device matrix

Status: existing design PR #48 / implementation follow-up required.

Scope for implementation PR:

- centralized compatibility-family resolver;
- version/feature detection;
- validated legacy/modern family selection;
- stored agent family/transport/capability profile;
- re-evaluation after firmware change;
- fail-safe unknown/unvalidated releases;
- real-device regression matrix.

### MT-02 — Agent self-update and rollback

Status: QUEUED

Separate PR after compatibility-family rules are stable.

- controlled agent source update;
- checksum/drift detection;
- previous-known-good source retention;
- post-install heartbeat verification;
- rollback/recovery on parser/start failure;
- fleet view of outdated agent source.

### MT-03 — Advanced diagnostic result UX

Status: QUEUED

- structured result modal/detail page;
- ping/traceroute/neighbor/log/DHCP formatting;
- bounded timeout/cancel behavior;
- optional bounded Torch/bandwidth diagnostics only with explicit safety constraints.

### MT-04 — Restore-test evidence

Status: QUEUED

- restore-test workflow/evidence;
- last successfully tested artifact;
- compatibility checks before binary restore planning;
- no arbitrary restore/script execution.

### MT-05 — Firmware intelligence

Status: QUEUED

Execution foundations already exist; this PR adds decision intelligence only:

- firmware catalog/source evidence;
- approved channels/targets;
- security/bugfix classification;
- model/architecture compatibility;
- fleet rollout policy/maintenance windows;
- post-update verification and failed-update remediation.

Configuration history/drift detection and Interface Health are already implemented and are not roadmap items.

---

## 2. Ubiquiti / UISP

### UBNT-01 — Periodic UISP synchronization

Status: QUEUED

- background sync;
- associated Device refresh without manual action;
- retry/backoff;
- deleted/missing/conflicting Device handling;
- connector health and recovery events.

### UBNT-02 — Monitoring normalization

Status: QUEUED

- uptime/CPU/memory/temperature where exposed;
- interface traffic/state;
- AirMax/LTU wireless metrics where exposed;
- history/retention;
- Operations Monitoring integration.

### UBNT-03 — Bulk onboarding/association

Status: QUEUED

- search/preview candidates;
- bulk MAC association;
- Customer/Site assignment;
- duplicate/conflict preview and audit.

### UBNT-04 — Backup/snapshot capability

Status: QUEUED

Implement only mechanisms safely exposed by UISP/vendor APIs. Unsupported families remain explicitly unsupported.

### UBNT-05 — Diagnostics

Status: QUEUED

Implement only safe diagnostics exposed by UISP/vendor APIs, with normalized result UI and audit.

### UBNT-06 — Firmware workflow

Status: QUEUED

- available/recommended firmware intelligence;
- compatibility/security classification;
- controlled upgrade where API supports it;
- post-upgrade verification;
- failure handling.

### UBNT-07 — CVE and lifecycle correlation

Status: QUEUED

Map UISP-observed model/firmware to security/lifecycle intelligence.

---

## 3. TP-Link and other TR-069 vendors

### TR069-01 — GenieACS connector

Status: QUEUED

- connector configuration/credentials/TLS;
- connection test;
- bounded northbound API client;
- connector health and audit.

### TR069-02 — Device discovery and association

Status: QUEUED

Lookup/association by MAC, serial, OUI, ProductClass and ACS Device ID with ambiguity/conflict handling.

### TR069-03 — Normalized inventory

Status: QUEUED

Normalize vendor/model/serial/MAC/software/hardware/WAN-management state/last Inform and evidence freshness.

### TR069-04 — Vendor parameter profiles

Status: QUEUED

- TR-098/TR-181 profiles;
- TP-Link mappings;
- additional vendor mappings;
- model/firmware overrides;
- capability detection from parameter tree.

### TR069-05 — Monitoring

Status: QUEUED

Collect only parameters exposed reliably by the CPE/ACS and integrate them into normalized monitoring/history.

### TR069-06 — Diagnostics

Status: QUEUED

ACS-mediated ping/traceroute/download-upload/line diagnostics where supported and safe.

### TR069-07 — Backup/restore

Status: QUEUED

Capability-driven configuration backup/restore for validated vendor/model profiles only.

### TR069-08 — Firmware workflow

Status: QUEUED

Validated ACS firmware tasks, maintenance windows, reconnect/post-update verification and failure handling.

---

## 4. Cross-vendor operations

### OPS-01 — Global customer-device monitoring

Status: QUEUED

- customer/Site/vendor/status filters;
- normalized health/last seen/metric freshness;
- vendor-aware metrics;
- thresholds, actionable alarms and recovery;
- no ISP-core topology/traffic engineering.

### OPS-02 — Connector/agent health

Status: QUEUED

- UISP/ACS/future connector health;
- last success/failure/staleness;
- retry/backoff visibility;
- Action Center failure/recovery integration.

### OPS-03 — Job framework maturation

Status: QUEUED

- normalized job states/results;
- cancel before start;
- controlled retry/requeue;
- idempotency for disruptive actions;
- structured result rendering.

### OPS-04 — Action Center completion

Status: QUEUED

- deduplicated issues across backup/firmware/security/lifecycle/connector/agent/monitoring;
- assignment/due dates/timeline;
- automatic recovery where appropriate;
- customer-specific worklist.

### OPS-05 — Notification rules and external channels

Status: QUEUED

- rules/preferences/throttling;
- recovery notifications;
- Email;
- Telegram;
- generic webhook;
- delivery/retry evidence.

---

## 5. Security and lifecycle

### SEC-01 — Security advisory ingestion

Status: QUEUED

Vendor advisory sources first, plus NVD/CISA/CERT where relevant; feed health, deduplication and source evidence.

### SEC-02 — Device-to-CVE matching

Status: QUEUED

Vendor/product/version normalization, affected/fixed range matching, confidence/evidence and automatic re-evaluation after firmware change.

### SEC-03 — CVE remediation lifecycle

Status: QUEUED

Open/planned/in-progress/resolved/exception/not-applicable states, remediation evidence and firmware-plan linkage.

### SEC-04 — EOL/EOS ingestion and remediation

Status: QUEUED

Authoritative lifecycle sources, dates/evidence, upcoming thresholds and Action Center workflow.

---

## 6. Incident management

### INC-01 — Incident model and timeline aggregation

Status: SPEC — roadmap PR #62.

### INC-02 — Incident UI and evidence

Status: QUEUED

### INC-03 — Root Cause correlation workflow

Status: QUEUED

### INC-04 — Incident evidence export/report integration

Status: QUEUED

Do not combine INC-01 through INC-04 into one runtime PR.

---

## 7. Compliance Baseline

### COMP-01 — Baseline model and inheritance

Status: SPEC — roadmap PR #63.

### COMP-02 — Capability-aware evaluation engine

Status: QUEUED

### COMP-03 — Findings/remediation lifecycle

Status: QUEUED

### COMP-04 — Compliance UI/report integration

Status: QUEUED

Do not combine COMP-01 through COMP-04 into one runtime PR.

---

## 8. Executive / NIS2 reporting

### RPT-01 — Report model and archive/hash evidence

Status: SPEC — roadmap PR #64.

### RPT-02 — Manual PDF/CSV report generation

Status: QUEUED

### RPT-03 — Scheduled recurring reports

Status: QUEUED

### RPT-04 — Delivery channels/evidence

Status: QUEUED

Do not combine RPT-01 through RPT-04 into one runtime PR.

---

## 9. Administration and production readiness

### ADMIN-01 — Mature RBAC administration

Status: QUEUED

Role editor, delegated/scoped access, API-key scope administration and audit of authorization changes.

### PROD-01 — Platform service health and operational logging

Status: QUEUED

Application/worker/PostgreSQL/Redis/service health, important log viewer/filtering, storage/certificate warnings and failure/recovery notifications.

### PROD-02 — NSM backup/restore and DR validation

Status: QUEUED

Database/evidence backup, documented restore, periodic restore test and DR runbook.

---

## Selection order

Unless an urgent defect blocks current functionality, prefer this dependency order:

1. RouterOS compatibility resolver / real-device validation;
2. UISP periodic sync + monitoring;
3. GenieACS connector + TR-069 discovery/inventory;
4. firmware/security/lifecycle intelligence;
5. Action Center/notifications;
6. Compliance Baseline;
7. Incident Timeline / Root Cause;
8. Scheduled Executive / NIS2 Reports;
9. RBAC and production-hardening completion.

When a queued item becomes active, open its dedicated implementation PR and keep the PR body continuously updated with `Current step`, `Next step`, tests and blocker state.

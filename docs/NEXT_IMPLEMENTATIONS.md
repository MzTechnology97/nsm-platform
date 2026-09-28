# NSM Platform — Next Implementations

Status: living version-independent product backlog

This document is the master list of functionality still required to bring NSM to the complete operational target defined from:

- the OptiWize reference material supplied during product discovery (screenshots, onboarding flow, wiki/helpdesk/API/changelog references and observed operational UX);
- the agreed NSM product architecture in `PRODUCT_REQUIREMENTS.md`;
- real-device testing and gaps discovered during development.

It contains **remaining or incomplete work only**. Functionality already implemented belongs in `IMPLEMENTED_CAPABILITIES.md` and should not remain duplicated here once complete.

The list is intentionally **not tied to a Core/release number**. Development can consume items in dependency order and update this file continuously.

## Document maintenance rule

For every completed feature:

1. implementation must be merged and operational, not just represented by a menu entry or model;
2. add the actually delivered behavior to `IMPLEMENTED_CAPABILITIES.md`;
3. remove the completed item here, or reduce it to the genuinely unfinished sub-items;
4. preserve explicit limitations by vendor/firmware/capability;
5. update tests and capability declarations together with the feature;
6. never present a planned integration as executable in the UI.

## Global product rules

These rules apply to every item below:

- no arbitrary remote shell from the NSM cloud/control plane;
- remote actions must be predefined, authenticated and auditable jobs;
- no global shared device secret;
- vendor/device capabilities must be truthful and server-enforced;
- unsupported operations must show `connector required`, `capability unavailable`, `not implemented` or `unsupported`, rather than appearing executable;
- observed state and desired/target state must remain separate;
- Sites remain customer-local;
- the supplied OptiWize FTP backup example remains excluded; NSM should use secure outbound transports/connectors instead;
- NSM should integrate with a mature ACS for TR-069 rather than becoming an ACS itself;
- security, firmware, backup, restore and remote actions require audit evidence;
- the system supports NIS2-oriented evidence but must not claim that installation alone creates compliance.

---

# A. Cross-vendor platform work

## A1. Unified capability registry

Complete a first-class vendor/device capability registry used consistently by backend, UI, scheduler and jobs.

Remaining work:

- [ ] Define one normalized capability contract covering inventory, monitoring, historical telemetry, snapshot, backup, restore, diagnostics, firmware, reboot, logs and agent/connector actions.
- [ ] Resolve capabilities from vendor + model + firmware + connector/agent generation + detected feature set.
- [ ] Persist the resolved capability profile and last evaluation timestamp.
- [ ] Re-evaluate capabilities after firmware, model/identity or connector changes.
- [ ] Make all action buttons and schedulers consume the same registry.
- [ ] Prevent manually crafted requests from bypassing capability restrictions.
- [ ] Expose a human-readable reason for every disabled capability.
- [ ] Add automated capability-regression tests per vendor/firmware family.

## A2. Cross-vendor normalized monitoring

Operations → Monitoring must become the global operational view rather than a placeholder or vendor-specific-only experience.

Remaining work:

- [ ] Global fleet monitoring page with server-side pagination/filtering.
- [ ] Customer, Site, vendor, Device type and status filters.
- [ ] Search by Device/customer/identity/MAC/IP/serial.
- [ ] Normalized online/offline/unknown state.
- [ ] Last seen / data freshness.
- [ ] Uptime.
- [ ] CPU utilization.
- [ ] memory utilization.
- [ ] temperature where exposed.
- [ ] selected interface state/traffic/error metrics where available.
- [ ] wireless signal/CCQ/capacity/noise metrics where meaningful.
- [ ] metric source and age so stale telemetry cannot look current.
- [ ] historical charts with normalized time ranges.
- [ ] vendor-aware empty states when a metric is not exposed.
- [ ] threshold/policy model for actionable monitoring alarms.
- [ ] recovery events when a monitored fault returns to normal.
- [ ] Action Center and notification integration for configured actionable alarms.
- [ ] data retention/downsampling policy by metric class.

## A3. Vendor-neutral connector/agent health

- [ ] Global connector health worklist for UISP, ACS and future connectors.
- [ ] Last successful sync/poll.
- [ ] last failed sync/error.
- [ ] stale connector threshold.
- [ ] credential/certificate-expiry state where applicable.
- [ ] automatic Action Center issue on sustained connector failure.
- [ ] automatic recovery/issue closure after successful recovery.
- [ ] connector-specific rate-limit/backoff handling.
- [ ] operator-visible retry status.

## A4. Job framework maturation

- [ ] Unified job result schema across agents/connectors.
- [ ] Queued/delivered/running/success/failed/expired/cancelled states where technically observable.
- [ ] Cancel action for jobs that have not started.
- [ ] Retry/requeue workflow with audit trail.
- [ ] Idempotency protection for higher-risk actions.
- [ ] Per-operation timeouts and expiry.
- [ ] Better formatted result rendering by job type rather than raw payload dumps.
- [ ] Detail modal/window for command results, as identified during real-device testing.
- [ ] Result download/export for large diagnostic output when appropriate.

## A5. Optional local/site collector

For networks where central direct access is undesirable or impossible:

- [ ] Define collector identity, enrollment and credential rotation.
- [ ] Outbound-only HTTPS control channel.
- [ ] Local vendor API/SSH/ACS access without exposing customer management networks centrally.
- [ ] Explicit allow-listed collector jobs.
- [ ] Connector health and version reporting.
- [ ] Safe update/rollback of collector software.
- [ ] Site/customer scoping.
- [ ] Audit every action proxied through a collector.

This can provide the OptiWize-like OOB/local-management role without enabling arbitrary cloud shell access.

---

# B. MikroTik completion

The MikroTik integration is the most developed vendor path, but compatibility and full production validation remain incomplete.

## B1. RouterOS compatibility families and provisioning resolver

- [ ] Replace broad firmware assumptions with a centralized compatibility registry/resolver.
- [ ] Detect RouterOS version before final agent delivery.
- [ ] Add feature probes where version alone is insufficient.
- [ ] Maintain separate validated agent families where RouterOS scripting generations require them.
- [ ] Record exact agent family, source revision/checksum and transport on the Device.
- [ ] Re-evaluate agent compatibility after RouterOS upgrade/downgrade.
- [ ] Automatically offer/schedule agent migration when firmware makes a different family appropriate.
- [ ] Fail safely on unknown/unvalidated RouterOS releases.
- [ ] Build and maintain a real-device/CHR regression matrix for representative releases and architectures.
- [ ] Decide explicit support policy for older RouterOS generations outside the currently supported RouterOS 7 onboarding path where operationally required.

## B2. Agent self-update and recovery

- [ ] Controlled agent source update for an already enrolled Device.
- [ ] Preserve the previous known-working source until the new agent is verified.
- [ ] Post-install heartbeat verification before declaring update success.
- [ ] Automatic rollback/recovery path when a newly installed agent does not parse/start.
- [ ] Agent/source checksum comparison to identify drift.
- [ ] Fleet view for outdated agent generations.
- [ ] Batch upgrade with concurrency limits.

## B3. Legacy capability expansion

Where RouterOS syntax permits it safely:

- [ ] Evaluate historical telemetry support for legacy agent families.
- [ ] Evaluate configuration snapshot support section-by-section.
- [ ] Evaluate backup capability without assuming modern JSON/file primitives.
- [ ] Evaluate support snapshot capability.
- [ ] Keep unsupported functions explicitly disabled if safe implementation is not possible.

The objective is graceful degradation, not forcing modern syntax onto old firmware.

## B4. Device workspace completion

Bring the MikroTik Device workspace to the full operational experience observed in the supplied OptiWize material.

Remaining work/improvements:

- [ ] Ensure consistent Device identity fields in every tab/sidebar, including MAC and management information.
- [ ] Normalize all byte values into human-readable units.
- [ ] Consistent percentage/unit formatting for CPU/memory/traffic values.
- [ ] Better stale/no-data messaging per metric/snapshot section.
- [ ] Persist and show snapshot collection timestamp per section.
- [ ] Show source/transport/capability reason where a section is unavailable.
- [ ] Add automatic configuration-change indication between snapshots/exports where appropriate.

## B5. Remote diagnostics completion

Already-supported jobs should receive richer presentation, and the remaining OptiWize-style diagnostic tools should be evaluated/implemented as safe allow-listed operations.

- [ ] Dedicated result modal/detail view.
- [ ] Ping formatted summary plus packet/latency details.
- [ ] Traceroute table one row per hop.
- [ ] Neighbor table with identity, IP, MAC, interface, board/model and version where exposed.
- [ ] Device log table with timestamp/topics/severity/message.
- [ ] DHCP lookup formatted results.
- [ ] Bandwidth diagnostic/test workflow with strict safety limits and explicit operator confirmation.
- [ ] Torch/traffic inspection workflow with bounded duration, interface selection and result limits.
- [ ] Source-address/interface options where RouterOS supports them.
- [ ] Diagnostic job timeout/cancel support.
- [ ] Audit/export of diagnostic results where useful.

## B6. Configuration history

- [ ] Automatic periodic configuration snapshot policy, distinct from binary backup policy.
- [ ] Snapshot version history.
- [ ] Automatic diff generation for material configuration changes.
- [ ] Change summary by subsystem: interfaces/IP/routes/firewall/DHCP/PPP/system.
- [ ] Link detected configuration change to Audit/Event history.
- [ ] Optional Action Center rule for unexpected/out-of-window changes.
- [ ] Baseline/approved configuration marker.
- [ ] Evidence export for configuration history.

## B7. Backup/restore production completion

The backup transport and `.rsc` diff are implemented; remaining production functions are:

- [ ] Restore-test workflow with evidence and timestamp.
- [ ] Mark last known successfully restore-tested artifact.
- [ ] Controlled restore planning/checklist without exposing arbitrary script execution.
- [ ] Device/model/RouterOS compatibility checks before offering a binary restore path.
- [ ] Backup storage capacity/health alerts.
- [ ] Missing-backup SLA/policy evaluation.
- [ ] Clear fleet coverage metrics based on actual executable method + recent successful artifact.
- [ ] Maintenance-window handling for large/fleet backup operations.

## B8. Firmware intelligence on top of the existing execution workflow

The RouterOS readiness/stage/activate mechanism exists. Missing is the authoritative decision/intelligence layer:

- [ ] Vendor firmware catalog ingestion.
- [ ] Stable/long-term/release-candidate channel awareness as configured by policy.
- [ ] Changelog/advisory evidence attached to target firmware.
- [ ] Classification: optional, bugfix, security, critical security, outdated.
- [ ] Model/architecture/package compatibility checks.
- [ ] Approved/recommended target policy.
- [ ] Fleet rollout planning and maintenance windows.
- [ ] Concurrency/batch limits.
- [ ] Pre-upgrade backup gate based on executable/recent backup status.
- [ ] Post-upgrade RouterOS health verification.
- [ ] Post-upgrade RouterBOOT decision workflow.
- [ ] Failed/stalled upgrade Action Center handling and recovery procedure.
- [ ] Firmware history/evidence report per Device.

---

# C. Ubiquiti / UISP completion

The read-only UISP connector and manual association/refresh path are implemented. The remaining goal is a complete operational Ubiquiti adapter rather than inventory association only.

## C1. Automatic UISP synchronization

- [ ] Background periodic sync worker.
- [ ] Incremental/batched synchronization suitable for large UISP inventories.
- [ ] Sync all associated NSM devices without manual refresh.
- [ ] Detect deleted/missing UISP devices without deleting NSM history.
- [ ] Detect external-ID/MAC conflicts and create actionable warnings.
- [ ] Connector last-sync status and statistics.
- [ ] Backoff/retry on API errors.
- [ ] Action Center issue on sustained connector failure.
- [ ] Recovery event when sync resumes.

## C2. Ubiquiti bulk onboarding

- [ ] Search/preview multiple UISP candidates.
- [ ] Bulk MAC association/import.
- [ ] Customer/Site assignment workflow during import.
- [ ] Duplicate/conflict preview before commit.
- [ ] Preserve UISP organization/site metadata as non-authoritative connector metadata.
- [ ] Audit bulk association results.

## C3. Ubiquiti monitoring

Normalize the UISP data exposed for AirMax AC/LTU and other supported Ubiquiti devices:

- [ ] CPU/memory where exposed.
- [ ] uptime.
- [ ] temperature where exposed.
- [ ] interface state and traffic.
- [ ] wireless signal/RSSI.
- [ ] noise floor.
- [ ] CCQ/quality/capacity metrics where exposed by UISP/device family.
- [ ] TX/RX rate/throughput where exposed.
- [ ] link peer metadata where available.
- [ ] historical persistence and retention.
- [ ] global Operations → Monitoring integration.

Do not invent metrics not supplied by the UISP/device API; capability flags must reflect the actual platform/device family.

## C4. Ubiquiti configuration backup/snapshot

- [ ] Determine supported backup/configuration mechanisms for each Ubiquiti family managed through UISP.
- [ ] Implement only methods exposed safely by UISP/vendor APIs.
- [ ] Capability-aware manual backup.
- [ ] Scheduled backup/policy integration.
- [ ] Artifact hashing/evidence.
- [ ] Text configuration viewer/diff where a textual configuration is actually available.
- [ ] Restore-test/evidence only for supported models/methods.
- [ ] Truthful `unsupported` state where UISP/device family does not expose backup.

## C5. Ubiquiti diagnostics

Where UISP/vendor APIs expose safe operations:

- [ ] ping diagnostic;
- [ ] traceroute diagnostic;
- [ ] neighbor/link information;
- [ ] radio/wireless diagnostic snapshot;
- [ ] bounded traffic/test tools if the API safely supports them;
- [ ] formatted result UI consistent with MikroTik diagnostics;
- [ ] full audit trail.

## C6. Ubiquiti firmware management

- [ ] Read current firmware and available/recommended firmware from authoritative Ubiquiti sources.
- [ ] Firmware catalog/channel model for AirMax AC/LTU and other supported families.
- [ ] Security/bugfix classification.
- [ ] Model compatibility.
- [ ] Firmware worklist integration.
- [ ] Controlled upgrade action only where UISP/vendor API supports it.
- [ ] Pre-upgrade backup gate when an executable backup method exists.
- [ ] Post-upgrade health/version verification.
- [ ] Batch/concurrency controls.
- [ ] Action Center on failed/stalled update.

## C7. Ubiquiti security/lifecycle correlation

- [ ] Map UISP-observed model/firmware to vulnerability records.
- [ ] Ubiquiti advisory ingestion/correlation.
- [ ] EOL/EOS lifecycle source ingestion for supported product families.
- [ ] Action Center remediation link to firmware/lifecycle workflow.

---

# D. TP-Link and other TR-069 vendors

The current code contains only the TP-Link/TR-069 onboarding/capability placeholder. A real ACS integration remains to be implemented.

The architecture should remain vendor-neutral so the same ACS layer can manage TP-Link and other TR-069 CPE vendors.

## D1. GenieACS / ACS connector

- [ ] Connector administration page.
- [ ] ACS base URL and authentication/credential storage.
- [ ] TLS verification controls.
- [ ] Connection test.
- [ ] Northbound API client with timeouts, bounded responses and safe error handling.
- [ ] Connector health, last sync and last error.
- [ ] Rate limiting/backoff.
- [ ] Audit connector configuration/test operations.

NSM must consume the ACS API; it should not implement its own CWMP ACS.

## D2. TR-069 device discovery and association

- [ ] Lookup by MAC.
- [ ] lookup by serial.
- [ ] lookup by OUI.
- [ ] lookup by ProductClass.
- [ ] lookup by ACS Device ID.
- [ ] normalized preview before association.
- [ ] stable external ACS Device ID storage.
- [ ] ambiguity/conflict handling.
- [ ] bulk association/import.
- [ ] preserve customer-local Site semantics independently of ACS grouping.

## D3. TR-069 normalized inventory

- [ ] Manufacturer/vendor.
- [ ] OUI.
- [ ] ProductClass/model.
- [ ] serial.
- [ ] MAC(s).
- [ ] software/firmware version.
- [ ] hardware version where exposed.
- [ ] WAN/management IP where appropriate.
- [ ] last Inform/last seen.
- [ ] online/reachability state derived from ACS freshness rules.
- [ ] connection/request URL metadata only where safe and appropriate.
- [ ] parameter-source timestamp/freshness.

## D4. Vendor parameter profiles

TR-069 parameter paths vary by vendor/model/data model. Add profile-driven normalization rather than hard-coding TP-Link paths globally.

- [ ] TR-098 profile support where required.
- [ ] TR-181 profile support where required.
- [ ] TP-Link parameter mapping profiles.
- [ ] profiles for additional CPE vendors as they are onboarded.
- [ ] capability detection from available parameter tree.
- [ ] model/firmware-specific overrides.
- [ ] safe unknown-model behavior.

## D5. TR-069 monitoring

Where the CPE exposes the parameters:

- [ ] uptime.
- [ ] CPU/memory.
- [ ] WAN state.
- [ ] interface counters.
- [ ] optical/DSL/radio metrics according to device class.
- [ ] Wi-Fi radio/client metrics where exposed.
- [ ] temperature where exposed.
- [ ] periodic sampling through ACS without excessive CPE load.
- [ ] history/retention.
- [ ] Operations → Monitoring integration.

## D6. TR-069 diagnostics

Use only standard/vendor diagnostics exposed by the ACS/CPE:

- [ ] IP ping diagnostics where exposed.
- [ ] traceroute diagnostics where exposed.
- [ ] download/upload diagnostics where exposed and safe.
- [ ] DSL/optical diagnostics according to device class.
- [ ] bounded timeout and operator confirmation for intrusive tests.
- [ ] normalized result presentation.
- [ ] audit trail.

## D7. TR-069 backup and restore

Backup must remain capability-driven.

- [ ] Discover whether a Device exposes a supported configuration download/export mechanism.
- [ ] Implement ACS-mediated backup only for validated vendor/model profiles.
- [ ] Store artifact/hash/evidence using the common backup model.
- [ ] Integrate with global/customer backup policies.
- [ ] Manual instant backup only when the method is executable.
- [ ] Scheduled backup only when safe.
- [ ] Restore workflow only when the CPE/ACS exposes a validated configuration restore mechanism.
- [ ] Restore-test evidence where possible.
- [ ] Explicit `capability not exposed` state for unsupported CPEs.

## D8. TR-069 firmware workflow

Where the ACS and CPE support firmware download/activation:

- [ ] Firmware catalog/model compatibility.
- [ ] ACS task generation using a validated vendor profile.
- [ ] explicit operator approval.
- [ ] maintenance-window scheduling.
- [ ] pre-update backup gate if executable.
- [ ] transfer/progress/task status.
- [ ] reboot/reconnect handling.
- [ ] post-update firmware verification from a later Inform.
- [ ] failure/recovery issue handling.

## D9. Additional TR-069 vendors

- [ ] Make onboarding vendor list extensible without new hard-coded workflow branches for every manufacturer.
- [ ] Add vendor aliases/OUI mapping.
- [ ] Add per-vendor parameter/capability profile packages.
- [ ] Keep common ACS transport separate from vendor-specific normalization.
- [ ] Build fixtures/tests from real ACS parameter trees.

## D10. TR-369 / USP future adapter

After the TR-069/ACS abstraction is mature:

- [ ] Define a parallel USP connector capability interface.
- [ ] Reuse normalized inventory/monitoring/firmware/security models.
- [ ] Avoid coupling the core domain model specifically to CWMP/TR-069.

---

# E. Security and vulnerability management

The database/views exist, but automated security intelligence is not yet the complete operational pipeline.

## E1. Security advisory ingestion

- [ ] Scheduled source-ingestion worker.
- [ ] Vendor advisory feeds/pages as primary evidence where available.
- [ ] NVD ingestion.
- [ ] CISA source ingestion where relevant.
- [ ] CERT/other authoritative source support where appropriate.
- [ ] Deduplication/merge of the same CVE from multiple sources.
- [ ] Store source URL, publication/update timestamps and evidence metadata.
- [ ] Retry/backoff and feed-health monitoring.
- [ ] Audit ingestion runs/errors.

## E2. Device-to-CVE matching engine

- [ ] Normalize vendor/product/model identity.
- [ ] Normalize installed firmware/software version.
- [ ] Version-range matcher.
- [ ] Fixed-version matcher.
- [ ] Architecture/package qualifiers where relevant.
- [ ] Vendor-specific matching rules when generic CPE data is insufficient.
- [ ] Confidence/evidence field for ambiguous matches.
- [ ] Prevent false positive correlation when evidence is insufficient.
- [ ] Re-evaluate CVEs automatically after inventory/firmware changes.
- [ ] Resolve impacts only when remediation evidence supports resolution.

## E3. CVE remediation workflow

- [ ] Full states: OPEN, PLANNED, IN_PROGRESS, RESOLVED, EXCEPTION and NOT_APPLICABLE where used.
- [ ] technician/assignee.
- [ ] target/remediation date.
- [ ] remediation notes/evidence.
- [ ] link to firmware plan when firmware is the fix.
- [ ] exception justification and expiry/review date.
- [ ] retain resolved history permanently.
- [ ] automatic Action Center creation/update for severe actionable impacts.
- [ ] automatic recovery/closure when verified fixed.

## E4. Security dashboard/worklist completion

- [ ] Reliable global CVE counts generated from the matching engine.
- [ ] customer counts without duplicating Devices for multiple CVEs.
- [ ] filters by customer/vendor/device/severity/status/age/source.
- [ ] fixed-version and remediation columns.
- [ ] evidence/advisory links.
- [ ] export/report integration.
- [ ] security KPI drill-downs from dashboard/customer/device views.

---

# F. EOL / EOS lifecycle intelligence

Views/fields exist; authoritative lifecycle ingestion and remediation remain.

- [ ] Vendor lifecycle source adapters.
- [ ] model/product normalization.
- [ ] separate EOL and EOS/support-until dates.
- [ ] security-update availability flag.
- [ ] evidence/source URL.
- [ ] scheduled refresh.
- [ ] manual evidence override with audit trail for vendors without machine-readable sources.
- [ ] customer/vendor/device/status/date filters.
- [ ] upcoming lifecycle threshold alerts.
- [ ] Action Center issues for actionable EOL/EOS conditions.
- [ ] recommendation/replacement notes.
- [ ] resolved/replaced Device evidence history.

---

# G. Action Center completion

The issue foundation exists; complete it as the central operational remediation queue.

- [ ] Aggregate all implemented domains: monitoring, backup, firmware, CVE, lifecycle, connector, agent and certificate issues.
- [ ] Deduplicate/suppress repeated equivalent issues.
- [ ] Preserve one Device with multiple underlying issue details rather than visually duplicating it unnecessarily.
- [ ] technician/assignee support.
- [ ] assignment filters.
- [ ] age/SLA filters.
- [ ] due/remediation date.
- [ ] issue detail timeline.
- [ ] remediation links directly to the relevant Device/workflow.
- [ ] explicit resolve/reopen flow where auto-resolution is not appropriate.
- [ ] automatic recovery closure for recoverable technical faults.
- [ ] bulk acknowledge/assignment for fleet operations.
- [ ] customer-specific Action Center view.

---

# H. Notifications completion

The in-app notification surface exists. Remaining work:

- [ ] Notification rule engine by category/severity/customer/vendor.
- [ ] User notification preferences.
- [ ] Deduplication/throttling.
- [ ] recovery notifications for every supported alert class, not only selected workflows.
- [ ] Email channel.
- [ ] Telegram channel.
- [ ] generic webhook channel.
- [ ] optional Slack/Teams adapters if required.
- [ ] delivery status/retry history.
- [ ] failed-channel Action Center/system warning.
- [ ] clear distinction between informational Audit-only events and actionable notifications.

---

# I. Firmware management — cross-vendor control plane

MikroTik execution foundations exist; the global Operations → Firmware product still requires an authoritative multi-vendor intelligence/control layer.

- [ ] Unified firmware catalog schema.
- [ ] Vendor/model/architecture/channel applicability.
- [ ] Installed vs recommended/approved target separation.
- [ ] Security/bugfix/optional/critical classification.
- [ ] Source/advisory evidence.
- [ ] Per-vendor policy for accepted channels.
- [ ] Customer/Site/vendor/device target policy.
- [ ] Fleet worklist with search/filter/pagination.
- [ ] Batch upgrade planning.
- [ ] maintenance windows.
- [ ] concurrency limits.
- [ ] mandatory/preferred backup gate rules.
- [ ] approval workflow for disruptive actions.
- [ ] post-upgrade verification.
- [ ] retry/recovery state.
- [ ] firmware history/evidence.
- [ ] connectors for Ubiquiti and TR-069-capable CPE where execution is supported.

---

# J. Backup — cross-vendor completion

- [ ] Complete executable coverage calculation for every vendor/device.
- [ ] Global customer summary driven by actual executable capability + recent run state.
- [ ] Ubiquiti backup connector methods where vendor APIs expose them.
- [ ] TR-069/ACS backup methods where validated CPE profiles expose them.
- [ ] Generic/collector backup method only where explicitly defined and safe.
- [ ] Cross-vendor manual `Backup now` with the capability registry choosing the method.
- [ ] Policy violation for enabled policy with no executable method.
- [ ] missing/stale backup issue generation.
- [ ] storage capacity/retention health.
- [ ] restore-test evidence workflow.
- [ ] archived restore-test result and hash evidence.
- [ ] backup/restore reporting.

---

# K. Audit, evidence and reports

Audit Events are now filterable/paginated/exportable. Reports remain a major missing product area.

## K1. Audit/evidence extensions

- [ ] Evidence attachment model for operator-uploaded files where required.
- [ ] Link audit records to backup artifacts, firmware plans, CVEs, lifecycle evidence and job output consistently.
- [ ] Evidence retention policy.
- [ ] tamper-evident/reportable hash chain or equivalent integrity strategy if adopted by the product design.
- [ ] scoped audit export by Customer/Device/date/category.

## K2. Reports

- [ ] Audit → Reports operational page.
- [ ] Report templates.
- [ ] All Customers/Devices scope.
- [ ] selected Customers/Devices scope.
- [ ] single Customer scope.
- [ ] single Device scope.
- [ ] date-range selection.
- [ ] PDF generation as the minimum export.
- [ ] CSV export for tabular evidence.
- [ ] later JSON/API export where useful.
- [ ] archive generated reports.
- [ ] report SHA-256/hash evidence.
- [ ] report-generation audit event.
- [ ] user/creator/timestamp metadata.
- [ ] NIS2-oriented evidence packs combining inventory, firmware, CVE, lifecycle, backup, change and action history.

---

# L. RBAC and multi-user administration completion

Existing permissions form a foundation. Remaining work:

- [ ] Administrative role editor using the granular permission model.
- [ ] clean built-in Administrator/Technician/Operator/Auditor defaults.
- [ ] Customer-scoped access where needed for delegated operators/auditors.
- [ ] Site/device scope where required without weakening customer isolation.
- [ ] API key scope administration UI.
- [ ] audit of role/permission changes.
- [ ] disable historical users rather than breaking audit references.
- [ ] session/security administration appropriate for production use.

---

# M. Integrations and public API completion

- [ ] Documented stable API contract for inventory/operations/evidence use cases.
- [ ] Pagination/filter conventions across APIs.
- [ ] explicit API versioning/deprecation policy.
- [ ] Webhook/event subscription mechanism if required.
- [ ] integration health page covering UISP, ACS and future adapters.
- [ ] credential rotation workflow for connectors/API keys.
- [ ] audit connector/API credential changes without exposing secrets.

---

# N. Production hardening and service operations

Required before treating NSM as a fully production-ready management platform:

- [ ] HTTPS/FQDN production deployment guidance and enforcement.
- [ ] secure-cookie/session production defaults.
- [ ] certificate-expiry monitoring and Action Center integration.
- [ ] documented DB backup using `pg_dump`.
- [ ] documented restore procedure and periodic restore test.
- [ ] worker/scheduler health monitoring.
- [ ] Redis/PostgreSQL/application dependency health checks.
- [ ] service failure and recovery notifications.
- [ ] operational log viewer/filtering for critical NSM services where this remains an agreed platform requirement.
- [ ] log retention/rotation guidance.
- [ ] storage utilization monitoring for backup/evidence/report volumes.
- [ ] safe migration rollback/recovery procedure.
- [ ] disaster-recovery runbook.
- [ ] optional off-site/secondary backup strategy for NSM's own evidence repository.

---

# O. Scalability and UX consistency

- [ ] Enforce server-side pagination on every potentially unbounded fleet/history table.
- [ ] Standardize search/filter/date-range controls.
- [ ] Standardize empty/loading/error/unsupported states.
- [ ] Standardize result modals/detail pages for long job output.
- [ ] Standardize human-readable byte/bit/time/percentage units.
- [ ] Standardize customer/vendor/site/device chips and links.
- [ ] Maintain responsive mobile/tablet behavior for every new worklist.
- [ ] Verify large-inventory query plans and indexes.
- [ ] Avoid N+1 queries in fleet worklists/connectors.
- [ ] Background jobs for slow sync/report/feed operations rather than blocking HTTP requests.

---

# P. Testing and acceptance matrix

## P1. Real-device matrix

Maintain a living matrix covering at least:

- [ ] representative MikroTik RouterOS firmware generations/architectures;
- [ ] representative Ubiquiti AirMax AC devices through UISP;
- [ ] representative Ubiquiti LTU devices through UISP;
- [ ] representative TP-Link TR-069 CPE through the selected ACS;
- [ ] at least one additional non-TP-Link TR-069 vendor to validate vendor-neutral ACS architecture.

For every capability declared supported, test:

- [ ] onboarding/association;
- [ ] identity/inventory;
- [ ] heartbeat/sync;
- [ ] monitoring;
- [ ] snapshot;
- [ ] diagnostics;
- [ ] backup;
- [ ] restore/test where supported;
- [ ] firmware workflow where supported;
- [ ] CVE/lifecycle correlation;
- [ ] audit evidence;
- [ ] recovery after failure/reboot/disconnection.

## P2. Generated-source/connector regression tests

- [ ] Validate final composed MikroTik source after every injected handler.
- [ ] Use sanitized real-device fixtures where emulation cannot reproduce RouterOS parsing behavior.
- [ ] Keep real tokens/secrets out of fixtures.
- [ ] UISP response fixtures for multiple device families/API shapes.
- [ ] GenieACS/TR-069 parameter-tree fixtures by vendor/model.
- [ ] Security-feed fixtures including difficult version ranges.
- [ ] EOL/EOS fixture coverage.

---

# Q. Recommended dependency order

This is not a release plan; it is a dependency-aware implementation order.

### Foundation / unblockers

1. Unified capability registry.
2. MikroTik compatibility resolver + agent family migration.
3. Real-device regression matrix.
4. Cross-vendor connector health framework.

### Complete existing vendor paths

5. MikroTik remaining diagnostics/configuration-history/restore-test work.
6. UISP periodic sync + monitoring.
7. Ubiquiti backup/firmware capabilities where exposed.
8. GenieACS connector + TR-069 inventory association.
9. TR-069 vendor profiles + monitoring/diagnostics.

### Intelligence layer

10. Firmware catalog/intelligence.
11. CVE ingestion and matching.
12. EOL/EOS ingestion.
13. Action Center synthesis and remediation lifecycle.
14. Notification rules/external channels.

### Evidence / production completion

15. Cross-vendor backup/restore evidence.
16. Reports and NIS2 evidence packs.
17. RBAC maturation.
18. Production/service hardening and restore/DR validation.

---

# R. Definition of done for a roadmap item

A feature should move from this file to `IMPLEMENTED_CAPABILITIES.md` only when all applicable points are satisfied:

- backend/data model implemented;
- connector/agent/device execution path implemented where required;
- server-side authorization and capability checks implemented;
- usable UI implemented;
- failure/unsupported state implemented;
- audit event/evidence implemented for material operations;
- Action Center/notification integration implemented when the event is actionable;
- automated smoke/integration coverage added;
- real-device validation performed when device syntax/API behavior cannot be proven by unit tests;
- secrets are not exposed in UI/logs/fixtures;
- documentation updated in the same development cycle.

---

# S. Explicitly out of scope / not to regress into

- Arbitrary remote RouterOS shell execution from NSM.
- A single shared secret for all Devices.
- FTP-based MikroTik backup as the product backup architecture.
- Building a proprietary TR-069 ACS inside NSM when a mature ACS connector can be used.
- Treating UISP Site/Organization as the authoritative NSM Customer/Site hierarchy.
- Calling a Device `protected`, `supported` or `updatable` when the required connector/capability is not executable.
- Claiming NIS2 compliance solely because NSM is installed.

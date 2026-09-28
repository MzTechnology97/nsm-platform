# NSM Platform — Implemented Capabilities

Status: living implementation reference

This document records **only functionality that is implemented in the repository**. It is intentionally separate from the product roadmap and must not be used to describe planned or aspirational behavior.

It is version-independent: the document follows the product as code evolves rather than describing a specific release number.

Last reviewed against the default branch: 2026-09-28.

## Maintenance rule

When a feature is merged:

1. update this document in the same development cycle if the feature becomes operational;
2. describe only the behavior actually implemented;
3. move/remove the corresponding item from `NEXT_IMPLEMENTATIONS.md`;
4. do not mark a feature implemented merely because a menu entry, model, placeholder, or future capability flag exists;
5. where support depends on a vendor, RouterOS generation, connector, or device capability, state that limitation explicitly.

Open PRs and design documents are not considered implemented until their behavior is merged into the default branch.

---

## 1. Platform and data model

Implemented:

- Customer, customer-local Site and Device hierarchy.
- A Device may belong to a Customer with or without a Site.
- Cross-customer Site assignment is rejected server-side.
- Device identity separates operator alias/display name from observed identity.
- Device inventory fields include vendor, model, serial, MAC, architecture, management IP, observed firmware, RouterBOOT where applicable, source and verification timestamps.
- Customer-centric workspaces with device/site/security/backup/history views.
- Global Device inventory.
- Customer and Device CRUD foundations.
- Device CSV import.
- Customer/device KPI foundations used by dashboard and workspaces.
- Demo data/workflow support for isolated UI and integration testing.

## 2. Search and navigation

Implemented:

- Collapsible application navigation.
- Global search foundations covering customers and devices.
- Device search across operator alias, observed identity, serial, MAC, IP and model.
- MAC normalization for inventory/search workflows.
- Customer-local Site organization rather than a global authoritative Site hierarchy.
- Responsive UI foundations, theme preference and configurable platform branding.

## 3. Users, permissions and administration

Implemented:

- Authenticated user sessions.
- User administration foundations.
- Role/permission checks on operational routes.
- Granular permission names for device, monitoring, backup, firmware, security, audit and administration workflows.
- User profile/security workflow.
- Password-change workflow with audit event generation.
- Platform branding configuration.
- API key foundations and scoped API operations.

This section describes the existing authorization foundation; it does not imply that every future workflow already has a complete delegated-role administration experience.

## 4. Audit and operational history

Implemented:

- Append-oriented audit event model used by material platform operations.
- Device enrollment/inventory-related audit events.
- Backup-related events.
- Firmware/job/agent operational events where implemented by the corresponding workflow.
- Connector configuration/test/association events for UISP.
- Per-device Audit worklist.
- Per-device Job & activity worklist.
- Separation of active job queue from completed/history records.
- Server-side filtering for device activity and audit history.
- Search and date filtering in the scalable activity/audit worklists.
- Pagination with selectable page sizes for device jobs/activity.
- Global Audit Events server-side filtering and pagination.
- Global Audit Events filtered CSV export.
- Audit event emitted when the global audit register is exported.

## 5. Action Center and in-app notifications

Implemented foundations:

- Action Center issue persistence.
- Severity/category/status presentation.
- Issue acknowledgement workflow with user/timestamp tracking.
- Issue-related audit events.
- In-app notification persistence.
- Top-bar notification bell/unread count.
- Recent-notification dropdown.
- Notification list and per-user read state.
- Automated agent-health/install-recovery notifications and Action Center integration for supported MikroTik cases.
- Backup/operational components can create or resolve issues where their current workflow implements that behavior.

External notification channels are not claimed here.

## 6. Backup control plane

Implemented:

- Global Backup Center foundations.
- Customer backup workspace.
- Backup policy model and policy inheritance foundations.
- Global/vendor/customer/site/device scope handling.
- Capability-aware backup method registry/guarding.
- Server-side rejection/normalization of incompatible backup options.
- Distinction between a configured policy and an executable device backup capability.
- Manual `Backup ora` action for supported devices.
- Scheduled backup worker foundations.
- Retry handling.
- Stale job/agent handling foundations.
- Retention policy foundations.
- Pre-firmware backup controls in the MikroTik firmware workflow.
- Backup run and artifact history.
- Authenticated artifact access.
- SHA-256 artifact evidence where generated by the backup pipeline.

### 6.1 MikroTik backup transport

Implemented for the supported MikroTik agent path:

- RouterOS binary `.backup` generation.
- AES-SHA256 encrypted RouterOS backup.
- Unique per-job backup password.
- Encrypted-at-rest handling of the backup password by NSM.
- Optional RouterOS `.rsc` text export.
- Outbound agent upload without requiring FTP/SFTP on the router.
- Chunked RouterOS file reading and Base64 transport.
- Upload offset validation and artifact size limits.
- Server-side finalization/storage and SHA-256 verification.
- Temporary RouterOS backup file cleanup by the agent workflow.

The old OptiWize FTP backup example supplied during product discovery is intentionally **not** part of the NSM implementation.

### 6.2 MikroTik text export inspection and diff

Implemented:

- Safe web viewer for stored MikroTik `.rsc` export artifacts.
- Size-bounded text loading.
- Comparison only between exports belonging to the same Device.
- Line-by-line added/removed/context diff.
- Diff summary.
- Audit events for viewing an export and for opening a diff.

## 7. MikroTik onboarding and agent

Implemented:

- One-time enrollment token bound to the intended Device.
- Outbound RouterOS bootstrap download/import workflow.
- RouterOS 7 preflight.
- Device-mode checks for fetch/scheduler/flagged state where RouterOS exposes them.
- Per-device credential generation instead of a global shared RouterOS secret.
- Permanent device ID + secret authentication for the agent.
- Agent heartbeat/polling model.
- Allow-listed remote jobs rather than arbitrary remote shell execution.
- Agent reinstall/re-enrollment foundations without deleting the Device record.
- Legacy RouterOS transport for versions that cannot use the modern JSON scripting path.
- Modern RouterOS agent source generation.
- Final modern-agent syntax hardening for invalid empty local initializers.
- Dynamic privilege profile so higher-risk RouterOS permissions are only added when required by allow-listed firmware/backup operations.

### 7.1 Agent installation state and fleet

Implemented:

- Per-device installation verification state machine.
- Distinction between token pending, token expired, pairing completed, active credential, first heartbeat, verified/stale and suspected parser/install failure.
- Grace period after pairing before declaring an install/agent-start problem.
- Agent tab with installation pipeline/status diagnostics.
- Agent Fleet operations view.
- Fleet KPI cards.
- Server-side fleet filtering by installation/health/customer/transport state.
- Critical Action Center issue when pairing completes but no post-install heartbeat arrives after the grace period.
- Automatic recovery/issue resolution when the first valid heartbeat is later observed.
- No display of raw agent secrets or secret hashes in the operational UI.

## 8. MikroTik inventory and Device workspace

Implemented:

- Dedicated MikroTik Device workspace.
- Observed RouterOS identity.
- Board/model.
- Serial number where exposed.
- Primary MAC discovery.
- Architecture.
- Software ID where exposed.
- RouterOS version.
- RouterBOOT current firmware where exposed.
- Uptime.
- CPU information/count/load.
- Total/free memory.
- Management source and agent metadata.
- Current heartbeat/last-seen state.
- Human-readable memory parsing/normalization in the telemetry path.

## 9. MikroTik telemetry and monitoring

Implemented on the modern agent path:

- Periodic CPU load samples.
- Free/total memory samples.
- Derived memory-used percentage.
- Uptime samples.
- Historical metric persistence.
- Device metrics API.
- Time ranges for 1 hour, 24 hours, 7 days and 30 days.
- Downsampling to a bounded number of points for the UI/API.
- Telemetry retention cleanup foundation with 90-day retention.
- Per-device Monitor workspace.

Legacy RouterOS transport remains capability-limited and is not described as providing the same historical telemetry contract.

## 10. MikroTik configuration snapshots

Implemented on the supported modern agent path:

- Resources/system snapshot.
- IP addresses.
- IP routes.
- Interfaces.
- Firewall filter/NAT snapshot.
- Active PPP sessions.
- DHCP leases.
- Warning/error/critical log snapshot.
- Support snapshot combining multiple operational sections.
- Job-based snapshot execution and result upload.

Legacy RouterOS support is capability-dependent and may intentionally expose fewer snapshot functions.

## 11. MikroTik remote diagnostics

Implemented allow-listed diagnostics include:

- ping;
- traceroute;
- neighbor discovery;
- DHCP lookup;
- warning/error log retrieval;
- support snapshot where the agent capability supports it.

Results are executed as authenticated predefined jobs rather than arbitrary command execution.

## 12. MikroTik firmware and RouterBOOT workflow

Implemented foundations/workflows include:

- firmware readiness job;
- current update channel/status collection;
- installed/latest RouterOS version collection from RouterOS update facilities;
- free storage check;
- RouterBOOT current/upgrade firmware collection;
- firmware upgrade planning workspace;
- package staging/download workflow;
- explicit firmware activation/reboot acknowledgement flow;
- post-reboot verification foundation;
- RouterBOOT staging workflow;
- RouterBOOT reboot/activation acknowledgement workflow;
- separate RouterOS and RouterBOOT lifecycle handling;
- privilege guard for firmware activation;
- firmware operational worklist.

This does not imply that vendor advisory/security intelligence or an authoritative multi-vendor firmware catalog is already implemented.

## 13. Ubiquiti / UISP connector

Implemented as a read-only UISP Network connector:

- Administrative UISP connector configuration.
- Encrypted storage of the UISP API token.
- Enable/disable and TLS verification settings.
- Connection test.
- Device retrieval through the UISP Network API.
- Response size/timeouts and connector error handling.
- Ubiquiti Device association by normalized MAC.
- Preview before association.
- Stable UISP external Device ID storage.
- Duplicate/ambiguous association protection.
- Manual refresh of an associated Device.
- Synchronization of currently exposed identity/model/serial/MAC/management IP/firmware/status/last-seen fields.
- UISP Site/role/category retained as connector metadata without redefining NSM customer-local Sites.
- Audit events for connector configuration/test/device association/inventory refresh.

The connector is currently an inventory/association source; this document does not claim Ubiquiti backup, firmware execution or full telemetry unless those functions are separately implemented.

## 14. TP-Link / TR-069 foundations

Implemented foundations only:

- TP-Link/CPE vendor type in onboarding and inventory.
- TR-069/ACS as the declared management source for this vendor class.
- CPE onboarding validation requiring at least MAC or serial where applicable.
- Capability-aware backup registry entry that truthfully reports ACS/TR-069 backup as non-executable while the ACS integration is unavailable.
- UI wording that distinguishes the planned ACS path from an executable capability.

No ACS northbound connector is claimed as implemented.

## 15. Security data and drill-down foundations

Implemented foundations:

- Security advisory persistence model.
- Device-to-advisory vulnerability impact model.
- Security/Vulnerabilities worklist UI.
- Search by CVE/vendor/product/summary.
- Vendor and severity filters.
- Open/resolved impact filtering.
- Customer filtering.
- Affected Device and affected Customer counts.
- Server-side pagination.
- Vulnerability detail/drill-down with impacted Devices and Customers.
- Security information surfaced in customer/device workspaces where current records exist.
- Lifecycle drill-down foundations and lifecycle fields used by the UI/data model.

The existence of these models/views does **not** mean automated CVE or EOL/EOS source ingestion is implemented.

## 16. Firmware status foundations

Implemented:

- Observed firmware field separated from other Device identity data.
- Firmware status fields/worklist foundations.
- Device/customer counters can use firmware attention states.
- MikroTik readiness/planning/staging/activation mechanisms described above.

An authoritative cross-vendor catalog with automatic advisory classification is not claimed here.

## 17. API and integration foundations

Implemented:

- API key support.
- Scoped operational API foundations.
- UISP connector integration.
- MikroTik agent APIs and job transport.
- Dedicated legacy and modern MikroTik transport paths.
- Connector/integration persistence used by UISP.

## 18. Deployment and CI foundations

Implemented in the repository/workflow:

- Alembic migration workflow.
- Docker Compose deployment foundations.
- Application health/smoke validation in CI.
- Integrated smoke tests for major platform workflows.
- Dedicated MikroTik tests for enrollment, legacy jobs, snapshots, diagnostics, firmware, RouterBOOT, backup, telemetry, agent installation/fleet state and source-syntax safety.
- Automatic deploy/promotion workflow foundations used by the project.
- Production configuration/secrets kept outside committed runtime data.

---

## Source-of-truth boundary

`IMPLEMENTED_CAPABILITIES.md` answers one question only:

> What can the code currently do?

For work still required to reach the complete target system derived from the supplied OptiWize reference material and agreed NSM architecture, see `NEXT_IMPLEMENTATIONS.md`.

# NSM Platform

Customer-edge multi-vendor platform for device inventory, monitoring, backup, configuration history, firmware, security, lifecycle, audit and compliance evidence.

NSM is primarily intended for **devices installed at customer premises or customer-serving edge locations**. It complements vendor/NMS/UISP/ACS tools and does not currently aim to replace an ISP backbone/core NMS.

> This README is a high-level dashboard. The detailed implementation source of truth is `docs/IMPLEMENTED_CAPABILITIES.md`. Future work should be split into focused, testable PRs rather than one large development stream.

## Status

### Core platform

- [x] Customer / customer-local Site / Device hierarchy
- [x] Inventory and Device identity model
- [x] Customer workspaces and global Device inventory
- [x] Search foundations
- [x] Authentication and permission enforcement foundations
- [x] Audit event foundation
- [x] Action Center foundation
- [x] In-app notifications / unread state
- [x] API key / operational API foundations
- [ ] Mature delegated RBAC and scoped administration
- [ ] Complete evidence/report archive
- [ ] Production DR/restore validation and operational hardening

### MikroTik

- [x] Secure one-time onboarding and per-device credentials
- [x] Modern and legacy RouterOS transport paths
- [x] Agent heartbeat and allow-listed job queue
- [x] Agent install verification / Agent Fleet recovery states
- [x] Inventory collection
- [x] Modern-agent CPU/memory/uptime telemetry history
- [x] Configuration/operational snapshots on supported modern agents
- [x] Ping / traceroute / neighbor / DHCP / log diagnostics
- [x] Encrypted `.backup` + optional `.rsc` backup transport
- [x] `.rsc` viewer and configuration diff
- [x] Configuration history, approved baseline and drift detection
- [x] Interface Health worklist from configuration snapshots
- [x] Firmware readiness / staging / activation foundations
- [x] RouterBOOT lifecycle foundations
- [ ] Validated RouterOS compatibility-family resolver and real-device matrix
- [ ] Agent self-update with verified rollback/recovery
- [ ] Restore-test evidence workflow
- [ ] Firmware catalog/advisory intelligence
- [ ] Richer diagnostic result UI / bounded advanced diagnostics

### Ubiquiti / UISP

- [x] Read-only UISP connector configuration
- [x] Encrypted API token storage
- [x] Connector test
- [x] MAC-based Device preview/association
- [x] Stable external UISP Device ID
- [x] Manual inventory refresh
- [x] Current identity/model/serial/MAC/IP/firmware/status/last-seen normalization
- [ ] Automatic periodic UISP synchronization
- [ ] Monitoring/history normalization
- [ ] Bulk onboarding/association
- [ ] Backup/snapshot capability where exposed by vendor APIs
- [ ] Diagnostics where exposed by vendor APIs
- [ ] Firmware workflow
- [ ] CVE and EOL/EOS correlation

### TP-Link and other TR-069 CPE

- [x] Vendor/onboarding foundations
- [x] TR-069/ACS management-source model
- [x] Capability state truthfully marks ACS-dependent actions unavailable until implemented
- [ ] GenieACS/ACS connector
- [ ] Device discovery/association
- [ ] TR-098 / TR-181 normalization profiles
- [ ] TP-Link and additional vendor parameter profiles
- [ ] Monitoring/history through ACS
- [ ] TR-069 diagnostics
- [ ] Capability-aware backup/restore
- [ ] Firmware deployment/verification through ACS where supported

### Security, lifecycle and compliance

- [x] Security advisory and Device-impact data models
- [x] Vulnerability worklist/drill-down foundations
- [x] Lifecycle UI/data foundations
- [ ] Automated advisory ingestion
- [ ] Device ↔ CVE version matching
- [ ] EOL/EOS source ingestion
- [ ] Compliance Baseline
- [ ] Incident Timeline / Root Cause
- [ ] Scheduled Executive / NIS2 reports

## Development rule

Implementation work follows **one feature → one branch → one PR → tests → merge → documentation update**.

A developer/agent must not silently abandon an active feature and start another. A blocked feature must record the blocker, last successful step and exact next action required.

## Definition of done

A feature is not complete merely because a route, model, menu entry or placeholder exists. Where applicable it needs:

- actual backend/agent/connector behavior;
- authorization and capability checks;
- usable UI and explicit unsupported/error states;
- audit/evidence;
- automated tests;
- real-device/vendor validation where simulation cannot prove behavior;
- documentation update.

## Architecture principles

- No arbitrary remote shell from NSM.
- Remote operations are allow-listed and auditable.
- No global shared Device secret.
- Planned capabilities are never displayed as executable.
- MikroTik backup does not use the legacy OptiWize FTP approach.
- TR-069 integrates with a mature ACS such as GenieACS rather than implementing CWMP/ACS inside NSM.
- UISP Site/Organization metadata does not replace NSM customer-local Sites.
- NSM can support NIS2-oriented evidence but does not claim that installing the platform alone creates compliance.

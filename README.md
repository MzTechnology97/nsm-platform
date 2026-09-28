# NSM Platform

Multi-vendor operational, security, backup, lifecycle and evidence platform focused primarily on **devices installed at customer premises and customer-serving edge locations**.

NSM is designed to normalize device state across vendors while keeping vendor-specific execution behind explicit capability adapters. It complements NMS, UISP, ACS/TR-069 platforms and vendor tools rather than pretending every device exposes the same management functions.

> **Documentation rule:** this README is a high-level project dashboard. Detailed current capabilities live in [`docs/IMPLEMENTED_CAPABILITIES.md`](docs/IMPLEMENTED_CAPABILITIES.md); remaining work lives in [`docs/NEXT_IMPLEMENTATIONS.md`](docs/NEXT_IMPLEMENTATIONS.md).

## Product scope

Current priority is **customer-side / customer-edge equipment management and tracking**, including inventory, health, configuration, backup, firmware, security, lifecycle, incidents and compliance evidence.

The platform is **not currently intended to become a full ISP backbone/core management suite**. Features such as BGP optimization, OSPF/MPLS/VPLS engineering, POP topology analysis, backbone path simulation and traffic engineering are out of scope unless a future product decision explicitly changes this priority.

See [`docs/PRODUCT_SCOPE_AND_PRIORITIES.md`](docs/PRODUCT_SCOPE_AND_PRIORITIES.md) for the durable scope decision and the planned Incident Timeline, Compliance Baseline and Scheduled Executive/NIS2 Reports capabilities.

## Project status

Legend:

- ✅ implemented in the repository;
- 🟡 foundation/partial implementation — not yet a complete production capability;
- ⬜ planned / remaining implementation.

### Core platform

- [x] Customer / customer-local Site / Device hierarchy
- [x] Inventory and Device identity model
- [x] Customer workspaces and global Device inventory
- [x] Global/device search foundations
- [x] Authentication and permission enforcement foundations
- [x] Audit event foundation
- [x] Action Center foundation
- [x] In-app notifications and unread state
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
- [x] Modern-agent historical CPU/memory/uptime telemetry
- [x] Configuration/operational snapshot handlers on supported modern agents
- [x] Ping / traceroute / neighbor / DHCP / log diagnostic jobs
- [x] Encrypted `.backup` + optional `.rsc` backup transport
- [x] `.rsc` viewer and configuration diff
- [x] Firmware readiness / staging / activation foundations
- [x] RouterBOOT lifecycle workflow foundations
- [ ] Validated RouterOS compatibility-family resolver and real-device matrix
- [ ] Agent self-update with verified rollback/recovery
- [ ] Expand legacy capabilities where RouterOS safely allows them
- [ ] Configuration-change history / baseline workflow
- [ ] Restore-test evidence workflow
- [ ] Firmware intelligence/catalog/advisory layer
- [ ] Rich diagnostic result UI and bounded advanced diagnostics

### Ubiquiti / UISP

- [x] Read-only UISP connector configuration
- [x] Encrypted API token storage
- [x] Connector test
- [x] MAC-based Device preview/association
- [x] Stable external UISP Device ID
- [x] Manual inventory refresh
- [x] Identity/model/serial/MAC/IP/firmware/status/last-seen normalization currently exposed by the connector
- [ ] Automatic periodic UISP synchronization
- [ ] Ubiquiti monitoring/history normalization
- [ ] Bulk onboarding/association
- [ ] Backup/snapshot capability where vendor APIs expose it
- [ ] Diagnostics where vendor APIs expose them
- [ ] Firmware workflow
- [ ] CVE and EOL/EOS correlation

### TP-Link and other TR-069 CPE

- [x] Vendor/onboarding foundations
- [x] TR-069/ACS management-source model
- [x] Capability registry truthfully marks ACS backup as unavailable until implemented
- [ ] GenieACS/ACS connector
- [ ] MAC/serial/OUI/ProductClass/ACS-ID discovery and association
- [ ] TR-098 / TR-181 normalization profiles
- [ ] TP-Link vendor parameter profiles
- [ ] Additional vendor profiles
- [ ] Monitoring/history through ACS
- [ ] TR-069 diagnostics
- [ ] Capability-aware backup/restore
- [ ] Firmware deployment/verification through ACS where supported
- [ ] Future TR-369/USP adapter

### Operations

- [x] Backup policy/control-plane foundations
- [x] Per-device and global activity/audit worklists
- [x] Filtering/pagination for scalable audit/job views
- [x] Global Audit CSV export
- [ ] Complete cross-vendor Operations → Monitoring
- [ ] Unified connector/agent health dashboard
- [ ] Mature job cancel/retry/idempotency/result-rendering framework
- [ ] Cross-vendor executable backup coverage and restore evidence
- [ ] Multi-vendor firmware control plane
- [ ] Incident Timeline / Root Cause for customer/device incidents

### Security and lifecycle

- [x] Security advisory and Device impact data models
- [x] Vulnerability worklist/drill-down foundations
- [x] Lifecycle UI/data foundations
- [ ] Automated vendor/NVD/CISA/CERT advisory ingestion
- [ ] Device ↔ CVE version matching engine
- [ ] Full remediation lifecycle and evidence
- [ ] EOL/EOS source ingestion
- [ ] Security/Lifecycle → Action Center automation
- [ ] Compliance Baseline with vendor/capability-aware findings and exceptions

### Reports and compliance evidence

- [x] Audit/evidence foundations
- [ ] Audit → Reports production implementation
- [ ] PDF report generation
- [ ] CSV evidence exports beyond the existing Audit export where applicable
- [ ] Archived report hashes and generation evidence
- [ ] NIS2-oriented evidence packs
- [ ] Scheduled Executive / NIS2 Reports with recurring generation and archived evidence

## Documentation

| Document | Purpose |
| --- | --- |
| [`docs/PRODUCT_SCOPE_AND_PRIORITIES.md`](docs/PRODUCT_SCOPE_AND_PRIORITIES.md) | Customer-edge scope guardrail and durable priority decisions |
| [`docs/IMPLEMENTED_CAPABILITIES.md`](docs/IMPLEMENTED_CAPABILITIES.md) | Only capabilities actually implemented in code |
| [`docs/NEXT_IMPLEMENTATIONS.md`](docs/NEXT_IMPLEMENTATIONS.md) | Version-independent backlog of remaining work |
| [`docs/PRODUCT_REQUIREMENTS.md`](docs/PRODUCT_REQUIREMENTS.md) | Durable product requirements and architecture decisions |
| [`docs/DEVELOPMENT_WORKFLOW.md`](docs/DEVELOPMENT_WORKFLOW.md) | Rules for continuous development, PR scope and handoff |
| [`docs/README.md`](docs/README.md) | Documentation maintenance rules |

## Development workflow

The project uses a strict **one objective → one branch → one PR → tests → merge → documentation update** flow.

A development agent must not silently abandon an active objective and start another one. If work cannot continue, the PR must be explicitly marked/documented as **BLOCKED** with:

- exact blocker;
- last successful step;
- failing command/test/log;
- files currently changed;
- next concrete action required to resume.

Before selecting a new roadmap item, development must also verify that the objective fits the current **customer-edge/customer-installed Device scope**. ISP backbone/core expansions require an explicit product decision.

See [`docs/DEVELOPMENT_WORKFLOW.md`](docs/DEVELOPMENT_WORKFLOW.md) for the full continuation protocol.

## Definition of done

A feature is not considered complete because a route, menu entry, model or placeholder exists. Where applicable it must have:

- backend/data model;
- actual agent/connector execution path;
- server-side authorization and capability checks;
- usable UI;
- explicit unsupported/error states;
- audit/evidence for material actions;
- automated tests;
- real-device validation when vendor syntax/API behavior cannot be adequately simulated;
- documentation moved from `NEXT_IMPLEMENTATIONS.md` to `IMPLEMENTED_CAPABILITIES.md`.

## Security / architecture principles

- No arbitrary remote shell from NSM.
- Remote operations are allow-listed and auditable.
- No global shared device secret.
- Planned capabilities must never be displayed as executable.
- MikroTik backup does not use the legacy FTP approach from the OptiWize reference material.
- TR-069 management integrates with a mature ACS such as GenieACS rather than implementing CWMP/ACS inside NSM.
- UISP Site/Organization metadata does not replace NSM customer-local Sites.
- NSM supports evidence useful in NIS2-oriented programs but does not claim that installing the platform alone creates compliance.

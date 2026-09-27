# NSM Platform — Product Requirements and Architecture Decisions

Status: living product specification

This document is the durable source of truth for the product direction agreed during development. The platform is intentionally generic and must not embed customer-specific company names, logos, credentials, network ranges, or business identifiers in source code.

## 1. Product purpose

NSM Platform is a multi-vendor operational, security, backup, lifecycle and evidence platform for ISP/WISP environments.

It complements, rather than replaces, specialist tools such as NMS/monitoring systems, vendor controllers, ACS platforms and configuration management systems. Its main value is to normalize operational state across customers and network vendors and turn that state into an auditable workflow.

Primary concerns:

- inventory and authoritative device identity;
- customer-scoped organization;
- lightweight operational monitoring;
- configuration backup and restore evidence;
- firmware lifecycle and update planning;
- CVE impact and remediation tracking;
- EOL/EOS lifecycle evidence;
- Action Center and notifications;
- audit trail and reports;
- role-based administration;
- integrations with vendor-native management systems.

## 2. Canonical hierarchy

The canonical hierarchy is:

```text
Customer
├── Site (optional, customer-local)
│   └── Device
└── Device without site (allowed)
```

### 2.1 Sites are customer-local

A Site is never a globally shared POP/site object.

Examples:

- `Customer A / Sede principale`
- `Customer B / Sede principale`

are two different objects even though they have the same display name.

Requirements:

- every Site has exactly one `customer_id`;
- a Site name does not have to be globally unique;
- a Device can only reference a Site owned by the same Customer;
- cross-customer Site assignment must be rejected server-side, not only hidden in the UI;
- Site creation, editing and removal belong inside the Customer workspace;
- there is no primary global `Sites` navigation item;
- `/sites` may exist only as a compatibility redirect, not as an authoritative global workspace;
- UISP Site/Organization data may be retained as connector metadata, but it must not redefine this hierarchy.

## 3. Customer-centric UX

The Customers area is the primary operational entry point.

### 3.1 Customer list

Use a compact one-row-per-customer layout suitable for hundreds or thousands of customers.

Each row should expose, without opening the Customer profile:

- customer name and optional internal code;
- number of Devices;
- number of customer-local Sites;
- number of Devices requiring firmware attention;
- number of distinct Devices affected by open High/Critical CVEs;
- number of open/acknowledged Action Center issues;
- optional backup-specific attention indicator.

Counters must not inflate device counts by counting several CVEs on the same Device as several affected Devices.

### 3.2 Customer profile

The Customer profile should behave as an operational profile/dashboard.

Top KPI widgets:

- Devices;
- Devices affected by serious CVEs;
- Devices requiring firmware update/review;
- open errors/attention items.

Sections/tabs:

- Overview;
- Devices;
- Sites;
- Backups;
- Security;
- History;
- Reports when available.

The profile should surface important issues before the user opens individual Device records.

## 4. Device identity model

Separate operator-friendly naming from authoritative observed identity.

Fields/concepts:

- `display_name`: editable portal alias;
- `device_identity`: authoritative observed hostname/system identity, normally read-only;
- `vendor`;
- model/board;
- serial;
- primary MAC;
- architecture;
- management IP;
- management source;
- observed firmware;
- desired/recommended firmware;
- last seen;
- inventory source and last verification timestamp.

UI title priority:

1. `display_name`;
2. `device_identity`;
3. product-specific placeholder.

Observed and desired values must never be conflated. In particular, installed firmware is observational; recommended/target firmware is separate.

Significant automatically discovered changes should generate append-only audit events.

## 5. Device onboarding

Onboarding should ask the operator for as little information as possible. Prefer authoritative discovery.

### 5.1 MikroTik

Canonical MikroTik enrollment:

1. operator selects Customer, optional Site and MikroTik vendor, plus optional alias;
2. Device enters `PENDING_ENROLLMENT`;
3. backend creates a Device UUID and short-lived one-time token (target approximately 30 minutes);
4. UI generates a RouterOS command to paste into Terminal;
5. RouterOS initiates outbound HTTPS/443 to NSM;
6. token maps directly to the intended Device/Customer;
7. RouterOS submits authoritative inventory;
8. token is invalidated after use;
9. server creates a unique permanent `device_id + device_secret` credential;
10. agent uses outbound heartbeat/polling for predefined jobs.

No global shared RouterOS secret.

The managed agent must not expose arbitrary remote shell execution. Supported jobs should be explicit, versioned operations such as:

- inventory refresh;
- heartbeat/health;
- backup;
- configuration export;
- firmware check/update workflow;
- other narrowly-defined future actions.

Agent reinstall/update for already-enrolled routers must not require deleting/recreating the Device.

### 5.2 Ubiquiti / UISP

UISP is a first-class connector, not a generic shell agent.

Target onboarding:

1. choose Customer and optional Site;
2. enter/scan MAC;
3. normalize MAC;
4. query configured UISP instance;
5. preview matched Device data;
6. associate to NSM record;
7. retain UISP internal Device ID as stable external reference.

Importing the full UISP organizational structure is not required and UISP Site/Organization is not authoritative for NSM customer-local Sites.

### 5.3 TP-Link / ISP CPE

Prefer a mature TR-069 ACS such as GenieACS instead of implementing an ACS inside NSM.

NSM integrates through the ACS northbound API.

Matching candidates can include:

- MAC;
- serial;
- OUI;
- ProductClass;
- ACS Device ID.

Backup/restore is only offered when the device data model and ACS actually expose a supported configuration mechanism.

TR-369/USP may be added later.

### 5.4 Optional site collector

A local collector may perform vendor APIs/SSH locally and communicate outbound HTTPS to central NSM. This is useful where direct central management is undesirable or impossible.

## 6. Backup architecture

Backup must be customer-centric and vendor-capability-aware.

### 6.1 Global vs customer views

Global Backup page:

- one row/summary per Customer;
- total Devices;
- executable/protected Devices;
- Devices missing an executable policy/method;
- open backup alerts;
- global/vendor/customer policy summary.

It must not dump every Device from every Customer into one unbounded table.

Customer Backup page:

- only Devices owned by that Customer;
- filter by customer-local Site;
- filter by latest backup status;
- effective policy;
- schedule;
- latest execution;
- artifacts and hashes;
- customer/site/device policy management.

### 6.2 Policy precedence

Effective policy specificity, highest first:

1. Device;
2. Site;
3. Customer;
4. Vendor;
5. Global.

A policy is not the same thing as executable backup coverage. Coverage requires both:

- an effective enabled policy; and
- a currently implemented/available backup method for that Device.

The UI must never call a Device `protected` merely because a policy exists when the connector/agent required to execute it is unavailable.

### 6.3 Capability-aware form

For an exact Device or Vendor target, only compatible backup capabilities are presented and accepted.

Examples:

- MikroTik: RouterOS NSM agent methods only;
- Ubiquiti: supported Ubiquiti connector method only;
- TP-Link/CPE: ACS/TR-069 method only if available;
- Generic/Legacy: explicit fallback/snapshot capabilities only.

For mixed Global/Customer/Site scopes, the operator should not need to understand protocol compatibility. The UI should explain that NSM chooses the supported method per Device.

Server-side normalization must reject or clear incompatible options even if a request is manually manipulated.

### 6.4 MikroTik real backup transport

Implemented design:

- `.backup` generated by RouterOS with AES-SHA256 encryption;
- `.rsc` text export when enabled;
- unique backup password generated per job;
- password encrypted at rest using the platform encryption master key;
- password delivered only to the authenticated Device while executing the job;
- RouterOS reads file chunks with `/file read`;
- recommended raw chunk size approximately 24 KiB and never above RouterOS limit;
- Base64 transported outbound over HTTPS;
- strict upload offsets;
- maximum artifact size enforcement;
- fsync / finalized upload;
- SHA256 server-side verification;
- atomic archive move;
- temporary RouterOS files removed after transfer;
- no FTP/SFTP service required;
- artifact download is authenticated through NSM.

No raw live PostgreSQL copy is used for platform DB backups; use `pg_dump`.

### 6.5 Backup scheduler and maintenance

Required/implemented foundation:

- readable schedules (daily, weekly, monthly, six-hour etc.);
- retry count;
- stale job detection;
- retention daily/weekly/monthly;
- pre-firmware backup option;
- SHA256 evidence;
- Action Center issue on meaningful failure;
- resolve/close backup issue after later success where appropriate;
- stale agent detection.

## 7. Monitoring

Monitoring is deliberately lightweight and does not replace Zabbix or a full NMS.

Desired normalized state:

- reachability/online state;
- last seen;
- uptime;
- CPU;
- memory;
- temperature where available;
- firmware;
- selected interfaces;
- wireless metrics such as signal/CCQ where meaningful.

Customer/profile counters should link to filtered detailed views.

## 8. Firmware management

Firmware states should distinguish at least:

- current/unknown;
- optional update;
- bugfix;
- security update;
- critical security update;
- outdated.

Keep separate:

- observed/installed version;
- recommended/target version;
- channel/source;
- evidence/vendor advisory.

Before an automated firmware action, use backup policy/pre-firmware controls where the Device has an executable backup capability.

## 9. Vulnerability management

Security → Vulnerabilities should support:

- CVE ID;
- severity;
- affected Devices count;
- affected Customers count;
- installed version;
- fixed version;
- source/advisory;
- remediation state;
- evidence.

Preferred sources:

- vendor advisory first;
- NVD;
- CISA;
- CERT and other authoritative sources.

Lifecycle states for Device impact:

- `OPEN`;
- `PLANNED`;
- `IN_PROGRESS`;
- `RESOLVED`;
- `EXCEPTION`;
- optionally `NOT_APPLICABLE`.

Resolved CVE history must be retained with detection, resolution and remediation evidence.

A new advisory with zero affected managed Devices may remain visible in Security without generating a critical Action Center issue.

## 10. Lifecycle / EOL / EOS

Track EOL and EOS separately.

Store where known:

- lifecycle status;
- EOL date;
- EOS/support-until date;
- whether security updates are still available;
- vendor/source URL or evidence;
- recommendation text.

Lifecycle problems should become Action Center issues where action is required.

## 11. Action Center

Action Center is the central attention queue, separate from audit history and notifications.

Examples:

- critical/high CVE;
- security firmware required;
- outdated firmware;
- EOL/EOS;
- backup failed/missing;
- Device offline where configured as actionable;
- stale agent;
- connector/credential problems;
- certificate expiry;
- other policy violations.

Filters should include severity, Customer, vendor, Device, category/reason, status, technician/assignee and age.

Avoid duplicate Device rows where one Device has several underlying issues; expose the issue count/details clearly.

Suggested states:

- open;
- acknowledged;
- resolved.

## 12. Notifications

Notifications are a UISP-like top-right user surface and are distinct from Action Center and Audit.

Examples:

- successful routine backup → audit only by default;
- failed backup → audit + notification + Action Center issue.

Suggested severities:

- INFO;
- WARNING;
- HIGH;
- CRITICAL.

Categories:

- Security;
- Backup;
- Firmware;
- Lifecycle;
- Monitoring;
- Integration;
- System;
- User/Admin.

Future channels can include Web, Email, Telegram and optionally webhook/Slack/Teams.

## 13. Audit and evidence

Audit must be append-only for material operations and observations.

Examples:

- Device enrollment;
- inventory discovery;
- identity/model/serial/MAC/IP changes;
- firmware changes;
- backup creation/failure/deletion/restore test;
- configuration changes/diffs;
- CVE state transitions;
- lifecycle events;
- credential rotations;
- report generation;
- user/admin operations.

Evidence may reference:

- backup artifacts;
- hashes;
- vendor advisory URLs;
- job logs;
- firmware before/after;
- uploaded evidence files.

## 14. Reports

Reports should support selection by:

- all Customers/Devices;
- selected set;
- single Customer;
- single Device;
- date range.

Minimum export: PDF.

Desirable: CSV.

Later: JSON/API export.

Generated reports should be archived with a report hash and generation audit event.

## 15. Users and RBAC

Historical users should normally be disabled (`is_active=false`) rather than hard-deleted when audit references exist.

Baseline roles:

- Administrator;
- Technician;
- Operator;
- Auditor.

Granular permissions should include at least:

- `customers.read` / `customers.write`;
- `devices.read` / `devices.write` / `devices.enroll`;
- `backup.read` / `backup.execute` / `backup.configure`;
- `firmware.read` / `firmware.execute`;
- `security.read` / `security.remediate`;
- `reports.generate`;
- `users.manage`;
- `settings.manage`.

## 16. Global navigation

Desktop sidebar, collapsible:

- Dashboard
- Inventory
  - Customers
  - Devices
- Operations
  - Monitoring
  - Backups
  - Firmware
- Security
  - Vulnerabilities
  - Lifecycle / EOL-EOS
- Action Center
- Audit
  - Events
  - Reports
- Integrations
- Administration

`Sites` is intentionally absent from global navigation because Sites are customer-local.

Top bar:

- global search;
- notification bell/unread count;
- user menu;
- theme control.

## 17. Global search

Search targets:

- Customer;
- customer-local Site;
- alias;
- Device identity;
- model;
- MAC;
- serial;
- management IP.

MAC search should normalize colon, dash and plain forms and be case-insensitive.

Search should use server-side filtering/pagination and remain efficient for a large inventory.

## 18. Deployment and upgrade safety

Production data and secrets are not stored in Git.

Never commit:

- `.env`;
- runtime secrets;
- PostgreSQL data directory;
- Redis data;
- backups;
- reports;
- evidence;
- uploads;
- logs.

Upgrade sequence:

1. validate source/revision;
2. run PostgreSQL `pg_dump` backup;
3. apply Alembic migrations;
4. rebuild/restart services;
5. run health checks;
6. preserve existing users/data/audit.

Do not raw-copy a live PostgreSQL data directory as the application-level DB backup mechanism.

## 19. NIS2 / compliance positioning

NSM supports operational evidence and control implementation useful in NIS2-oriented programs, but the product must not claim that installing it alone makes an operator compliant.

Relevant evidence domains include:

- asset inventory;
- firmware/vulnerability status;
- backup and restore evidence;
- configuration/change history;
- supplier/integration inventory;
- lifecycle/EOL/EOS;
- incident/action tracking;
- access/user administration;
- reports and retained evidence.

## 20. Current implementation milestone

Through Core 0.9, the project includes foundations for:

- Customer/Site/Device model;
- customer-local Site validation;
- customer-centric UI and KPI counters;
- global inventory/search;
- audit and Action Center foundations;
- backup policies and retention settings;
- secure MikroTik enrollment/heartbeat/job queue;
- real encrypted MikroTik chunked backup transport;
- backup scheduler/retry/retention/agent health;
- per-customer backup workspace;
- capability-aware backup form and server-side option normalization;
- agent reinstall/update workflow foundation;
- CI covering migrations and integrated workflows.

## 21. Near-term roadmap

Priorities after Core 0.9:

1. formal backup capability registry and truthful executable-coverage calculation;
2. complete backup scheduler production worker behavior and operational visibility;
3. UISP connector implementation and MAC-based association workflow;
4. GenieACS integration and TR-069 capability discovery;
5. richer Device monitoring data from agents/connectors;
6. firmware catalog/update workflow with security classification;
7. CVE source ingestion, version matching and remediation workflow;
8. lifecycle/EOL/EOS source ingestion;
9. notifications rules and external channels;
10. report generation/evidence archive;
11. restore-test workflow and evidence;
12. mature RBAC administration and policy inheritance;
13. HTTPS/FQDN production hardening and secure-cookie enforcement.

## 22. Product rule for unsupported features

The UI must not present an operation as executable merely because a future integration is planned.

Use explicit states such as:

- Available;
- Connector required;
- Capability not exposed by device;
- Not implemented yet;
- Unsupported.

This rule is especially important for Backup, Restore, Firmware and remote actions.

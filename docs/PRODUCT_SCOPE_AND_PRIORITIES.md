# NSM Platform — Product Scope and Priorities

Status: durable product-scope decision

This document narrows the operational target of NSM and constrains how the living roadmap should be interpreted.

## 1. Primary scope

NSM is primarily intended for the **management, inventory, tracking, maintenance, security and compliance evidence of devices installed at customer premises or customer-serving edge locations**.

Typical managed assets include, according to the connector/capability available:

- MikroTik routers and CPE/customer-edge devices;
- Ubiquiti AirMax AC / LTU and other Ubiquiti customer-serving devices through UISP;
- TP-Link and other CPE managed through TR-069/ACS;
- additional customer-installed network appliances added through future vendor adapters, ACS profiles or safe generic integrations.

The product should answer questions such as:

- what equipment is installed for this Customer and Site?
- what model, serial, MAC, firmware and management source does it have?
- is the Device online and when was it last verified?
- is its configuration backed up and has it changed?
- is firmware current, supported and affected by known security issues?
- is the Device EOL/EOS?
- what operational incidents or remediation actions involved it?
- what evidence can be produced for internal controls, audits and NIS2-oriented processes?

## 2. Explicit non-priority: ISP backbone/core

The current product roadmap must **not expand into a full ISP backbone/core management platform** unless a future product decision explicitly changes this scope.

The following are currently out of scope or non-priority:

- ISP backbone topology reconstruction;
- POP-to-POP dependency mapping;
- BGP policy/route optimization;
- OSPF/MPLS/VPLS engineering and simulation;
- traffic-engineering/path-selection optimization;
- backbone capacity planning;
- automated core failover design;
- ISP-wide routing diagnostics unrelated to a managed customer-edge Device;
- NMS replacement for the operator's complete core network.

NSM may still store or use limited network information when it is directly necessary to manage a customer-installed Device, but that must not evolve silently into a backbone-management subsystem.

Before starting any new feature, development should ask:

> Does this capability improve management, tracking, maintenance, security, backup, lifecycle or compliance evidence for customer-installed devices?

If the answer is no and the feature primarily belongs to ISP-core operations, it should not be selected from the roadmap without an explicit scope decision.

## 3. Scope hierarchy for development priority

Preferred priority order:

1. reliable customer/Device/Site inventory and identity;
2. vendor onboarding and synchronization;
3. monitoring and health of customer-installed devices;
4. configuration backup, history and restore evidence;
5. firmware and lifecycle management;
6. CVE/security correlation and remediation;
7. Action Center and incident management;
8. compliance baselines and evidence;
9. customer/executive/NIS2-oriented reporting;
10. additional vendor adapters and device families.

Backbone/core features do not belong in this priority sequence.

---

# 4. Planned feature — Incident Timeline / Root Cause

Status: planned

Purpose: provide a single incident view for a Customer, Site or Device by correlating existing operational evidence around a fault or degradation.

## Required capabilities

- create an Incident manually or automatically from an actionable condition;
- associate one or more Customers, Sites and Devices;
- define incident start/end timestamps and current state;
- build a chronological timeline from relevant NSM evidence;
- correlate, where available:
  - online/offline/recovery transitions;
  - agent/connector failures and recoveries;
  - configuration drift/baseline events;
  - firmware changes and reboot activity;
  - backup success/failure/missing-backup conditions;
  - diagnostic jobs and their results;
  - CVE/security findings and remediation actions;
  - EOL/EOS findings;
  - Action Center state transitions;
  - operator notes and manual evidence;
- highlight events immediately before and after the incident start;
- allow filtering noise from unrelated events;
- retain a final incident summary and remediation notes;
- generate an incident evidence package/report;
- audit creation, edits, closure and evidence export.

## Root-cause handling rule

NSM may present a **probable cause or correlated evidence**, but it must not present correlation as certainty without evidence.

The UI should distinguish:

- observed fact;
- correlated event;
- operator-confirmed root cause;
- hypothesis/unconfirmed cause.

This feature is focused on customer-device incidents, not ISP backbone root-cause analysis.

---

# 5. Planned feature — Compliance Baseline

Status: planned

Purpose: continuously compare each managed customer Device against an approved operational/security baseline and turn deviations into auditable findings.

## Baseline model

Support baseline inheritance with a specificity model such as:

1. Device override;
2. Site;
3. Customer;
4. vendor/model/profile;
5. global default.

A baseline rule must declare:

- rule identifier;
- description;
- applicable vendor/model/capability;
- expected state;
- severity;
- evidence source;
- evaluation timestamp;
- remediation guidance;
- whether an exception is allowed.

## Initial useful controls

Where the vendor/device capability exposes reliable evidence, evaluate controls such as:

- approved firmware/channel/version policy;
- unsupported/EOL/EOS firmware or hardware;
- unresolved High/Critical CVEs;
- agent/connector missing, stale or unhealthy;
- backup policy missing;
- latest successful backup older than policy threshold;
- restore test missing or expired;
- configuration drift from approved baseline;
- management service/protocol not allowed by policy;
- insecure legacy service enabled where reliably observable;
- expected DNS/NTP/syslog/management settings where the configuration source supports trustworthy evaluation;
- required monitoring/heartbeat freshness;
- certificate or integration credential approaching expiry where managed by NSM.

Rules must be vendor/capability-aware. A CPE must not fail a control merely because its connector does not expose the required parameter.

## Finding lifecycle

Suggested states:

- OPEN;
- ACKNOWLEDGED;
- REMEDIATION_PLANNED;
- RESOLVED;
- EXCEPTION;
- NOT_APPLICABLE.

Requirements:

- automatic re-evaluation after relevant inventory/configuration/firmware changes;
- exception justification and optional expiry/review date;
- link finding to Action Center when actionable;
- retain evidence used for pass/fail decisions;
- retain history after resolution;
- expose baseline compliance per Customer, Site and Device;
- support report/export use.

The baseline is an operational control/evidence tool. It must not claim that passing all NSM rules alone proves legal or NIS2 compliance.

---

# 6. Planned feature — Scheduled Executive / NIS2 Reports

Status: planned

Purpose: automatically generate periodic management/compliance evidence reports from the information already collected by NSM.

## Scheduling

Support at least:

- monthly;
- quarterly;
- annual;
- custom recurring schedules where useful.

A schedule should define:

- scope: all Customers, selected Customers, one Customer, Site or selected Devices;
- reporting period;
- report template;
- recipients/delivery policy when external delivery channels are enabled;
- retention/archive policy.

## Initial report content

Depending on selected scope/template:

- Device inventory and significant inventory changes;
- firmware status and completed/failed update activity;
- open/resolved vulnerabilities and remediation state;
- EOL/EOS lifecycle findings;
- backup coverage, last backup, failures and restore-test evidence;
- configuration drift/baseline changes;
- Compliance Baseline findings and exceptions;
- Incident Timeline summaries and confirmed root causes;
- Action Center open/acknowledged/resolved items;
- connector/agent health exceptions;
- audit summary for material actions;
- unresolved high-severity risks requiring management attention.

## Evidence requirements

- minimum PDF generation;
- CSV attachments/exports for tabular evidence where useful;
- report generation timestamp;
- generating user/system identity;
- covered date range and scope;
- SHA-256 or equivalent report hash;
- immutable/archive reference according to retention policy;
- audit event for generation;
- audit event for manual download/export;
- delivery result where scheduled delivery is later implemented.

The report should provide management and NIS2-oriented operational evidence without making a legal-compliance certification claim.

---

# 7. Relationship with the living roadmap

`NEXT_IMPLEMENTATIONS.md` remains the detailed implementation backlog. This document is a **scope guardrail** and a durable priority decision.

When choosing new work from the roadmap:

- customer-edge/device-management features take priority;
- ISP backbone/core expansions are skipped unless explicitly re-approved;
- Incident Timeline / Root Cause, Compliance Baseline and Scheduled Executive/NIS2 Reports are considered target product capabilities;
- once any of these features becomes operational, the delivered behavior must move into `IMPLEMENTED_CAPABILITIES.md`.

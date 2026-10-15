# Feature Roadmap — Incident Timeline / Root Cause

Status: planned feature

## Objective

Provide a single incident view for a Customer, Site or Device by correlating operational evidence around a fault or degradation affecting customer-installed/customer-serving edge equipment.

This feature does **not** target ISP-backbone root-cause analysis.

## PR slicing

Implementation should proceed as separate focused PRs:

### PR 1 — Incident model and timeline aggregation

- Incident entity/state/start/end timestamps.
- Customer/Site/Device associations.
- Manual incident creation/closure.
- Timeline aggregation from existing NSM evidence.
- Audit of incident lifecycle.

### PR 2 — Incident UI and evidence

- Incident list/detail UI.
- chronological event timeline;
- filters/noise suppression;
- operator notes;
- evidence attachments/links where supported;
- incident summary and remediation notes.

### PR 3 — Correlation and Root Cause workflow

- correlate events immediately before/after incident start;
- surface relevant configuration drift, firmware/reboot, backup failures, agent/connector failures, diagnostics, security findings and Action Center transitions;
- distinguish correlation from confirmed causality;
- operator-confirmed root-cause workflow.

### PR 4 — Incident evidence export/report integration

- incident evidence package;
- report integration;
- generation/download audit events.

Do not combine all four steps into one implementation PR.

## Timeline evidence sources

Where available, correlate:

- Device online/offline/recovery transitions;
- agent/connector failures and recoveries;
- configuration baseline/drift events;
- firmware changes and reboots;
- backup success/failure/missing-backup conditions;
- diagnostic jobs/results;
- CVE/security findings and remediation actions;
- EOL/EOS findings;
- Action Center transitions;
- operator notes/manual evidence.

## Root-cause truthfulness

The UI must distinguish:

- **Observed fact** — directly recorded evidence.
- **Correlated event** — temporally/operationally related evidence.
- **Hypothesis** — possible cause not confirmed.
- **Operator-confirmed root cause** — explicitly confirmed by a user with audit trail.

NSM must not present correlation as certainty.

## Acceptance criteria

The feature is complete only when:

- incidents can be created, updated and closed;
- impacted customer-edge Devices can be associated;
- relevant existing evidence is shown chronologically;
- evidence source/timestamp remains clear;
- operator-confirmed root cause is separate from automated correlation;
- material changes are audited;
- list/detail views support realistic history volume;
- automated tests cover lifecycle and timeline aggregation;
- report/export integration is implemented in its own final step.

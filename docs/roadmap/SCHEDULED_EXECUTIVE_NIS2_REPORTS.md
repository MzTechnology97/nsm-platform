# Feature Roadmap — Scheduled Executive / NIS2 Reports

Status: planned feature

## Objective

Automatically generate periodic management/compliance evidence reports from information already collected by NSM for customer-installed/customer-serving edge devices.

The output is evidence-oriented and must not claim legal certification or automatic NIS2 compliance.

## PR slicing

Implementation should proceed as separate focused PRs:

### PR 1 — Report model and archive

- report definition/template model;
- generated report record;
- scope and covered date range;
- creator/system identity;
- archive/storage reference;
- report hash;
- generation audit event.

### PR 2 — Manual report generation

- one Customer / selected Customers / Site / selected Devices scope;
- date-range selection;
- minimum PDF generation;
- structured report sections from implemented evidence sources;
- download/export audit event.

### PR 3 — Scheduling engine

Support at least:

- monthly;
- quarterly;
- annual;
- custom recurring schedule where useful.

Include:

- schedule enable/disable;
- report period calculation;
- retention policy;
- retry/failure state;
- Action Center/system warning on persistent generation failure where appropriate.

### PR 4 — Delivery channels

Only after delivery channels exist:

- recipients/delivery policy;
- Email delivery;
- later external channels where appropriate;
- delivery result/retry history;
- audit evidence.

Do not combine all report functionality into one implementation PR.

## Initial report content

Depending on selected scope/template and implemented source capabilities:

- Device inventory and significant inventory changes;
- firmware status and completed/failed update activity;
- open/resolved vulnerabilities and remediation state;
- EOL/EOS lifecycle findings;
- backup coverage, last backup, failures and restore-test evidence when implemented;
- configuration drift/baseline changes;
- Compliance Baseline findings and exceptions when implemented;
- Incident Timeline summaries and confirmed root causes when implemented;
- Action Center open/acknowledged/resolved items;
- connector/agent health exceptions;
- audit summary for material actions;
- unresolved high-severity risks requiring management attention.

## Evidence requirements

Each generated report should retain:

- generation timestamp;
- generating user/system identity;
- covered date range;
- target scope;
- template/version;
- SHA-256 or equivalent hash;
- immutable/archive reference according to retention policy;
- generation audit event;
- manual download/export audit event;
- delivery result when scheduled delivery is implemented.

## Output formats

Initial target:

- PDF as the primary management/report format;
- CSV for tabular supporting evidence where useful.

Future JSON/API export may be added separately if needed.

## Acceptance criteria

The feature is complete only when:

- a report can be generated from selected scope/date range;
- source sections clearly distinguish unavailable data from compliant/healthy state;
- generated artifact is archived with hash/evidence metadata;
- generation/download is audited;
- recurring schedules generate the correct period automatically;
- failed scheduled runs are visible/retryable;
- tests cover period calculation, scoping, archive/hash metadata and scheduled generation;
- the UI never claims that a generated report is a legal compliance certificate.

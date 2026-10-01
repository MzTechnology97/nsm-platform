# Feature Roadmap — Compliance Baseline

Status: planned feature

## Objective

Continuously compare each managed customer Device against an approved operational/security baseline and convert deviations into auditable findings.

The feature is intended for customer-installed/customer-serving edge devices and evidence workflows. It does not claim that passing NSM rules alone proves legal or NIS2 compliance.

## PR slicing

Implementation should proceed as separate focused PRs:

### PR 1 — Baseline model and inheritance

- baseline/rule models;
- applicability by vendor/model/capability;
- inheritance precedence;
- severity/evidence/remediation metadata;
- exception policy fields.

Suggested precedence:

1. Device override;
2. Site;
3. Customer;
4. vendor/model/profile;
5. global default.

### PR 2 — Evaluation engine

- evaluate only rules for which trustworthy evidence exists;
- persist pass/fail/unknown/not-applicable result;
- record evidence source and evaluation timestamp;
- automatic re-evaluation after relevant inventory/configuration/firmware changes;
- no failure when the connector/device simply does not expose the required evidence.

### PR 3 — Findings and remediation workflow

Suggested states:

- OPEN;
- ACKNOWLEDGED;
- REMEDIATION_PLANNED;
- RESOLVED;
- EXCEPTION;
- NOT_APPLICABLE.

Include:

- exception justification and expiry/review date;
- remediation guidance;
- assignee/technician where the platform supports assignment;
- Action Center link for actionable failures;
- history retained after resolution.

### PR 4 — Compliance UI and reporting integration

- Customer/Site/Device compliance views;
- filters by severity/status/rule/vendor;
- coverage and unknown-evidence states;
- baseline findings export/report integration;
- audit evidence for rule/exception changes.

Do not combine all steps into one implementation PR.

## Initial useful controls

Only where reliable evidence exists:

- approved firmware/channel/version policy;
- unsupported/EOL/EOS firmware or hardware;
- unresolved High/Critical CVEs;
- agent/connector missing, stale or unhealthy;
- backup policy missing;
- latest successful backup older than policy threshold;
- restore test missing/expired when implemented;
- configuration drift from approved baseline;
- management service/protocol not allowed by policy;
- insecure legacy service enabled where reliably observable;
- expected DNS/NTP/syslog/management settings where snapshots expose trustworthy state;
- required heartbeat/monitoring freshness;
- certificate/integration credential approaching expiry where NSM manages that evidence.

## Rule contract

Each rule should define at minimum:

- stable rule identifier;
- description;
- applicability;
- expected state;
- severity;
- evidence source;
- evaluation timestamp;
- remediation guidance;
- whether an exception is permitted.

## Truthfulness rules

- `unknown` is not the same as `failed`;
- `unsupported` is not the same as `non-compliant`;
- stale evidence must be distinguishable from current evidence;
- vendor/capability differences must be respected;
- legal compliance must not be inferred solely from technical checks.

## Acceptance criteria

The feature is complete only when:

- inherited rules resolve deterministically;
- unsupported evidence does not create false failures;
- findings have an auditable lifecycle;
- Action Center integration exists for actionable failures;
- exceptions are justified and reviewable;
- evaluation automatically reacts to relevant Device changes;
- customer/device views show coverage, failures and unknowns clearly;
- tests cover inheritance, applicability and finding transitions.

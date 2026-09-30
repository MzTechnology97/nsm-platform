# NSM Platform — Development Workflow

Status: living engineering process

The project uses a strict **one feature → one branch → one PR → tests → merge → documentation update** flow.

The purpose is to prevent silent stalls, accidental scope changes and half-completed work that another developer/agent cannot safely resume.

## 1. One active objective per PR

Every implementation PR must have one coherent, testable objective.

Good examples:

- `UISP periodic synchronization`
- `GenieACS connector`
- `MikroTik compatibility resolver`
- `Compliance Baseline engine`
- `Incident Timeline`

Avoid PRs whose objective is effectively `continue developing NSM` or which combine unrelated vendor/features.

A feature may contain several commits, but every commit must serve the same objective or a dependency discovered while implementing it.

## 2. Do not open every future PR at once

The roadmap is a queue, not a request to keep dozens of inactive PRs open.

Preferred flow:

1. finish or explicitly block the current implementation PR;
2. merge it when CI and required device tests pass;
3. update documentation;
4. open the next focused implementation PR from the queue.

Documentation/design PRs may exist in parallel when they do not compete with the active implementation branch.

## 3. Mandatory visible status

Every implementation PR must expose one of these states:

- `ACTIVE`
- `BLOCKED`
- `READY FOR TEST`
- `READY FOR MERGE`

The PR body must also contain:

- current step;
- next concrete step;
- implemented work;
- still-missing work;
- tests passing/failing;
- real-device validation status where required.

If repository activity stops and the PR is not marked `BLOCKED`, progress must not be assumed.

## 4. No silent scope switching

An agent/developer must not abandon a difficult feature and silently start an unrelated one.

A different feature may start only when the active objective is:

- merged;
- explicitly closed/abandoned with reason;
- or explicitly `BLOCKED` by an external dependency, with the switch recorded.

Newly discovered unrelated work belongs in the implementation queue or in a separate future PR.

## 5. Blocking protocol

A blocked PR must record:

- exact blocker;
- last successful step;
- failing command/test/log;
- affected files/components;
- whether the current code remains safe/usable;
- exact next action required;
- whether user input, hardware or external service access is required.

This information must be sufficient for another agent/developer to continue without reconstructing the entire chat history.

## 6. Source of truth before starting

Before a new feature PR:

1. inspect current `main`;
2. inspect open PRs for overlap/conflicts;
3. read `docs/IMPLEMENTED_CAPABILITIES.md` when present;
4. read the implementation queue/feature specification;
5. inspect current code paths rather than assuming an older conversation still matches the repository;
6. define acceptance criteria and vendor/version scope.

## 7. Development loop

### Define

- one operational objective;
- acceptance criteria;
- vendor/model/firmware/capability scope;
- migration risk;
- required real-device validation.

### Implement foundation

- schema/domain changes when required;
- service/connector/agent behavior;
- explicit unsupported/failure behavior;
- server-side permission/capability guards.

### Implement operational path

- actual job/sync/poll/action path;
- authentication;
- timeout/retry/idempotency where relevant;
- no arbitrary remote shell.

### Implement UI

- usable operational view;
- human-readable units;
- explicit no-data/unsupported/error states;
- search/filter/pagination for unbounded data;
- structured results instead of raw payload dumps when possible.

### Add evidence

For material operations add, where applicable:

- Audit event;
- Action Center finding;
- notification;
- automatic recovery/resolution event.

### Test

- unit/domain tests;
- integration/smoke tests;
- migration tests if schema changes;
- generated RouterOS source checks when relevant;
- sanitized connector fixtures;
- real-device/vendor tests when simulation cannot prove behavior.

### Document

- move delivered behavior into `IMPLEMENTED_CAPABILITIES.md`;
- update/remove the completed implementation-queue item;
- update the high-level README only when actual project status changed.

### Merge and verify

- CI green;
- required device/vendor validation complete;
- merge;
- deploy/promotion;
- production/real-device smoke test when applicable;
- then select the next queued feature.

## 8. PR sizing rule

Prefer a complete vertical slice rather than either extreme:

- too large: `Implement Ubiquiti support`;
- too small: ten PRs that individually leave a non-functional feature.

Example good split for Ubiquiti:

1. UISP periodic synchronization;
2. Ubiquiti monitoring normalization;
3. Ubiquiti backup capability;
4. Ubiquiti firmware workflow;
5. Ubiquiti CVE/lifecycle correlation.

Example good split for TR-069:

1. GenieACS connector;
2. Device discovery/association;
3. Inventory normalization;
4. vendor parameter profiles;
5. monitoring;
6. diagnostics;
7. backup/restore;
8. firmware workflow.

## 9. Real-device test protocol

Record for every required physical-device test:

- vendor/model;
- firmware version/channel;
- architecture where relevant;
- agent/connector revision;
- operation tested;
- exact sanitized outcome;
- whether the result is supported, unsupported or defective.

Never commit enrollment tokens, secrets, API credentials, customer data or unsanitized captured configuration.

For RouterOS, passing Python/TestClient tests is not proof that generated RouterOS source parses on a real router.

## 10. Customer-edge scope guard

Before starting a new feature, verify that it improves management, tracking, maintenance, security, backup, lifecycle or compliance evidence for customer-installed/customer-serving edge devices.

ISP backbone/core features are not selected unless the product scope is explicitly changed.

## 11. Definition of done

A feature is complete only when applicable items are satisfied:

- backend/data model;
- actual agent/connector/device path;
- server-side authorization and capability enforcement;
- usable UI;
- explicit unsupported/error state;
- audit/evidence;
- automated tests;
- required real-device/vendor validation;
- documentation update;
- CI green;
- post-merge smoke verification where applicable.

A route, menu entry, model or placeholder alone is not a completed feature.

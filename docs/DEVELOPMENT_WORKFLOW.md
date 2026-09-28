# NSM Platform — Development Workflow

Status: living engineering process

This document exists to prevent development from silently stopping, changing direction without notice, or leaving half-completed work that another developer/agent cannot safely resume.

The workflow is intentionally simple: **one active objective, one branch, one PR, visible progress, explicit handoff**.

---

## 1. Source of truth before starting work

Before starting any implementation:

1. fetch the current `main`;
2. inspect open PRs to avoid duplicating or conflicting work;
3. read `docs/IMPLEMENTED_CAPABILITIES.md`;
4. read the relevant section of `docs/NEXT_IMPLEMENTATIONS.md`;
5. inspect the current code paths instead of assuming an older conversation/release still matches the repository;
6. define the smallest coherent objective that can be completed and tested.

Do not start from stale release assumptions when `main` has advanced.

---

## 2. One active objective per PR

Each development PR must have a single primary objective.

Good examples:

- `UISP periodic inventory synchronization`;
- `GenieACS connector and device lookup`;
- `MikroTik compatibility resolver`;
- `Security advisory ingestion worker`.

Avoid PRs whose scope is effectively `continue developing NSM`.

A PR may contain several commits, but every commit must serve the same objective or a required dependency discovered while implementing it.

If another unrelated improvement is discovered, add it to `NEXT_IMPLEMENTATIONS.md` or open a separate follow-up PR instead of silently switching scope.

---

## 3. Mandatory PR progress checklist

Every implementation PR should keep a live checklist in the PR body:

```text
Status: ACTIVE | BLOCKED | READY FOR TEST | READY FOR MERGE

Objective:
- ...

Implementation:
- [ ] backend/data model
- [ ] agent/connector execution path
- [ ] UI
- [ ] authorization/capability guards
- [ ] audit/evidence
- [ ] automated tests
- [ ] real-device validation, if required
- [ ] documentation update

Current step:
- ...

Next step:
- ...
```

This checklist is the quickest way to determine whether development is progressing or has stopped.

---

## 4. No silent stalls

If work cannot continue, the PR must be explicitly marked **BLOCKED** rather than leaving the branch inactive with no explanation.

The blocking note must include:

- exact blocker;
- last successful step;
- failing command/test/log or external dependency;
- affected files/components;
- whether existing code remains safe/usable;
- exact next action needed to resume;
- whether user input or real-device testing is required.

Example:

```text
Status: BLOCKED

Blocker:
RouterOS 7.24.4 rejects generated source at line 46.

Last successful step:
Enrollment and bootstrap fetch succeed.

Evidence:
Script Error: syntax error (line 46 column 24)

Next action:
Capture final generated agent source, identify parser-invalid syntax, patch generator, rerun source regression test and repeat on the same router.
```

An agent/developer must not switch to another roadmap item merely because the current one is difficult.

A different item may start only when the current objective is:

- merged;
- explicitly abandoned/closed;
- or explicitly BLOCKED by an external dependency and the switch is recorded.

---

## 5. Visible progress rule

Long implementation sessions should leave observable evidence of progress.

At least one of the following should change as work progresses:

- commits pushed to the active branch;
- PR checklist/state updated;
- PR comment with test/result/blocker;
- CI run triggered by a pushed change.

Do not claim that development is continuing when there is no repository activity and no explicit local/blocking state that can be verified.

For substantial work, prefer small meaningful commits over one very large final commit.

Example sequence:

```text
connector model/config
→ API client
→ normalization
→ UI
→ background worker
→ tests
→ docs
```

---

## 6. Development loop

For each objective:

### Step 1 — Define

- identify roadmap item;
- inspect current implementation;
- define acceptance criteria;
- define vendor/version/capability scope;
- identify data migration risk.

### Step 2 — Implement foundation

- schema/model changes where required;
- domain/service layer;
- connector/agent capability contract;
- failure/unsupported behavior.

### Step 3 — Implement operational path

- actual Device/connector execution;
- job/polling/synchronization;
- authentication/authorization;
- retry/idempotency where needed.

### Step 4 — UI

- operational view;
- search/filter/pagination if data can grow;
- human-readable units;
- explicit no-data/unsupported/error states;
- no raw payload dump when structured rendering is possible.

### Step 5 — Evidence

For material operations add:

- Audit event;
- Action Center issue when actionable;
- notification where useful;
- recovery/resolution event where the condition can return to normal.

### Step 6 — Test

- unit/domain test where appropriate;
- integration/smoke test;
- migration test if schema changes;
- source-generation test for RouterOS scripts;
- sanitized API fixtures for connectors;
- real-device validation where simulation cannot prove behavior.

### Step 7 — Document

- update `IMPLEMENTED_CAPABILITIES.md` with only what actually works;
- remove/reduce completed items in `NEXT_IMPLEMENTATIONS.md`;
- update README checkbox only when implementation is actually merged/operational.

### Step 8 — Merge and verify

- CI green;
- merge;
- deploy/promotion process completes;
- perform production/real-device smoke test when applicable;
- open a focused follow-up issue/PR for any newly discovered defect.

---

## 7. Continuation / handoff protocol

When another agent/developer must continue the same work, the active PR must make continuation possible without reconstructing the entire history.

Required handoff information:

```text
Objective
Current status
Branch / PR
Latest meaningful commit
Implemented so far
Still missing
Tests passing
Tests failing
Real-device results
Known risks
Next exact code change/test
```

Do not rely only on chat history. Important engineering state belongs in GitHub.

---

## 8. Scope-change protocol

If implementation reveals that the original architecture is wrong or insufficient:

1. do not quietly rewrite the objective;
2. explain the discovered constraint in the PR;
3. update the acceptance criteria;
4. update `PRODUCT_REQUIREMENTS.md` only if the durable product decision changes;
5. update `NEXT_IMPLEMENTATIONS.md` if additional work becomes necessary;
6. keep already-working behavior regression-tested.

---

## 9. Real-device test protocol

Real-device testing is mandatory for behavior that depends on vendor parsers, firmware syntax or undocumented API details.

For every real-device result record:

- vendor/model;
- firmware version/channel;
- architecture where relevant;
- agent/connector revision;
- operation tested;
- exact outcome;
- sanitized error/log;
- whether the result is supported, unsupported or defective.

Never commit:

- enrollment tokens;
- device secrets;
- API tokens;
- credentials;
- private customer data;
- unsanitized captured configuration.

For RouterOS, a Python/TestClient success is not proof that generated RouterOS source parses on a real router.

---

## 10. PR size and sequencing

Prefer a series of complete, dependency-ordered PRs over one enormous PR.

Recommended size:

- one capability or one coherent vertical slice;
- enough code that the feature can be tested end-to-end;
- small enough to review and recover if CI fails.

Avoid splitting a feature so aggressively that every intermediate merge leaves non-functional UI claiming functionality that does not yet exist.

---

## 11. Priority classes

Use consistent priority labels where available:

- `urgent` — current code blocks enrollment, operation, data integrity or security-critical testing;
- `high` — major missing product capability or widespread operational defect;
- `normal` — planned development;
- `low` — polish or optional enhancement.

Urgent work should pre-empt roadmap work only when the reason is recorded.

---

## 12. Recommended implementation cadence

For this project the preferred cadence is:

1. choose one roadmap block;
2. open implementation PR;
3. implement until CI is green or explicitly BLOCKED;
4. perform any required real-device test;
5. fix regressions before taking another roadmap item;
6. merge;
7. update living documentation;
8. only then start the next block.

This is intentionally stricter than opportunistic feature development because the project combines backend state, RouterOS scripts, vendor connectors, scheduled workers, security workflows and production data migrations.

---

## 13. High-value automatic checks to add/maintain

CI should progressively enforce:

- application import/startup;
- migration upgrade from an existing schema;
- permission enforcement;
- duplicate route detection;
- server-side pagination on large worklists;
- capability guards for unsupported actions;
- final composed RouterOS source syntax/static hazards;
- no committed runtime secrets;
- backup path/storage safety;
- connector response bounds/timeouts;
- sanitized fixtures;
- documentation links and checklist consistency where practical.

---

## 14. Definition of complete development flow

A development item is complete only when:

```text
roadmap item selected
        ↓
PR ACTIVE
        ↓
implementation commits
        ↓
CI green
        ↓
real-device/vendor test where needed
        ↓
PR READY FOR MERGE
        ↓
merge
        ↓
deploy/smoke verification
        ↓
IMPLEMENTED_CAPABILITIES updated
        ↓
NEXT_IMPLEMENTATIONS item removed/reduced
        ↓
next objective selected
```

Any interruption in that flow must be visible as **BLOCKED**, with enough information for another agent/developer to resume immediately.

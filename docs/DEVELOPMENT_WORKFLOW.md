# NSM Platform — Development Workflow

Status: **living engineering process**

NSM uses a strict focused-development flow so a human or coding agent can resume work from repository state without reconstructing chat history.

## 1. Core rule

Use:

```text
one coherent objective
    ↓
one branch
    ↓
one PR
    ↓
automated tests
    ↓
required physical/vendor acceptance
    ↓
merge
    ↓
documentation update
```

A PR may contain several commits, but every commit must serve the same operational objective or a dependency discovered while implementing it.

Avoid both extremes:

- too broad: `implement all Ubiquiti support`;
- too fragmented: several PRs that each leave the feature unusable.

Prefer a testable vertical slice.

## 2. Current development priority

Until explicitly changed, choose work in this order:

1. **runtime bugs/regressions** affecting already implemented workflows;
2. **MikroTik Agent / RouterOS compatibility defects** — enrollment, heartbeat, jobs, snapshots, diagnostics, backup, update/rollback;
3. **GUI defects and broken operator workflows** — navigation, stale state, action feedback, inconsistent capability state;
4. roadmap feature expansion only after the active defect/acceptance queue is controlled.

A production-blocking Agent/GUI defect may immediately become the primary objective even if a roadmap feature had previously been next.

## 3. Do not open the entire roadmap at once

`ROADMAP.md` is a prioritized queue, not a request for dozens of inactive implementation PRs.

Preferred sequence:

1. inspect current `main` and open PRs;
2. finish or explicitly block the current primary implementation objective;
3. merge when its own criteria are satisfied;
4. select the next focused objective;
5. open its PR only when development actually begins.

Documentation/design work may exist in parallel when it does not compete with the active runtime objective.

## 4. Mandatory PR state

Every implementation PR must visibly use one of:

- `ACTIVE`
- `BLOCKED`
- `READY FOR TEST`
- `READY FOR MERGE`

The PR body should expose:

- objective;
- confirmed bug/need;
- implemented change;
- scope boundary;
- automated test state;
- physical/vendor test state where required;
- blocker if any;
- exact next step.

Do not label a PR `READY FOR MERGE` when a stated physical acceptance gate is still open.

## 5. No silent scope switching

Do not abandon a hard problem and silently start an unrelated feature.

A different implementation objective may become primary only when the current one is:

- merged;
- explicitly closed/superseded with reason;
- or explicitly `BLOCKED` by an external dependency, with the blocker and next action recorded.

Unrelated issues discovered during implementation belong in a focused future PR/roadmap entry unless they directly block the current objective.

## 6. Blocking protocol

A blocked PR must record enough information for another agent to continue safely:

- exact blocker;
- last successful step;
- failing test/command and useful sanitized error;
- affected components/files;
- whether current code remains safe;
- exact next action;
- whether physical hardware, user input or an external service is required.

Never describe a CI failure as a code regression until logs show the failing code/test. Runner/quota/infrastructure failures and assertion failures are different blockers.

## 7. Source-of-truth preflight

Before writing code:

1. read root `README.md`;
2. read `docs/IMPLEMENTED_CAPABILITIES.md`;
3. read `docs/ROADMAP.md`;
4. read the relevant section of `docs/ARCHITECTURE.md`;
5. inspect current open PRs for overlap;
6. inspect actual current code and tests;
7. define acceptance criteria and vendor/version scope.

Old chats, stale branch descriptions and historical milestone docs are context, not authoritative runtime truth.

## 8. Implementation loop

### 8.1 Define

Write down:

- one operational objective;
- user/operator impact;
- acceptance criteria;
- affected vendor/model/firmware/capability;
- data/schema risk;
- authentication/authorization impact;
- physical/vendor acceptance requirement.

### 8.2 Reproduce first for bugs

For a normal bug PR, prefer:

1. reproduce the defect;
2. add/adjust a focused regression test that demonstrates the bad contract;
3. fix the code;
4. retain the regression test;
5. run the full relevant CI suite.

Do not open a spec-only bug PR and postpone implementation unless the issue is explicitly a tracker/architecture decision.

### 8.3 Implement domain behavior

Where applicable:

- schema/model changes;
- service/domain logic;
- explicit state transitions;
- timeout/retry/idempotency;
- authorization/capability checks;
- explicit unsupported behavior.

### 8.4 Implement actual device/connector path

A feature is not complete at the model/UI layer if its value depends on real device/connector behavior.

For Agent/connector work, validate:

- authentication;
- scoping/ownership;
- bounded payloads;
- retry/error semantics;
- idempotency;
- version/capability limits;
- no arbitrary command execution.

### 8.5 Implement UI

User-facing work should provide:

- canonical navigation;
- clear supported/unsupported/no-data state;
- human-readable units/labels;
- filters/pagination for unbounded data;
- contextual success/warning/error feedback;
- no raw payload dump as the primary UX when normalized presentation is possible.

Browser actions must not accidentally change machine-facing API contracts.

### 8.6 Add evidence

For material operations, consider:

- Audit event;
- Action Center issue/resolution;
- user notification;
- artifact/hash/job references;
- recovery evidence.

Routine success does not automatically require an Action Center issue.

### 8.7 Test

Use the appropriate layers:

- focused domain/unit test where useful;
- integration/smoke test;
- migration test for schema changes;
- generated RouterOS source assertions for Agent source composition;
- synthetic connector fixtures;
- full CI;
- physical/vendor acceptance when simulation is insufficient.

## 9. RouterOS-specific acceptance rule

Python/TestClient success is not proof that generated RouterOS source parses or behaves correctly on physical RouterOS.

For every physical RouterOS acceptance, record in the PR:

- model/device class (sanitized if necessary);
- RouterOS version/channel;
- architecture if relevant;
- Agent family/version;
- operation tested;
- sanitized outcome;
- final state: supported / unsupported / defective.

Never commit the Device secret, enrollment token, backup password or unsanitized exported configuration.

## 10. Compatibility changes

When changing RouterOS compatibility:

- do not force one universal script through incompatible firmware generations;
- keep compatibility resolution server-side/capability-aware;
- update UI exposure through the same capability contract;
- fail safely on unknown/unvalidated behavior;
- preserve real-device regression notes in the focused PR/tracker;
- test existing families for regression.

## 11. Backup changes

For backup work, verify ownership of every lifecycle state.

Questions a PR should answer:

- who creates the job/run?
- who marks it delivered/running/terminal?
- who owns timeout/retry?
- how is upload progress proven monotonic?
- what is cleaned after success/failure?
- what happens on duplicate Agent completion?
- which artifacts remain evidence?
- what is the Device/customer isolation rule?

Never expose a Device as `protected` solely because a policy exists when no executable backup method exists.

## 12. External integration policy

Add an external integration reference only when:

- current runtime code consumes it; or
- an active focused implementation PR is building that dependency.

Document only contracts actually needed to maintain the connector:

- authentication model;
- exact API objects/endpoints consumed;
- mapping/normalization;
- timeout/error behavior;
- capability limits.

Do not accumulate unused vendor API documentation or historical implementation references in product docs.

The current UISP connector qualifies because runtime code actively consumes UISP Network Device data.

## 13. Public repository data policy

This repository is public.

Before committing tests, docs, logs or fixtures:

- replace real IPv4 addresses with RFC 5737 documentation addresses;
- use `example.test` / `example.invalid` hostnames;
- use locally administered MACs (`02:` prefix) for fixtures;
- use synthetic Customer/Site/Device/serial identities;
- generate UUIDs where uniqueness matters;
- never commit production credentials, cookies, tokens, private keys or backup passwords;
- never commit captured customer configurations or operational logs without full sanitization.

Run and respect the repository public-data-safety guard. Do not weaken the guard to fit copied production data.

## 14. Screenshot policy

Screenshots are documentation assets and therefore subject to the same public-data policy.

Only capture a synthetic/demo environment. Before commit, manually inspect:

- page content;
- browser address bar;
- user/profile area;
- Customer/Device labels;
- IP/MAC/serial fields;
- logs/job results;
- QR codes/tokens/commands;
- notification text.

Do not blur a real credential and assume the image is safe; prefer generating a demo state that never contained it.

## 15. Documentation maintenance

When a runtime PR changes product state:

- update `IMPLEMENTED_CAPABILITIES.md`;
- update `ROADMAP.md` if a gate closes/new gate appears;
- update `ARCHITECTURE.md` if a boundary changes;
- update root `README.md` only for high-level state changes.

A future capability stays in the roadmap until it is actually implemented.

## 16. CI and merge discipline

Before merge:

- focused tests pass;
- full required CI passes;
- no unrelated failures are ignored;
- physical/vendor acceptance passes when required;
- branch is compatible with current `main`;
- public-data-safety checks pass;
- docs reflect the resulting state.

If CI is failing, inspect the exact job/log before changing code. Fix the first real failure, rerun, and avoid speculative broad changes.

### Release and versioning

Enforced by `app/tests/release_discipline_smoke.py`:

- every merged PR is a release: it bumps the Core patch version (`APP_VERSION` in `app/app/entrypoint.py`, `0.49.N`) and adds a dated `## 0.49.N — YYYY-MM-DD` entry at the top of `docs/CHANGELOG.md`, grouped by area (MikroTik, Ubiquiti, Security, Operations, GUI…);
- versions are unique and consecutive; stacked PRs are renumbered when their order changes;
- the MikroTik agent version (`TARGET_AGENT_VERSION`) is independent from the Core version, changes only when the agent source changes, and is named in the CHANGELOG entry that introduces it;
- Alembic migrations are numbered `0001…` without gaps, form a single chain with one head and always have a downgrade.

## 17. PR sizing examples

### Good Ubiquiti split

1. periodic UISP synchronization;
2. monitoring normalization;
3. bulk onboarding;
4. backup capability if actually exposed;
5. firmware workflow;
6. CVE/lifecycle correlation.

### Good ACS/TR-069 split

1. connector;
2. Device discovery/association;
3. inventory normalization;
4. parameter profiles;
5. monitoring;
6. diagnostics;
7. backup/restore;
8. firmware workflow.

### Good MikroTik defect split

- terminal completion idempotency;
- backup no-progress guard;
- RouterOS temporary-file cleanup;
- legacy structured snapshot parity;

These are related, but each has a separate failure contract and acceptance gate.

## 18. Definition of done

A feature/fix is complete only when the applicable items are satisfied:

- actual backend/domain behavior;
- actual Agent/connector/device path;
- authorization and capability enforcement;
- usable GUI;
- explicit unsupported/error states;
- audit/evidence behavior;
- regression tests;
- required physical/vendor acceptance;
- green CI;
- public-safe committed data;
- documentation update;
- post-merge smoke/deploy validation where appropriate.

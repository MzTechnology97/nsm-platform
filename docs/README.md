# NSM Platform documentation map

This directory is the maintained knowledge base for developers and coding agents working on NSM Platform.

The repository is public. All documentation, examples, fixtures and future screenshots must be safe to publish.

## Mandatory reading order for a development agent

Before changing runtime behavior:

1. read the root [`README.md`](../README.md);
2. read [`IMPLEMENTED_CAPABILITIES.md`](IMPLEMENTED_CAPABILITIES.md);
3. read [`ROADMAP.md`](ROADMAP.md);
4. read [`ARCHITECTURE.md`](ARCHITECTURE.md) for the affected subsystem;
5. read [`DEVELOPMENT_WORKFLOW.md`](DEVELOPMENT_WORKFLOW.md);
6. read [`PUBLIC_REPOSITORY_DATA_SAFETY.md`](PUBLIC_REPOSITORY_DATA_SAFETY.md);
7. inspect current `main` and current open PRs;
8. inspect the actual implementation and tests before assuming older documentation is still correct.

For long-term product intent, also read [`PRODUCT_REQUIREMENTS.md`](PRODUCT_REQUIREMENTS.md).

## Source-of-truth hierarchy

When two documents disagree, use this order:

1. **current code and migrations in `main`** — authoritative for runtime behavior;
2. **`IMPLEMENTED_CAPABILITIES.md`** — maintained summary of what `main` actually provides;
3. **`ROADMAP.md`** — active acceptance gates and approved future work;
4. **`ARCHITECTURE.md`** — current intended technical boundaries;
5. **`PRODUCT_REQUIREMENTS.md`** — durable product direction, including features not implemented yet;
6. old milestone notes, closed PRs and chat history — historical context only.

An open implementation PR is not an implemented capability until merged. A merged feature that still has an explicit physical-device acceptance requirement must be documented as implemented with validation pending, not as universally validated.

## Canonical documents

### `IMPLEMENTED_CAPABILITIES.md`

Use this to answer: **what can NSM actually do today?**

It must:

- describe behavior present in `main`;
- distinguish automated coverage from physical/vendor validation;
- state meaningful capability limits;
- avoid advertising placeholders as functional capabilities;
- be updated when an implementation PR changes the product state.

### `ROADMAP.md`

Use this to answer: **what should be worked on next, and what remains?**

It contains:

- current defect-first priority;
- active acceptance gates;
- MikroTik compatibility/parity work;
- UISP roadmap;
- ACS/TR-069 roadmap;
- vulnerability/lifecycle roadmap;
- Incident Timeline;
- Compliance Baseline;
- Executive/NIS2 reporting;
- RBAC/production hardening.

Roadmap items should be sized into focused future PRs, not opened all at once.

### `ARCHITECTURE.md`

Use this to understand:

- runtime containers and process boundaries;
- data hierarchy;
- Agent and connector trust boundaries;
- job lifecycle;
- backup lifecycle;
- browser-vs-machine API behavior;
- deployment/update flow.

### `DEVELOPMENT_WORKFLOW.md`

Defines how humans and agents work in this repository:

- one coherent objective per PR;
- visible status and blocker handoff;
- defect-first priority;
- tests and physical acceptance;
- public-safe fixtures;
- documentation updates before completion.

### `PUBLIC_REPOSITORY_DATA_SAFETY.md`

Mandatory security policy for a public repository. Do not weaken automated checks to admit operational data.

### `PRODUCT_REQUIREMENTS.md`

Durable product intent. It may describe approved future capabilities. It is **not** evidence that those capabilities are implemented.

## Third-party integration documentation policy

Document an external API or product contract only when NSM runtime code actually depends on it or when an active implementation PR is building that dependency.

For an active connector, document only the minimum contract needed to maintain the integration: authentication model, endpoints/objects actually consumed, normalization rules, failure behavior and capability limits.

Do not accumulate historical vendor/API references that are not used by current code. A future integration should be described generically in the roadmap until its actual implementation technology is selected.

The current UISP integration is a valid exception because runtime code actively consumes UISP Network device data for association and inventory refresh.

## Documentation update rule

Every PR that materially changes product behavior should consider updates to:

- `IMPLEMENTED_CAPABILITIES.md`;
- `ROADMAP.md` if a gate is completed or a new one is discovered;
- `ARCHITECTURE.md` if a trust/process/data boundary changes;
- root `README.md` only when high-level project status changes.

Do not update documentation to claim a capability before its implementation exists.

## Screenshots

Screenshots may be stored under `docs/images/` when a safe demo instance is available.

Required safety rules:

- use demo/synthetic records only;
- no real Customer/Site/Device names;
- no real IP addresses, MAC addresses, serials or hostnames;
- no enrollment tokens, API keys, backup passwords, cookies or session data;
- no usernames or deployment paths that identify an operator installation;
- no browser address bar containing a private deployment hostname;
- no logs containing operational identifiers;
- review the final image manually before committing it.

Recommended first screenshot set:

1. Dashboard;
2. Customer profile / Devices;
3. MikroTik Device overview and Agent workspace;
4. MikroTik Monitor / Configuration workspace;
5. device-scoped Backup Explorer;
6. Agent Fleet;
7. Firmware worklist / upgrade workflow;
8. UISP association workspace using synthetic Ubiquiti data;
9. Action Center;
10. Notification Center.

Screenshots are explanatory assets only. They are never a source of truth for current behavior.

## Historical documents

Files such as milestone notes and focused bug diagnostics remain useful for provenance but should not be the first source consulted by a new agent. When a historical document conflicts with current code or the canonical documents above, update or clearly mark it as historical rather than copying the stale statement forward.

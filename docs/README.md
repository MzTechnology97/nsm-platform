# NSM Documentation Index

The project documentation is split by purpose so product direction, current implementation, scope and future work are not confused.

## Authoritative documents

### `PRODUCT_SCOPE_AND_PRIORITIES.md`

**Current product-scope guardrail.**

It establishes that NSM is primarily focused on management, tracking, maintenance, security, backup, lifecycle and compliance evidence for devices installed at customer premises/customer-serving edge locations.

ISP backbone/core management is currently out of scope or non-priority unless a future product decision explicitly changes that direction.

It also records three target product capabilities:

- Incident Timeline / Root Cause;
- Compliance Baseline;
- Scheduled Executive / NIS2 Reports.

### `PRODUCT_REQUIREMENTS.md`

Product principles, architecture decisions and durable requirements.

It describes what NSM is intended to be. Historical milestone/roadmap text inside that document should not be used as the current implementation-status source.

### `IMPLEMENTED_CAPABILITIES.md`

**Current implementation source of truth.**

It contains only functionality that is actually implemented in the repository. It is version-independent and must be updated as features become operational.

Do not add future/planned behavior to this document.

### `NEXT_IMPLEMENTATIONS.md`

**Living implementation backlog.**

It contains the remaining work needed to reach the complete target system derived from the supplied OptiWize reference material, the agreed NSM architecture and real-device validation.

It is deliberately not tied to a Core/release number.

The backlog must be interpreted through `PRODUCT_SCOPE_AND_PRIORITIES.md`: customer-edge/customer-installed Device capabilities take priority, while ISP backbone/core expansions are skipped unless explicitly approved.

## Required documentation workflow

Whenever a feature is completed:

1. verify that the feature fits the current product scope;
2. implement and test the feature;
3. add the delivered behavior to `IMPLEMENTED_CAPABILITIES.md`;
4. remove the completed item from `NEXT_IMPLEMENTATIONS.md`, or leave only the unfinished sub-items;
5. keep limitations explicit by vendor/device/firmware/capability;
6. update product requirements or product scope only when the durable architecture/product decision itself changes.

A menu entry, placeholder, data model, open PR or planned connector is not enough to classify a capability as implemented.

## Supporting diagnostic/design documents

Focused documents such as RouterOS compatibility investigations may remain in `docs/` as engineering evidence. They do not replace the living implementation documents or the product-scope guardrail above.

# NSM Documentation Index

Project documentation is split by purpose so current implementation, product scope and future work are not mixed together.

## `IMPLEMENTED_CAPABILITIES.md`

Current implementation source of truth.

Contains only behavior that is actually implemented in the repository. Planned behavior does not belong here.

## `PRODUCT_REQUIREMENTS.md`

Durable architecture/product requirements. Historical milestone text inside that document is not the current implementation-status source.

## `PRODUCT_SCOPE_AND_PRIORITIES.md`

Durable scope guardrail: NSM is focused on customer-premises/customer-serving edge devices, not ISP backbone/core management.

## `DEVELOPMENT_WORKFLOW.md`

Defines the one-feature-per-PR development and handoff discipline.

## Feature roadmap documents

Future major capabilities should be specified in focused documents/PRs rather than one enormous roadmap PR.

Examples:

- Incident Timeline / Root Cause;
- Compliance Baseline;
- Scheduled Executive / NIS2 Reports;
- UISP periodic synchronization;
- GenieACS/TR-069 connector;
- CVE ingestion/matching;
- EOL/EOS ingestion.

## Documentation maintenance rule

When a feature is completed:

1. merge tested working behavior;
2. add only the delivered capability to `IMPLEMENTED_CAPABILITIES.md`;
3. close/reduce the corresponding feature roadmap item;
4. update the root README only if high-level project status changed;
5. change `PRODUCT_REQUIREMENTS.md` or product scope only when a durable design decision changed.

A menu entry, placeholder, model or open PR is not enough to mark a capability implemented.

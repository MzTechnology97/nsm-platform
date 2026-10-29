## Objective

<!-- One coherent objective per PR (DEVELOPMENT_WORKFLOW §1). Link the roadmap entry or the operator request. -->

## State

**ACTIVE** <!-- ACTIVE | BLOCKED | READY FOR TEST | READY FOR MERGE — not READY FOR MERGE while a physical acceptance gate is open -->

## Confirmed bug / need

<!-- What was observed or requested, with sanitized evidence. -->

## Change

<!-- What this PR implements. -->

## Scope

- [ ] Backend / data model (migration numbered without gaps, with downgrade)
- [ ] Agent / connector execution path
- [ ] UI
- [ ] Permissions / capability checks
- [ ] Audit / evidence
- [ ] Action Center / notifications where actionable
- [ ] Automated tests
- [ ] Real-device / vendor validation where required
- [ ] Documentation (`IMPLEMENTED_CAPABILITIES.md`, `ROADMAP.md`, `OPERATIONS.md` as needed)

Out of scope: <!-- what this PR deliberately does not touch -->

## Release

- Core version `0.49.N` (`APP_VERSION`) and a dated `## 0.49.N — YYYY-MM-DD` entry at the top of `docs/CHANGELOG.md`
- MikroTik agent version: <!-- unchanged, or the new TARGET_AGENT_VERSION -->

## Tests

- [ ] Focused smoke tests: <!-- list -->
- [ ] `release_discipline_smoke.py`, `public_data_safety_smoke.py`
- [ ] Frontend build (`npm run build`) when the UI changes
- [ ] CI green

### Real-device / vendor validation

<!-- Vendor, model, firmware, operation, sanitized result; or N/A with the reason. -->

## Blocker / next step

<!-- If BLOCKED: exact blocker, last successful step, failing command and sanitized error, whether user input or hardware is needed. Otherwise: the exact next action. -->

<!-- Public repository: synthetic data only (RFC 5737 addresses, 02: MACs, example.test). No credentials, customer names or real identifiers. -->

# Changelog

NSM Core follows `0.49.x`: every release promoted to `deploy` increases the
patch number in `app/app/entrypoint.py` (`APP_VERSION`, shown in the sidebar).
MikroTik Agent versions are independent (`mikrotik_agent_generation.py`).

## 0.49.8 — 2026-10-07

Compliance
- **COMP-04** Compliance tab on every device, summary on the customer security tab, report section *8. Compliance* and CSV columns.

## 0.49.7 — 2026-10-07

Compliance
- **COMP-03** Non-compliance handling: take in charge, exceptions with justification and expiry, automatic clearing when compliant again, history, Action Center issue per device with unhandled failures.

## 0.49.6 — 2026-10-07

Compliance
- **COMP-01/02** Inherited, versioned compliance baselines (global → vendor → customer → site → device) and eight evidence-based controls with pass / fail / no evidence / not applicable results; `/compliance` matrix and baseline editor; evaluation every 30 minutes.

## 0.49.5 — 2026-10-07

Incidents
- **INC-04** *Incidenti* section in the periodic evidence report; hashed PDF evidence export of a single incident (timeline, hypotheses, confirmed root cause) stored in the report archive.

## 0.49.4 — 2026-10-07

Incidents
- **INC-03** Candidate correlations (heuristic, with reason and confidence), hypotheses with evidence snapshot, root cause only by explicit operator confirmation with justification; root cause column in the incident list.

## 0.49.3 — 2026-10-07

Incidents
- **INC-01/02** Incidents per Customer with involved Devices, status lifecycle and a deterministic timeline built from recorded evidence (audit, Action Center, backups, agent jobs, vulnerability changes) plus operator notes shown separately; *Apri incidente* from every Device.

## 0.49.2 — 2026-10-07

Security
- **SEC-04** Evidence reports carry security remediation: source provenance with stale warning, findings by state, unhandled critical/high, resolutions with reason and mean time to resolve, active exceptions with justification, open critical/high table; CSV adds per-device unhandled and excepted counts.

## 0.49.1 — 2026-10-07

Security
- **SEC-01** NVD CVE API 2.0 ingestion for RouterOS: full then incremental loads, backoff, Action Center issue on repeated failures, admin page *Integrazioni → Advisory NVD* (#134).
- **SEC-02** Device ↔ advisory matching on the installed RouterOS version, explicit *non valutabili*, automatic resolve/reopen with evidence (#134).
- **SEC-03** Remediation lifecycle (planned, in progress, exception with expiry), finding page with history and firmware plan link, Action Center issue per device with unhandled critical/high CVEs (#135).

GUI
- Device and Customer shells with tabs, backup archive redesign, devices list with quick filters (#129, #130, #131).
- Single stylesheet, readable dark theme, mobile fixes (#132).
- Operational lists share quick chips and pagination; backup overview as a per-device worklist; content-hashed static assets (#133).

Operations and fixes
- Firmware plans no longer stuck or resurrected; no downgrade plans (#117, #123).
- Backup retry after a partial attempt and stale attempt reports (#119, #121).
- UISP periodic sync (#120); restore-test evidence (#122); Agent self-update and RouterBOOT stuck-state recovery (#124, #125).
- Evidence reports PDF/CSV with archive and schedules (#126, #127).

## 0.49.0

Baseline before the changes above.

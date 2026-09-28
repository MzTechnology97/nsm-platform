# Core 0.38 — Real-device QA findings

This document collects issues observed during real-device testing against the current Core 0.38 baseline.

## Test context

Primary real device used for the observations below:

- Vendor: MikroTik
- Identity: `W-AP-R-CDA_NET`
- Device name in NSM: `Carmelo`
- Model: `wAP R`
- RouterOS: `7.12.1 (stable)`
- Architecture: `mipsbe`
- Management IP: `172.31.9.16`
- RouterBOOT: `6.44.5`
- Agent shown by UI: `0.20.0-legacy`
- Transport shown by UI: `LEGACY / bodyless-v1 / headers-v1`

Important: some points below are expected legacy capability limits, while others are product/UI defects or modules that appear not implemented. Do not treat every item as an agent bug.

---

## 1. Device overview / inventory presentation

### Observed

Basic enrollment and inventory work: identity, model, RouterOS version, architecture, serial, MAC, management IP, RouterBOOT, agent state, CPU and uptime are populated.

### Issues

1. Firmware status is shown as `UNKNOWN` even though RouterOS is correctly detected as `7.12.1 (stable)`.
2. Memory values are displayed as raw bytes, e.g. values such as `24522752` / `67108864`, rather than human-readable MiB/MB and/or percentage.
3. The Security card shows no associated CVEs. This appears related to the broader Security/CVE functionality described later and must not be assumed to mean that the installed firmware has no known vulnerabilities.

### Expected

- Human-readable memory formatting.
- Firmware lifecycle/readiness classification fed by the firmware intelligence subsystem.
- Security state must distinguish `no CVEs found` from `CVE matching unavailable/not evaluated`.

---

## 2. Device Monitor tab

### Observed

- CPU current value is present.
- Uptime and heartbeat are present.
- CPU and memory history charts are empty.
- UI reports `0 campioni` and no last historical update.
- Memory used is `—`.
- Device sidebar on this tab does not show the MAC address even though Overview does.

### Issues

1. Sidebar inconsistency: MAC disappears when moving from Overview to Monitor.
2. CPU value appears without `%` formatting.
3. Memory used is not shown even though free/total memory exists in device inventory.
4. Historical charts are empty.

### Legacy capability note

The Agent page explicitly reports **Telemetria storica** as unavailable on this legacy transport. Therefore:

- empty historical charts may currently be an intentional capability gap;
- the UI should state this explicitly instead of presenting an apparently broken empty chart;
- current/instant metrics should still be formatted correctly and memory usage should be calculated if source values exist.

---

## 3. Device Configuration tab

The following areas are empty on the RouterOS 7.12.1 legacy device:

- System resources
- IP addresses
- IP routes
- Interfaces
- Firewall
- PPP sessions
- DHCP leases
- Warnings/logs

Snapshot retrieval is not available.

### Legacy capability note

The Agent page explicitly reports **Snapshot configurazione** as unavailable and indicates that it requires the modern agent / newer RouterOS path.

This should therefore be treated as a declared capability limitation for the current legacy transport, not automatically as a snapshot parser bug.

### Expected UX

When a capability is unsupported, the page should show a clear capability-specific message rather than an empty section that looks broken.

---

## 4. Device Agent tab — legacy capability visibility

On RouterOS 7.12.1 the UI currently exposes 4 of 7 capabilities.

The following are explicitly unavailable:

- Snapshot configurazione
- Backup HTTPS
- Telemetria storica

This is useful information and should remain visible. It also explains several symptoms in Monitor, Configuration and Backup.

Recommended behavior:

- unsupported features should be visibly disabled with the exact compatibility reason;
- links/actions should not appear executable if the declared transport cannot perform them;
- capability state should be used consistently across all device tabs.

---

## 5. Remote Diagnostics UX

The following operations were observed working at transport/job level:

- Ping
- Traceroute
- Neighbor discovery
- Device logs

The result presentation is difficult to read.

### Problems

- results are displayed as large/raw inline data;
- RouterOS output can appear on a single line;
- recent-results history is hard to scan;
- pending/running/success/error state is not presented as a focused execution view.

### Requested UX

Executing a diagnostic should open a modal/detail window dedicated to that execution.

It should show:

- queued / running / success / failed state;
- timestamp;
- target and parameters;
- formatted output.

Suggested formatting:

- traceroute: one row per hop;
- neighbor discovery: table with identity/address/MAC/interface/board/version where available;
- logs: timestamp/topic/severity/message rows;
- ping: compact packet/latency/loss summary plus detailed output where useful.

The existing Recent results area can remain as compact history, with click-through to the detailed execution view.

### Capability distinction

A Support Snapshot job was observed failing because the operation is not available on the RouterOS legacy transport. That is a capability issue and should not be conflated with the diagnostics result-formatting issue.

---

## 6. Device Audit tab

The device audit list works but becomes excessively long.

### Missing functionality

- Pagination
- Filter by event/activity type
- Filter by result/status
- Text search for a specific activity/event
- Time/date range filter

Examples of event families that should be filterable include diagnostic, job, snapshot and completion events.

---

## 7. Job queue and job history

The same usability problems as Device Audit apply here.

### Missing functionality

- Pagination
- Filter by job type
- Filter by state (`pending`, `delivered`, `success`, `failed`, etc.)
- Text search
- Time/date range filter

It would also be useful to distinguish clearly between:

- active/current queue;
- completed/history records.

---

## 8. MikroTik onboarding — Copy command button

During new MikroTik device onboarding, the generated adoption/provisioning command is shown, but the **Copia comando** button is defective.

### Expected

The button must copy the complete generated command exactly as displayed, without changing RouterOS quoting, semicolons, newlines, tokens or escaping.

Also add clear clipboard feedback, for example:

- `Copiato`
- explicit failure feedback if browser clipboard access fails.

Possible implementation areas to verify:

- JS event binding;
- DOM selector/value source;
- multi-line escaping;
- Clipboard API behavior in the deployment context.

---

## 9. Backup section

### Current state on RouterOS 7.12.1 legacy

Backup is not available with the current legacy agent/transport. The Agent page also declares **Backup HTTPS** unavailable.

This specific limitation is therefore expected for this device.

### Product gaps independent of legacy support

1. No obvious manual **Backup now** action for devices/transports that support backup.
2. No useful configuration-backup comparison workflow.
3. No diff view to compare two backups/snapshots of the same device.

### Requested direction

Allow selection of two revisions and display additions, removals and modifications in a readable configuration diff.

---

## 10. Operations → Monitoring

The section is present but currently shows no useful metrics during this QA pass.

### Verify

Determine whether the backend is not implemented or whether the UI is not connected to existing telemetry.

This should become the fleet-level view, distinct from a single device Monitor tab. Candidate aggregate data includes:

- online/offline state;
- heartbeat freshness;
- CPU;
- memory;
- uptime;
- active alerts;
- trends.

---

## 11. Operations → Firmware

The section appears non-functional or incomplete.

Observed related symptom: device Firmware status remains `UNKNOWN` despite a detected RouterOS version.

The section should eventually correlate at least:

- installed version;
- channel;
- current/recommended version;
- update availability;
- lifecycle/support state;
- associated vulnerability/remediation context.

---

## 12. Security → Vulnerabilities

The section appears non-functional or not yet implemented.

### Missing/expected behavior

- CVE ingestion/source pipeline;
- vendor/product/version normalization;
- correlation of device firmware to applicable CVEs;
- severity and status;
- remediation/fixed version where known;
- aggregation at fleet level;
- drill-down into a specific device.

Important: do not display `Nessuna CVE associata` as equivalent to `no known vulnerabilities` when the matching engine has not completed or is unavailable.

---

## 13. Security → EOL / EOS

The section appears non-functional or not yet implemented.

Expected lifecycle intelligence should distinguish:

- software/release support status;
- hardware/model EOL/EOS status;
- known end-of-support date where available;
- unknown/unclassified state.

This should feed firmware/security/compliance views rather than remain isolated.

---

## 14. Security → Action Center

The section appears non-functional or not yet implemented.

Expected role: turn detected security/lifecycle/compliance problems into actionable remediation work.

Candidate sources:

- CVEs;
- outdated firmware;
- EOL/EOS;
- configuration/security findings;
- backup/monitoring failures.

Candidate workflow fields:

- source finding;
- severity/priority;
- affected device(s);
- proposed remediation;
- owner/assignment;
- state;
- evidence/closure.

---

## 15. Audit → Events

This section is functional, but the register is difficult to use at scale.

### Required improvements

- Pagination
- Filtering by event type
- Filtering by status/severity where applicable
- Filtering by device/customer
- Date/time range filtering
- Text search
- Export

Export should support at least CSV; JSON is also useful. The export should honor the active filters.

---

## 16. Audit → Reports

The section appears non-functional or not yet developed.

Future reports should be filterable by period and scope and be useful as NIS2/internal-audit evidence.

Candidate dimensions:

- customer;
- site;
- device;
- event type;
- finding;
- remediation status;
- outcome.

Candidate export formats: PDF and CSV.

---

## Suggested implementation grouping

### A. UI correctness and consistency

- human-readable memory;
- CPU `%` formatting;
- MAC sidebar consistency;
- capability-aware empty states;
- Copy command clipboard bug;
- diagnostic execution modal and formatted results.

### B. Table/list scalability

Build a reusable pagination/filter/search pattern and apply it consistently to:

- Device Audit
- Job queue/history
- Audit Events

### C. Backup/config history

- capability-aware backup action;
- manual Backup now where supported;
- revision history;
- backup/config diff.

### D. Fleet Operations

- Monitoring aggregation;
- Firmware aggregation/readiness.

### E. Security & lifecycle intelligence

- CVE ingestion and matching;
- firmware assessment;
- EOL/EOS;
- Action Center remediation workflow.

### F. Audit reporting

- filtered exports;
- report generation suitable for operational and NIS2 evidence.

---

## Priority suggestion for implementation sequencing

This is not intended as a release-number commitment, only as a dependency-aware work order.

1. Fix small UI defects and capability-aware states.
2. Add reusable pagination/filter/search components to existing working registers.
3. Connect/persist telemetry needed by fleet Monitoring where supported.
4. Complete firmware intelligence and lifecycle classification.
5. Build CVE correlation on top of normalized firmware/product identity.
6. Feed Security findings into Action Center.
7. Complete backup revision/diff workflow.
8. Add Audit Reports/export workflows.

The separate RouterOS compatibility/agent architecture proposal is intentionally documented in another PR so compatibility engineering can proceed independently from the broader product backlog.

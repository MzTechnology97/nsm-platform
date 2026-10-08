# NSM Platform — Roadmap

Status: **living prioritized roadmap**  
Snapshot: **2026-09-30**

This roadmap is a queue, not a request to open every future PR at once. Runtime defects and physical acceptance gates take priority over feature expansion.

## 1. Priority policy

Select the next implementation objective in this order unless explicitly changed:

1. **functional bugs/regressions** affecting existing workflows;
2. **MikroTik Agent / RouterOS compatibility defects**;
3. **GUI defects / broken operator workflows**;
4. only then new roadmap capabilities.

Every implementation objective should be a focused, end-to-end PR with automated regression coverage. Physical RouterOS/vendor acceptance is required where simulation cannot prove compatibility.

## 2. Current active gates

These are the immediate development/acceptance items at this snapshot.

### P0 — Agent terminal completion idempotency

**Merged in `main`** (#112–#116, 2026-10-01): late or duplicate completion reports no longer mutate terminal jobs, maintenance-failed jobs are not resurrected, duplicates are acknowledged without duplicate evidence, and both modern and legacy completion paths keep authentication.

### P0 — modern MikroTik backup no-progress guard

**Superseded in `main` by Core 0.49.11 / Agent 0.49.4** (uploader rewrite with progress guard); physical acceptance on 7.24.4 tracked in issue #128.

Acceptance on RouterOS 7.24.4:

- refresh/update the modern Agent;
- execute a normal `.backup + .rsc` backup;
- confirm RouterOS file reads produce data and upload offsets advance;
- confirm an empty read or non-advancing/out-of-range `next_offset` fails closed instead of spinning indefinitely;
- confirm scheduler/Agent operation continues after failure.

### P0 — modern MikroTik temporary backup-file cleanup

**Superseded in `main` by Core 0.49.11 / Agent 0.49.4** (temporary files removed after success and failure); physical acceptance on 7.24.4 tracked in issue #128.

Acceptance on RouterOS 7.24.4:

- successful backup leaves no job-specific temporary `.backup` / `.rsc` files on RouterOS;
- forced upload failure after local file creation also removes job-specific temporary files;
- failed job is reported correctly;
- next Agent cycle remains healthy.

### P0 — RouterOS 7.12.x structured configuration parity

**Merged in `main` (Core 0.49.10, Agent 0.49.3)** with bounded collection; physical validation on 7.12.1 is tracked in issue #128.

Validate on physical RouterOS 7.12.1:

1. resources;
2. interfaces;
3. IP addresses;
4. routes;
5. firewall filter / NAT;
6. DHCP leases;
7. PPP/tunnel sections where present;
8. warning/error/critical logs;
9. `Aggiorna tutto` batch behavior.

The implementation uses a fixed allow-listed `rows-v1` plain-text transport and fixed read-only RouterOS collection commands. It does not introduce arbitrary command execution.

### P0 — RouterOS 7.12.x historical telemetry acceptance

Legacy telemetry is implemented in `main`, but physical post-deploy history validation is still required.

Acceptance:

- several normal heartbeats populate telemetry history;
- CPU/memory/uptime/current state render correctly;
- no modern-only serialization assumption leaks into the legacy transport;
- stale/online state remains coherent.

### P0 — RouterOS 7.20.7 modern-family revalidation

A prior generated-source compatibility issue has been corrected, but this point release still needs a clean real-device regression pass.

Validate independently:

- clean enrollment;
- heartbeat;
- one normal generic job completion;
- configuration snapshot;
- diagnostics;
- firmware readiness;
- backup generation/upload/archive.

Do not infer support for one operation merely because another operation passes.

## 3. MikroTik hardening and parity

After the current P0 gates are closed, continue MikroTik work in focused PRs.

### MTK-01 — legacy snapshot payload chunking/pagination, only if needed

Trigger this PR only if normal-size physical RouterOS 7.12.x snapshot acceptance shows practical payload limits for real firewall/DHCP sections.

Scope:

- bounded chunk/page protocol for allow-listed snapshot data;
- deterministic ordering/reassembly;
- explicit incomplete/oversize failure state;
- no arbitrary source/command payloads;
- regression tests for split/reassembly/idempotency.

### MTK-02 — legacy backup transport design and implementation

**Done**: `.rsc` export up to 60 KB on 7.12 (Agent 0.49.7) and full `.backup` + `.rsc` over the FTP receiver on RouterOS 6.48/6.49 and 7.12 (Core 0.49.75, Agent 0.49.16). Physical acceptance tracked in issue #128.

Suggested vertical split:

1. verify physical legacy file-generation/read primitives and safe chunk size;
2. implement capability-gated legacy backup job transport;
3. reuse server artifact/session verification contracts where compatible;
4. add physical `.backup + .rsc` acceptance;
5. expose capability in GUI only after end-to-end acceptance.

Do not present legacy backup as available before this is complete.

### MTK-03 — restore-test evidence workflow

Goal: prove that stored artifacts remain operationally useful without unsafe automatic production restore.

**Implemented in `main`**, including report integration (last restore test per device in the backup report).

Original scope:

- restore-test record/model;
- operator workflow for controlled validation;
- artifact/hash/version references;
- result/evidence fields;
- audit event;
- report integration later.

### MTK-04 — firmware intelligence

**Implemented in `main`**: steps 1–4 (`app/routeros_catalog.py`: channel catalog from upgrade.mikrotik.com, security classification from release notes and open CVEs, device evaluation) and step 5 (`app/firmware_suggestions.py`: safe plan suggestions with the next step per Device, bulk readiness checks and plan creation that still require the pre-upgrade backup and approval).

Build the missing intelligence layer around the already implemented execution workflow.

Focused steps:

1. version/channel catalog model and refresh job;
2. recommended/target version calculation separated from observed version;
3. security/bugfix classification evidence;
4. Device worklist correlation;
5. only then automate safe upgrade-plan suggestions.

### MTK-05 — richer diagnostics

Improve existing bounded diagnostics rather than creating a generic remote shell.

Possible focused PRs:

- richer ping/traceroute presentation and history — **done** (`app/mikrotik_diagnostic_views.py`);
- structured neighbor/DHCP/log result presentation — **done**, for modern and legacy agents;
- bounded additional read-only diagnostics with explicit capability gates;
- support snapshot on legacy agents — **done** in Core 0.49.82 (assembled from the read-only section jobs);
- support-snapshot evidence/archive improvements.

## 4. GUI quality and regression queue

The contextual browser-feedback program is mostly implemented. Keep the GUI tracker focused on reproducible defects rather than broad rewrites.

When a new GUI bug is found:

1. reproduce it with a focused smoke test;
2. keep the operator inside a canonical NSM page;
3. preserve machine API JSON/HTTP contracts;
4. add one-shot contextual feedback;
5. verify route precedence explicitly where FastAPI has overlapping routes.

Additional quality work can proceed as concrete defects emerge:

- navigation consistency;
- empty/unsupported/no-data state clarity;
- mobile/responsive regressions;
- table/filter/pagination usability;
- richer diagnostic/configuration result presentation.

## 5. Ubiquiti / UISP roadmap

Current runtime integration is read-only and uses UISP Network Device data for association/manual refresh. NSM Customer/Site ownership remains authoritative.

### UBNT-01 — periodic UISP synchronization

**Implemented in `main` — real UISP instance validation pending.** Remaining step: confirm a scheduled cycle against a production-like UISP console.

Original scope:

- scheduled refresh of associated Devices;
- retry/backoff;
- missing external Device handling;
- connector health state;
- no automatic Customer/Site reassignment;
- audit significant observed changes;
- tests with synthetic connector responses.

### UBNT-02 — monitoring/history normalization

**Implemented in `main`** (`app/uisp_metrics.py`): overview metrics normalized, sampled at sync, shown on the UISP tab and in the customer device list.

- map supported UISP operational metrics into normalized NSM state;
- persist history only for meaningful supported fields;
- explicit unavailable/unsupported states;
- Customer/Device monitoring UI integration.

### UBNT-03 — bulk onboarding/association

**Implemented in `main`** (`app/uisp_onboarding.py`): UISP list with NSM state, explicit Customer/Site, preview, re-read at confirmation.

- bounded discovery list;
- duplicate/conflict handling;
- explicit NSM Customer/Site selection;
- preview before commit;
- stable external ID storage;
- audit.

### UBNT-04 — backup/snapshot capability

Implement only if the actually consumed UISP/vendor contract exposes a safe, verifiable configuration artifact/snapshot mechanism for the target Device family.

Do not fake coverage based only on a policy row.

### UBNT-05 — diagnostics

Implement only diagnostics actually exposed and validated by the integration contract. Normalize results into NSM rather than exposing opaque raw payloads as the primary UI.

### UBNT-06 — firmware workflow

**Step 1 implemented in `main`** (`app/uisp_firmware.py`): installed/latest version and compatibility read from UISP at every sync, firmware state on the Device and in the firmware worklist. Field names to be confirmed on a real UISP instance (`/nms/api-docs/`). Planning and execution remain open: upgrades are run from UISP.

- read current/available state where supported;
- planning/approval model;
- capability and model/version checks;
- controlled execution only when the real integration supports it;
- post-action verification;
- audit/evidence.

### UBNT-07 — security and lifecycle correlation

- normalize Ubiquiti model/version identity;
- correlate with the future advisory/lifecycle ingestion framework;
- never infer vulnerability from brand alone.

### UBNT-08 — full UISP device management (requested 2026-10-07)

**Step 1 done (Core 0.49.49)**: UISP history charts (signal, capacity, CPU/RAM, stations) on the device UISP tab.
**Step 2 done (Core 0.49.68)**: reboot with uptime verification, configuration backup archived in NSM (manual and by policy), interfaces and UISP events on the device UISP tab. Endpoint field names to be confirmed on a real console. Remaining: firmware upgrade execution through UISP.

Bring Ubiquiti devices managed through the UISP API to the depth of the MikroTik agent, for every operation the UISP API actually exposes and that can be verified on a real instance:

- configuration backup (download and archive in NSM, with evidence);
- reboot with confirmation, acknowledgement and post-reboot verification;
- firmware upgrade (planning, approval, execution through UISP, verification) on top of the UBNT-06 state;
- complete telemetry: signal/chain levels, noise, CCQ/airMAX quality, capacity, LAN/Ethernet interface state and speed, traffic, history;
- device logs.

### MON-01 — telemetry graphs and WAN/PPPoE traffic

**Step 1 implemented in `main` (Core 0.49.43, Agent 0.49.9)**: MikroTik interface traffic graphs on the Monitor page. Since Core 0.49.65 every graph (CPU, memory, traffic, UISP) uses the same Cacti/Zabbix-style renderer.
- Bit/s in and out from the agent byte counters, sampled every 2 minutes since Core 0.49.78 (Agent 0.49.18); 2-minute points for 7 days, then 10-minute averages, kept 90 days.
- WAN interfaces (pppoe-out, LTE, tunnel clients, else ether1) are monitored by default; operators can choose up to 8 per device.
- Statistics per range: current, average, maximum, 95th percentile and volume.

Next steps:
- ~~interface errors and drops~~ — done in Core 0.49.83 (Agent 0.49.20, *Errori e drop* chart);
- ~~latency series~~ — done in Core 0.49.85 (ICMP latency and loss from NSM for any vendor, unreachable alert);
- ~~signal series (MikroTik wireless)~~ — done in Core 0.49.86 (Agent 0.49.21, *Segnale wireless* panel);
- the same graphs for UISP devices (from `uisp_metric_samples`) and, later, cnMaestro;
- ~~graphs aggregated per customer and per site~~ — done in Core 0.49.72 (customer *Grafici* tab with site filter).

### LOG-01 — integrated syslog server and live device logs (requested 2026-10-07)

**Identification hardening done (Core 0.49.53, Agent 0.49.13)**, decided with the user on 2026-10-08: per-device key with strict mode, allowed networks, certain-or-discard attribution, fresh heartbeat addresses, capped memory and quotas.
**Server hardening done (Core 0.49.54)**: read-only container with a dedicated DB role, scrubbed secrets, `DOCKER-USER` firewall rules, optional TLS on 6514, hash chain anchored daily in the audit log.

**Step 1 done (Core 0.49.45).**
- Docker service `syslog` receives on UDP/TCP 514 and matches messages to devices by sender IP; shared NAT addresses are resolved by hostname.
- Unknown senders are listed for the admin.
- Every device has a **Syslog** tab: live log, filters, CSV export, and the configuration for its own vendor inline.
- The admin page *Amministrazione → Syslog* covers settings, receiver status and unknown senders.

**Step 3 done (Core 0.49.46).** Access events are recognised in the syslog for each vendor and raise brute-force, success-after-failures and new-public-address alerts and notifications. Each device gets an access panel and the fleet gets the *Security → Accessi* page.

**Step 2 done (Core 0.49.47, Agent 0.49.11).** The modern MikroTik agent configures remote syslog (action `nsm`: critical, error, warning, account), on demand, for all devices, or automatically.

Next steps:
- remote syslog setup for other vendors through their APIs (UISP/airOS, cnMaestro);
- ~~legacy agent support~~ — done in 0.49.67 (Agent 0.49.15, legacy-ops-v1 profile);
- ~~incidents and reports built from the access events~~ — done in 0.49.50 (report section, incident evidence, incident from alert).


**Receiver.** A syslog receiver integrated in the stack, as a Docker service (UDP/TCP 514, optionally TLS 6514).
- Messages are matched to devices only by the per-device key `NSM-<16 hex>` (logging prefix on MikroTik, origin-id on Cisco, device name elsewhere); lines without a key are always discarded (Core 0.49.74, operator decision).
- Logs are stored with retention and indexing.

**Device configuration.** Configured automatically:
- by the MikroTik agent, through a remote `/system logging action` plus rules for `error`, `warning`, `critical` and `account`;
- on other vendors that support a remote syslog server: UISP/airOS, Cambium, TP-Link Omada, Huawei, Fortinet and others, through their API or with guided instructions.

**Live log page.** Each device gets a log section updated in real time, with filters by severity, topic and text.

**Authentication alerts (requested 2026-10-07).** Where the log allows it, detect failed logins (wrong user or password), brute-force bursts and successful logins from unusual or public addresses, then raise alerts per device, user and source IP. Examples: RouterOS `login failure for user … from … via …`, SSH/Telnet/web failures on other vendors.

**Security.** Logs are kept to track anomalous external access, such as failed or successful logins from public IPs and new admin sessions. They feed:
- rules that raise incidents and evidence;
- notifications;
- reports.

### MON-02 — keep the last telemetry of offline devices (requested 2026-10-07)

Telemetry of a device that goes offline must never be deleted: the last received values stay visible, with their timestamp. Retention cleanup always keeps the newest sample of each device and interface.
- **Done in 0.49.43** for MikroTik metrics, interface traffic and UISP metrics.
- **Still to do:** other connectors when they get history.

### INV-07 — MikroTik devices without IP in the device list (bug)

**Fixed in `main` (Core 0.49.44, Agent 0.49.10).**
- The agent reports the RouterOS addresses.
- The management IP is chosen in this order: the public router address, else the address NSM sees the heartbeat from, else the first address. A value typed by an operator is never overwritten.
- Lists show the management IP with its scope and the LAN IP. The device page also shows the address NSM sees.

### GUI-02 — visual refresh: Material icons and ISP/WISP background (requested 2026-10-07)

**Implemented in `main` (Core 0.49.48)**: outlined Material-style icon set drawn for NSM (sprite `static/ui-icons.svg`), sidebar and topbar refresh, ISP/WISP line-art background (`static/bg-network.svg`). Next: icons in page headers and empty states.

- Replace the sidebar menu icons and the top notification icon with a consistent Material Design (or similar) icon set, self-hosted and CSP-safe.
- Make the GUI more attractive.
- Optionally add a subtle background suited to the ISP/WISP domain (network, antennas, fibre motifs) that never hurts readability or contrast, in light and dark theme.

### SCAN-01 — WAN exposure scanner for every vendor (requested 2026-10-07)

**Decided with the user (2026-10-08).**
- Run when a device is added, with a manual button in the device section.
- Only devices with a public or NATted IP; the operator gives the IP when the WAN/PPPoE address cannot be determined.
- On MikroTik the agent checks the router's own configuration.

**Step 1 done (Core 0.49.52, Agent 0.49.12)**: the MikroTik configuration-based check, with an *Esposizione* tab on every device and alerts.
**Step 2 done (Core 0.49.60)**: the check from the NSM server on the public IP for the other manufacturers (and MikroTik without the modern agent), with the IP given by the operator when it cannot be determined.
**Since Core 0.49.73** the operator can also target a private/CGNAT address (device reached directly, e.g. via VPN), with an explicit warning that the result is distorted when the device sits behind another router or NAT.
**Step 3 done (Core 0.49.61)**: the fleet view *Security → Esposizione*, exposed services in the security newsletter and in the evidence report (section 3).

A port/service scanner run from the NSM server against the WAN address of each managed device.
- **Targets.** Any vendor, not only MikroTik. The target is the detected WAN/PPPoE address, and the operator can pick or override the correct WAN IP when it cannot be determined.
- **Checks.** Critical services exposed on the WAN:
  - Telnet 23, SSH 22, FTP 21, HTTP/HTTPS admin 80/443/8080/8443;
  - DNS 53 (open resolver), SNMP 161/udp, TR-069 7547, UPnP 1900;
  - vendor-specific ports chosen by vendor and OS: RouterOS Winbox 8291, API 8728, API-SSL 8729, bandwidth-test 2000; UniFi/UISP 8443/2055; Huawei/ZTE ONT web and telnet; and so on.
- **Results.** Devices at risk are flagged like CVE exposure: per-device findings, a fleet "exposed services" view, severity, and inclusion in the vulnerability digest and reports.
- **Safety.** Scheduled and on-demand scans, only toward addresses of managed devices, with rate limiting and an audit trail.

### ZBX-01 — Zabbix API integration (requested 2026-10-07)

**Decided with the user (2026-10-08): NSM pushes its devices to Zabbix. Implemented in `main` (Core 0.49.51).** Hosts, groups per customer, templates per manufacturer, tags and inventory; 6.x and 7.x authentication; hosts disabled (not deleted) when devices leave NSM. Zabbix problems are shown inside NSM since Core 0.49.62 (device header and overview, Monitoring).

Integration with the operator's Zabbix (currently 6.x; support both the 6.x API and 7.x, where authentication moved to an API token in the `Authorization` header). How it applies to devices (import hosts/items, link NSM devices to Zabbix hosts, show problems and graphs, or push NSM devices to Zabbix) is to be decided with the user before implementation.

### DUDE-01 — MikroTik The Dude integration (requested 2026-10-07)

**Paused (2026-10-08)**: the user is evaluating whether it is worth adding.

Integration with MikroTik The Dude through the RouterOS API of the Dude server (`/dude` menu: devices, probes, services, notifications, maps). Which features are worth it is to be evaluated with the user. Candidates:
- import or reconcile the Dude device list (name, address, type) with the NSM inventory;
- use Dude up/down and service state as an extra monitoring source on the device page and in alerts, without duplicating polling;
- correlate Dude outages with NSM events (agent heartbeat, firmware jobs, backups);
- link from the NSM device to the Dude map element.

### GUI-SEARCH — global search bar

**Implemented in `main`**: compact grouped suggestions with manufacturer icons, device status, matched field, CVE and page shortcuts, counts with *vedi tutti*, recent searches, `/` and Ctrl+K shortcuts; manufacturer icons replace the two-letter vendor badges.

## 6. ACS / TR-069 CPE roadmap

The selected ACS is **GenieACS** (as preferred in `PRODUCT_REQUIREMENTS.md`); NSM reads its NBI and never implements an ACS itself. GenieACS ships as an optional service set of the NSM Docker stack (`./manage.sh acs-enable`). ACS-01..03 are implemented in `main` (`app/genieacs_connector.py`); validation against a real GenieACS instance is pending.

### ACS-01 — connector abstraction and first production connector

**Implemented in `main`** (GenieACS NBI, read-only).

- encrypted connector credentials;
- connectivity test;
- bounded Device query contract;
- connector health/error model;
- public-safe fixtures.

### ACS-02 — Device discovery and association

**Implemented in `main`** for per-Device association (serial, then MAC) of TR-069 CPEs of any brand (TP-Link, or Huawei/ZTE/others entered as *Altro produttore* with type router, ONT, wireless CPE or other — Core 0.49.66); bulk onboarding remains open.

- match candidates using reliable ACS identifiers;
- preview before association;
- retain external Device ID;
- preserve NSM Customer/Site authority;
- duplicate/conflict controls.

### ACS-03 — inventory normalization

**Implemented in `main`** for identity, model, firmware, hardware, IP and last Inform (TR-098 and TR-181 paths).

Normalize relevant Device identity and firmware fields from supported data models.

### ACS-04 — parameter profiles

- TR-098 / TR-181 mapping profiles;
- vendor/model-specific profiles only where required;
- explicit unsupported fields;
- tests against sanitized representative structures.

### ACS-05 — monitoring/history

Persist only supported, stable operational data with capability-aware unavailable states.

### ACS-06 — diagnostics

Add capability-gated standardized diagnostics where the ACS/CPE supports them.

### ACS-07 — backup/restore capability

Only expose backup/restore where the real CPE data model and connector expose a verifiable supported mechanism.

### ACS-08 — firmware workflow

- firmware target/evidence model;
- controlled ACS operation;
- progress/result state;
- post-upgrade verification;
- audit/evidence.

### VEND-01 — firmware catalogs for other vendors (requested 2026-10-07)

**Implemented in `main` (Core 0.49.69)**: *Firmware → Cataloghi altri produttori* — Ubiquiti from UISP and from the official update service (`fw-update.ubnt.com`, airOS platforms read from the installed firmware string; to be confirmed from the deployed instance), other brands from releases entered from the official download pages; device firmware state evaluated per model. Next: official machine-readable sources for other brands where they exist.

Release catalogs like the RouterOS one (versions per channel/model, release notes, security classification) for Ubiquiti, TP-Link, Tenda, Cambium and similar vendors. Each catalog is enabled only when the inventory contains devices of that brand, reads only official vendor sources and never guesses versions it cannot verify.

### VEND-02 — cnMaestro integration for Cambium (requested 2026-10-07)

**Step 1 implemented in `main` (Core 0.49.70)**: OAuth2 client credentials, paged inventory and statistics, linking by MAC/serial, identity/firmware/IP/status sync every 15 minutes, metrics history (signal, SNR, throughput, CPU/memory, stations) with Cacti-style graphs on the device *cnMaestro* tab. Field names to be confirmed on a real cnMaestro. Next: operations where the cnMaestro API supports them.

Read-only cnMaestro (cloud/on-premises) connector for Cambium radios: discovery and association like UISP, periodic sync, full monitoring and statistics (signal, link quality, capacity, interfaces, uptime, history) in the style of the MikroTik and UISP views; later operations only where the cnMaestro API supports them.

## 7. Cross-vendor vulnerability management

### SEC-05 — multi-vendor CVE correlation

**Implemented in `main`** (`app/vendor_cpe.py`): about 30 common manufacturers (Ubiquiti, TP-Link, Cambium, Mimosa, Tenda, Huawei, ZTE, Teltonika, Ruckus, Fortinet, Juniper, Cisco…), NVD queries only for the brands and models in the inventory, generic firmware version comparison, coverage per manufacturer on the NVD page. Exposure is never inferred from the brand alone. Open: vendor-specific version schemes (e.g. Huawei VxRxxxCxx) and Cisco OS families.

### SEC-01 — source ingestion framework

**Implemented in `main` — live NVD validation pending.** First adapter: NVD CVE API 2.0 for `cpe:2.3:o:mikrotik:routeros` (`app/advisory_sources.py`, admin page *Integrazioni → Advisory NVD*). Remaining step: one full load and one incremental cycle against the real API from the deployed instance.

- normalized advisory model;
- source identity/fetch timestamp/evidence;
- idempotent refresh;
- parser failure visibility;
- source-specific adapters kept separate from matching logic.

External sources should be documented only after an adapter actually uses them.

### SEC-02 — Device ↔ advisory matching

**Implemented in `main` for MikroTik RouterOS** (`app/advisory_matching.py`). Other vendors are not evaluated until their product/version mapping exists.

- vendor/product/model normalization;
- version range evaluation;
- confidence/evidence fields;
- explicit unknown/not-applicable states;
- no brand-only matching.

### SEC-03 — remediation lifecycle

**Implemented in `main`** (`app/vulnerability_remediation.py`): open / planned / in_progress / exception (with expiry) / resolved, history of every change, firmware plan link, one Action Center issue per Device with unhandled critical/high findings.

- OPEN / PLANNED / IN_PROGRESS / RESOLVED / EXCEPTION-style lifecycle;
- operator notes/evidence;
- firmware/remediation link;
- Action Center integration;
- resolved history retained.

### SEC-04 — security reporting integration

**Implemented in `main`.** Evidence reports include advisory source provenance (stale warning), findings by remediation state, unhandled critical/high, resolutions in the period with reason and mean time to resolve, active exceptions with justification, open critical/high table and not-evaluable devices; CSV adds per-device unhandled and excepted counts.

## 8. Lifecycle / EOL / EOS roadmap

### LIFE-01 — lifecycle source ingestion

**Implemented in `main`** (`app/lifecycle_catalog.py`): per-model catalog with separate EOL/EOS dates, mandatory source and verification date, CSV import/export.

- normalized vendor/model lifecycle record;
- EOL and EOS/support date separation;
- source/evidence timestamp;
- explicit unknown state.

### LIFE-02 — Device correlation

**Implemented in `main`**: exact model/alias correlation, ambiguous and unmatched states never applied, manual values preserved, *Senza dato lifecycle* and *In scadenza* worklists.

- reliable model correlation;
- unsupported/ambiguous state;
- Customer/device worklists;
- avoid silent false matches.

### LIFE-03 — remediation/replacement evidence

**Implemented in `main`** (`app/lifecycle_remediation.py`): remediation record per EOL/EOS Device, Action Center issue, history and report integration.

- Action Center finding;
- acknowledgement/remediation plan;
- replacement/exception evidence;
- report integration.

## 9. Incident Timeline / Root Cause

Approved future customer-edge feature. It must distinguish observed facts from correlation/hypothesis and from operator-confirmed root cause.

Split into focused PRs:

### INC-01 — incident model and timeline aggregation

**Implemented in `main`** (`app/incident_models.py`, `app/incident_timeline.py`).

- Incident entity/lifecycle;
- links to Customer/Device/Site;
- normalized timeline event references;
- deterministic chronology.

### INC-02 — incident UI and evidence

**Implemented in `main`** (`/incidents`): list with quick filters, creation from a Device or Customer, detail with timeline, operator notes and actions, status lifecycle, involved Devices. Notification association is not included.

- list/detail workflow;
- evidence links;
- operator notes;
- relevant Audit/Action/Notification associations.

### INC-03 — correlation and Root Cause workflow

**Implemented in `main`** (`app/incident_correlation.py`): heuristic candidates with reason and confidence, operator or suggested hypotheses with evidence snapshot, explicit confirmation with justification; only a confirmed hypothesis is the root cause.

- candidate correlations/hypotheses;
- explicit confidence/source;
- operator confirmation required for final root cause;
- never convert heuristic correlation directly into fact.

### INC-04 — export/report integration

**Implemented in `main`**: periodic reports have an *Incidenti* section; a single incident can be exported as a hashed PDF evidence stored in the report archive.

- incident evidence summary;
- report archive linkage;
- hash/audit evidence.

This feature remains customer/customer-edge focused, not ISP backbone topology RCA.

## 10. Compliance Baseline

Approved future evidence/control feature.

### COMP-01 — baseline model and inheritance

**Implemented in `main`** (`app/compliance_models.py`, `app/compliance_engine.py`).

- control definitions;
- global/vendor/customer/site/device applicability where appropriate;
- versioned baseline/effective baseline.

### COMP-02 — capability-aware evaluation

**Implemented in `main`**: eight controls, results pass / fail / unknown / not applicable, worker every 30 minutes.

Every control result must distinguish at least:

- pass;
- fail;
- unknown/no evidence;
- unsupported/not applicable.

Unsupported vendor capability must not become a false failure.

### COMP-03 — findings and remediation lifecycle

**Implemented in `main`** (`app/compliance_findings.py`): acknowledgement, exceptions with expiry, history, Action Center issue per Device.

- finding generation;
- assignment/acknowledgement;
- remediation evidence;
- exception lifecycle;
- Action Center link.

### COMP-04 — UI and report integration

**Implemented in `main`**: *Compliance* tab on every Device, summary in the Customer *Sicurezza* tab, report section *8. Compliance* and CSV columns.

- Customer/device compliance view;
- evidence drill-down;
- report-ready normalized results.

NSM may support NIS2-oriented operational evidence; it must not claim that installing the software alone establishes legal compliance.

## 11. Executive / NIS2-oriented reporting

Approved future reporting feature.

### REP-01 — report model, archive and hash evidence

**Implemented in `main`.**

- report metadata;
- scope/date range;
- immutable archived output reference;
- hash;
- generation audit event.

### REP-02 — manual PDF/CSV report generation

**Implemented in `main`** (incident/compliance sections will be added when those features exist).

- Customer/all/selected scope;
- inventory;
- firmware;
- vulnerabilities;
- lifecycle;
- backup evidence;
- incidents/compliance when available;
- explicit unavailable-data sections rather than fabricated completeness.

### REP-03 — scheduling engine

**Implemented in `main`.**

- monthly/quarterly/annual/custom recurring schedules;
- idempotent generation;
- retry/failure evidence;
- scheduler observability.

### REP-04 — delivery and delivery evidence

**Implemented in `main`**: e-mail, Telegram and Slack channels with per-user level/category preferences, outbox with retries and delivery log; scheduled reports delivered with attachment; vulnerability newsletter; worker error alerts (`app/notification_delivery.py`, `app/notification_chat.py`, `app/notification_digest.py`). External (non-user) e-mail recipients per schedule and delivery evidence in the audit log since Core 0.49.63.

Add delivery channels only when an actual supported implementation is selected. Record delivery attempt/result independently from report generation success.

## 12. RBAC and administration hardening

Focused future work:

- role/permission matrix review across all current routes — **done**: `rbac_routes_smoke.py` walks every registered route (anonymous access refused everywhere, read-only auditor writes refused except an explained allow-list);
- scoped/delegated administration — **done (Core 0.49.71)**: non-admin users limited to selected customers, enforced centrally on every ORM query of the request (customers, rows with `customer_id`, rows of their devices); API keys were already customer-scoped;
- credential rotation workflows — encryption master-key rotation **done** (`ENCRYPTION_PREVIOUS_KEYS`, `manage.sh rotate-secrets`, *Sistema* inventory); API keys rotate with a grace period; MikroTik agent tokens are rotated by reinstalling the agent;
- session/security policy hardening — **done**: failed-login throttling per username+address and per address, failed-login audit, idle timeout, sessions invalidated on password change and on demand;
- API-key lifecycle and audit review — **done**: rotation with grace period, usage evidence (count, last IP), review states (expired, expiring, unused, no expiry);
- least-privilege deployment documentation — **done** (`docs/OPERATIONS.md`).

## 13. Production readiness / DR

Focused future objectives:

- documented restore procedure for PostgreSQL logical backups — **done** (`docs/OPERATIONS.md`, `manage.sh restore-db`);
- backup-artifact storage recovery procedure — **done** (`docs/OPERATIONS.md`);
- periodic restore drill evidence — **done** (`manage.sh restore-drill`, *Amministrazione → Sistema*);
- upgrade rollback/runbook refinement — first runbook in `docs/OPERATIONS.md`;
- health/worker/connector observability — **done**: worker heartbeat, per-task outcome and isolation; connector and data-source health (UISP, NVD, GenieACS, RouterOS catalog, MikroTik agents) on *Sistema*;
- capacity limits and retention sizing — **done**: *Sistema* shows largest tables, backup archive growth and days to full; retention documented in `docs/OPERATIONS.md`;
- release/versioning discipline — **done**: rules in `DEVELOPMENT_WORKFLOW.md`, enforced by `release_discipline_smoke.py`;
- deployment hardening review — **done**: findings and per-installation actions in `docs/OPERATIONS.md`, privileges dropped for app containers, extra Caddy headers, regression guard test.

## 14. Documentation and screenshot roadmap

Documentation must be updated with runtime changes, not left as a separate end-of-project task.

When a sanitized demo UI is available, add public-safe screenshots under `docs/images/` for the views listed in `docs/README.md`. Never capture a real customer/deployment simply to improve the README.

## 15. Roadmap completion rule

A roadmap item leaves this document only when its focused implementation has:

- actual runtime behavior;
- explicit permissions/capability checks;
- usable UI when user-facing;
- audit/evidence where material;
- automated tests;
- required physical/vendor acceptance;
- green CI;
- an update to `IMPLEMENTED_CAPABILITIES.md`.

Until then, keep the state explicit: planned, active, blocked or validation pending.

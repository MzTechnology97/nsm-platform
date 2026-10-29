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

**Not needed for now (evaluated 2026-10-23, Core 0.49.268)**: the production legacy fleet is one RouterOS 7.12 router (Agent 0.49.23-legacy). Its latest successful section reads are far below the 56 KB rows-v1 wire limit (largest: routes and logs about 2 KB of data each); no section other than `logs` was ever truncated, and `logs` is truncated by design (last 20 of the warning/error entries). The only failures were expired jobs and the `services` section, which the legacy Agent cannot read (now refused explicitly by the MTK-05 capability gate). Re-evaluate when a legacy router with large firewall/DHCP tables is enrolled: the job result keeps `total`, `limit` and `truncated` for that check.

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

**Implemented in `main`**, including report integration (last restore test per device in the backup report) and, from Core 0.49.273, the fleet *restore readiness* worklist (recent complete backup + recent passed restore test per MikroTik).

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
- bounded additional read-only diagnostics with explicit capability gates — **done** in Core 0.49.268 (Agent 0.49.24): queues, certificates and BGP/OSPF sessions for modern and legacy agents, per-section capability gate (`app/mikrotik_snapshot_capabilities.py`);
- support snapshot on legacy agents — **done** in Core 0.49.82 (assembled from the read-only section jobs);
- support-snapshot evidence: JSON download with audited SHA-256 — **done** in Core 0.49.88.

### ACCESS-01 — global management list, SNMP and NTP on every device (requested 2026-10-23)

**Decided with the user (2026-10-23):**
- a single global list, *Reti di gestione autorizzate* (Amministrazione), prefilled with the NSM/Zabbix server, valid for **every** device: MikroTik, Ubiquiti CPEs, TP-Link, Cambium and the others;
- SNMP with the `public` community, read-only, answering only those addresses;
- SSH accepted only from those addresses, on every device;
- NTP client towards the Italian national time servers (INRIM `ntp1.inrim.it`, `ntp2.inrim.it`), clock `Europe/Rome` with automatic daylight saving time;
- applied automatically, with per-device exclusion; clock and synchronization state shown on the device.

Per vendor:
- **MikroTik (Agent)**: Core 0.49.287 / Agent 0.49.27:
  - `/snmp community` `public` limited to the list, `/snmp enabled=yes`;
  - `/system ntp client` with the INRIM servers;
  - `/system clock time-zone-name=Europe/Rome`;
  - SSH: *available-from* of `/ip service ssh` (not a firewall rule, so the operator's firewall order is never touched).
- **Ubiquiti airOS (UISP)**: UISP does not set SNMP, ACL or firewall rules today. NSM can do it like the syslog (UISP backup → edited `system.cfg` → apply, which reboots the CPE):
  - `snmp.status`, `snmp.community` and `snmp.location` exist in the archived configurations;
  - airOS has no address list for SNMP or SSH (`sshd.*` has only port and status), so the ACL needs firewall rules (`iptables.sys.fw.*`: accept SNMP, SSH and the management ports from the list, drop them from the others on the WAN);
  - **chosen approach (user, 2026-10-23)**: enable the airOS firewall and create ACL rules for the important services: SSH (TCP 22 or the configured `sshd.port`), discovery (UDP 10001) and SNMP (UDP 161). Each is accepted only from the list and dropped from other sources; the web ports follow the same pattern if requested;
  - the NTP keys (`ntpclient.*`) and the timezone keys need checking;
  - rule keys and ordering must be validated on a test CPE first, because a wrong rule can lock the CPE out of management.
- **TP-Link and other CPEs (TR-069)**:
  - NTP and timezone through `Device.Time.*` (`NTPServer1..`, `LocalTimeZone`);
  - SNMP and ACL only where the data model of the model exposes them; ACL writes were rejected (9003) on the VX230v, so this is per model.
- **Cambium (cnMaestro)**: configuration through cnMaestro (templates/API), to be verified on the operator's console.

**Exposure link (requested 2026-10-23):**
- When the exposure check (SCAN-01) finds critical services open to everyone, the device page and the exposure list offer **Applica regole ACL ai servizi critici esposti**. The button applies the management list to the exposed services only, through the same channel as above, with a confirmation and, for airOS, the reboot warning.
- A service limited by the ACL is **not** critical. On MikroTik the configuration check already marks it *limitato*. The external scan runs from the NSM server, which is in the list, and would still see the port open: when NSM knows the ACL is applied (last successful configuration with the current list), the finding becomes *limitato* instead of *esposto*.

Snapshot of the operator's network (read-only, 2026-10-23, 282 of 644 devices checked):
- Ubiquiti CPEs: SSH open to Internet on 265 of 268, discovery UDP 10001 on 262 (amplification), web on 2 and SNMP on 1.
- MikroTik: Winbox exposed on 2 routers, SNMP on 1 router plus 1 external result, bandwidth-test on 3. One legacy router seen externally with FTP and Telnet (critical) and API/web open, to be confirmed: an external scan of a shared IP may reach another device.
- TP-Link: TR-069 CWMP 7547 open to Internet on 2.
- Cambium: SSH on 1 CPE.

**ACL capacity (to evaluate, requested 2026-10-23):** measure, for each vendor and model, how many entries each ACL accepts before NSM relies on it, and record the limit in the vendor capabilities:
- MikroTik: `/snmp community addresses`, `/ip service available-from`, firewall address lists;
- airOS: firewall rules (`iptables.sys.fw.N`);
- TP-Link and other TR-069 CPEs: ACL tables of the data model (on the VX230v only a Ping row exists and writes were rejected);
- Cambium: management access lists.

When the global list is longer than a device accepts, NSM merges contiguous networks into larger prefixes where possible. Otherwise it does not apply a partial list and warns on the device, which avoids locking out a management address.

### ACCESS-02 — UPnP check and turn-off on every device (requested 2026-10-23)

UPnP lets any device on the customer LAN open ports on the router towards Internet, and on some firmwares it answers from the WAN too (SSDP 1900, amplification). It is enabled in about a third of the archived airOS configurations (`upnpd.status=enabled`, 315 of 928 backup files, often bound to `ppp+`).
- **Detection, every vendor**:
  - MikroTik: `/ip upnp` enabled and its interfaces, from the Agent snapshot;
  - Ubiquiti airOS: `upnpd.status` and `upnpd.devname` from the UISP configuration backups;
  - TP-Link and other TR-069 CPEs: `Device.UPnP.Device.Enable` or the vendor flags (`X_TP_UPnPCfg.Enable`), already read hourly by the NSM provision;
  - Cambium: from cnMaestro, to be verified;
  - plus the SSDP answer from outside (external check, external probe).

  Each device shows *UPnP attivo (solo LAN / anche verso WAN)*. The exposure page and the fleet list filter on it.
- **Turn it off**:
  - a *Disattiva UPnP* button on the device, and a **bulk** action from the list with a preview of the devices;
  - per vendor: MikroTik through the Agent (`/ip upnp set enabled=no`), TR-069 through a parameter write, airOS through the UISP configuration edit (which reboots the CPE, said in the confirmation), Cambium through cnMaestro;
  - every action is audited and reversible (*Riattiva*);
  - the bulk action runs in waves, and stops at the first error.
- **Suggestion, not imposed**: some customers need UPnP (consoles, VoIP, cameras). NSM suggests turning it off, and never turns it off by itself unless the operator sets a policy for it.

## 4. GUI quality and regression queue

The classic contextual browser-feedback program ended with the removal of the classic interface (R1, Core 0.49.168–0.49.174); the React interface shows API errors in context (`ApiError`). Keep the GUI tracker focused on reproducible defects rather than broad rewrites.

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

**Implemented in `main` — real UISP instance validation pending.** Remaining step: confirm a scheduled cycle against a production-like UISP console. Since Core 0.49.189 the *Diagnosi compatibilità* panel collects that evidence (expected fields found or missing, endpoint outcomes) without exposing values.

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

**Implemented in `main`** (`app/uisp_onboarding.py`): UISP list with NSM state, explicit Customer/Site, preview, re-read at confirmation. Customer endpoints: automatic import under the NSM customer with the same name, or the one the operator linked (`app/uisp_auto_customer.py`, Core 0.49.244); infrastructure sites stay manual (operator decision 2026-10-23).

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

**Implemented (Core 0.49.272)**: the UISP API v2.1 exposes no ping or traceroute. NSM reads the airMAX wireless links (`/devices/airmaxes/{id}/stations`, checked read-only on the operator's console) with normalized values and hints, and runs UISP speed tests between two radios with confirmation (tested against a fake UISP; first run on a real link still to be done with the operator). Not used: site survey (disruptive), cable test (EdgeSwitch only).

### UBNT-06 — firmware workflow

**Step 1 implemented in `main`** (`app/uisp_firmware.py`): installed/latest version and compatibility read from UISP at every sync, firmware state on the Device and in the firmware worklist. Checked on the operator's console (2026-10-22): it sends only `compatible`, so the recommended version comes from the Ubiquiti update service by airOS platform (`identification.platformId`) and `overview.canUpgrade` marks unknown states as update available (Core 0.49.199). **Step 2 (Core 0.49.252)**: upgrade started from NSM through UISP (`POST /tasks`, version chosen among UISP's stable images for the platform, typed confirmation, optional maintenance window), followed and verified on the version UISP reports (`app/uisp_firmware_upgrade.py`). **Bulk upgrades (Core 0.49.260)**: up to 50 devices selected in the firmware worklist, recommended version per device or one version for all, one UISP task, outcome per device.

- read current/available state where supported;
- planning/approval model;
- capability and model/version checks;
- controlled execution only when the real integration supports it;
- post-action verification;
- audit/evidence.

### UBNT-07 — security and lifecycle correlation

**CVE part implemented (Core 0.49.267)**: airOS platform → NVD hardware family (airMAX AC / M / XW / XM / TI), release compared without build numbers, model lists never matched by guess. Lifecycle correlation is LIFE-02.

- normalize Ubiquiti model/version identity;
- correlate with the future advisory/lifecycle ingestion framework;
- never infer vulnerability from brand alone.

### UBNT-08 — full UISP device management (requested 2026-10-07)

**Step 1 done (Core 0.49.49)**: UISP history charts (signal, capacity, CPU/RAM, stations) on the device UISP tab.
**Step 2 done (Core 0.49.68)**: reboot with uptime verification, configuration backup archived in NSM (manual and by policy), interfaces and UISP events on the device UISP tab. Read endpoints checked on the operator's console (Core 0.49.199); reboot and backup requests not yet exercised there. Remaining: firmware upgrade execution through UISP.

Bring Ubiquiti devices managed through the UISP API to the depth of the MikroTik agent, for every operation the UISP API actually exposes and that can be verified on a real instance:

- configuration backup (download and archive in NSM, with evidence);
- reboot with confirmation, acknowledgement and post-reboot verification;
- firmware upgrade (planning, approval, execution through UISP, verification) on top of the UBNT-06 state;
- complete telemetry: signal/chain levels, noise, CCQ/airMAX quality, capacity, LAN/Ethernet interface state and speed, traffic, history;
- device logs.

### UBNT-09 — UniFi Network controller connector (requested 2026-10-23)

**Step 1 done (Core 0.49.283)**: connector configurable in *Integrazioni → UniFi Network* (API key, sites to read, TLS), connection test (version, sites, base path of UniFi OS console or self-hosted application), read-only synchronization every 15 minutes: controller device list by site with type, IP, firmware and state, Ubiquiti devices of NSM linked by MAC with the latest CPU, memory, uptime and uplink rates. Real controller validation pending (operator API key). Next: import into NSM, history charts on *Monitor*, firmware.

Ubiquiti UniFi devices (gateways/routers such as UDM, UCG, UXG and USG; USW switches; access points) are managed by a UniFi Network controller (UniFi OS console, Cloud Key or self-hosted Network Application), not by UISP. Build a connector on the controller API, alongside the UISP one:

- connector settings per controller: address, API key (official UniFi Network integration API, sent in a header), TLS verification, sites to include; the legacy session API only where the integration API does not cover a need and the operator accepts it;
- discovery of sites and devices, association with NSM Customers/Sites by preview and explicit confirmation, as in UBNT-03; NSM ownership stays authoritative;
- inventory by device type: model, MAC, IP, firmware, uptime, adoption/connection state; switch ports (link, speed, PoE); AP radios, channels and client counts; gateway WAN state;
- monitoring and history on the device *Monitor* tab with the shared renderer (CPU/RAM, port and WAN traffic, clients), offline devices keeping their last telemetry (MON-02);
- firmware state from the controller (installed/available) in the firmware worklist, then upgrade execution through the controller with the existing planning, scheduling and verification;
- operations the API exposes and that are verified on a real controller: restart, switch port PoE power cycle, AP locate;
- configuration backup: settings live in the controller, so archive the controller backup (or the per-site configuration if exposed) as NSM evidence, never claiming a per-device backup the API does not provide;
- remote syslog/SIEM server configured on the controller towards the NSM syslog server (LOG-01);
- security/lifecycle correlation of UniFi models and versions with SEC-05, Zabbix hosts per ZBX-01.

Every endpoint, field and write operation is checked on the operator's controller before it is enabled; writes need confirmation and audit events like the other connectors. Blocked on a reachable controller and an API key created by the operator.

### MON-01 — telemetry graphs and WAN/PPPoE traffic

**Step 1 implemented in `main` (Core 0.49.43, Agent 0.49.9)**: MikroTik interface traffic graphs on the Monitor page. Since Core 0.49.65 every graph (CPU, memory, traffic, UISP) uses the same Cacti/Zabbix-style renderer.
- Bit/s in and out from the agent byte counters, sampled every 2 minutes since Core 0.49.78 (Agent 0.49.18); 2-minute points for 7 days, then 10-minute averages, kept 90 days.
- WAN interfaces (pppoe-out, LTE, tunnel clients, else ether1) are monitored by default; operators can choose up to 8 per device.
- Statistics per range: current, average, maximum, 95th percentile and volume.

Next steps:
- ~~interface errors and drops~~ — done in Core 0.49.83 (Agent 0.49.20, *Errori e drop* chart);
- ~~latency series~~ — done in Core 0.49.85 (ICMP latency and loss from NSM for any vendor, unreachable alert);
- ~~signal series (MikroTik wireless)~~ — done in Core 0.49.86 (Agent 0.49.21, *Segnale wireless* panel);
- ~~the same graphs for UISP devices and cnMaestro~~ — done in Core 0.49.242 (Monitor tab for every device) and 0.49.266 (cnMaestro history in the standard panels, last values of offline Cambium devices);
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
- remote syslog setup for other vendors: ~~UISP/airOS~~ — done in 0.49.257 (UISP backup applied); ~~Cambium ePMP~~ — done in 0.49.265 over SSH (the cnMaestro API has no configuration endpoint; preview, typed confirmation, restore point); others with guided instructions;
- ~~legacy agent support~~ — done in 0.49.67 (Agent 0.49.15, legacy-ops-v1 profile);
- ~~incidents and reports built from the access events~~ — done in 0.49.50 (report section, incident evidence, incident from alert).


**Receiver.** A syslog receiver integrated in the stack, as a Docker service (UDP/TCP 514, optionally TLS 6514).
- Messages are matched to devices only by the per-device key `NSM-<16 hex>` (logging prefix on MikroTik, origin-id on Cisco, device name elsewhere); lines without a key are always discarded (Core 0.49.74, operator decision), except from the address of a TR-069 CPE that NSM itself set to send syslog through the ACS, when no other device uses that address (Core 0.49.220: TP-Link takes no key).
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
- **Done** for cnMaestro metrics, ICMP, wireless and the ACS history (Core 0.49.263: newest sample of each CPE and its newest optical reading; the ACS tab shows when the last sample arrived).
- **Done in 0.49.266** for cnMaestro (Cambium): the retention keeps the newest sample of each device, a sync without statistics keeps the last values on the device, and the Monitor tab shows them with their time when the range has no samples.
- **Still to do:** other connectors when they get history.

### INV-07 — MikroTik devices without IP in the device list (bug)

**Fixed in `main` (Core 0.49.44, Agent 0.49.10).**
- The agent reports the RouterOS addresses.
- The management IP is chosen in this order: the public router address, else the address NSM sees the heartbeat from, else the first address. A value typed by an operator is never overwritten.
- Lists show the management IP with its scope and the LAN IP. The device page also shows the address NSM sees.

### GUI-02 — visual refresh: Material icons and ISP/WISP background (requested 2026-10-07)

**Implemented in `main` (Core 0.49.48)**: outlined Material-style icon set drawn for NSM (sprite `static/ui-icons.svg`), sidebar and topbar refresh, ISP/WISP line-art background (`static/bg-network.svg`). **Core 0.49.275**: the section icon in every page header and empty state.

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
**Core 0.49.239** (operator report 2026-10-23: a port filtered by an ACL that admits the NSM server looked exposed): an open CWMP port on a TR-069 CPE managed by NSM is *da verificare* (NSM is its ACS), the CPE's remote web management settings read via TR-069 decide *esposto*/*limitato* and its port is probed too, and the operator can declare any service filtered by an ACL with the allowed addresses. Operator decision (2026-10-23): ACLs are declared by hand; no second vantage point (no external port-check service, no probe from another site).

A port/service scanner run from the NSM server against the WAN address of each managed device.
- **Targets.** Any vendor, not only MikroTik. The target is the detected WAN/PPPoE address, and the operator can pick or override the correct WAN IP when it cannot be determined.
- **Checks.** Critical services exposed on the WAN:
  - Telnet 23, SSH 22, FTP 21, HTTP/HTTPS admin 80/443/8080/8443;
  - DNS 53 (open resolver), SNMP 161/udp, TR-069 7547, UPnP 1900;
  - vendor-specific ports chosen by vendor and OS: RouterOS Winbox 8291, API 8728, API-SSL 8729, bandwidth-test 2000; UniFi/UISP 8443/2055; Huawei/ZTE ONT web and telnet; and so on.
- **Results.** Devices at risk are flagged like CVE exposure: per-device findings, a fleet "exposed services" view, severity, and inclusion in the vulnerability digest and reports.
- **Safety.** Scheduled and on-demand scans, only toward addresses of managed devices, with rate limiting and an audit trail.

### SCAN-02 — exposure only for devices reachable from Internet, from a real outside vantage point (requested 2026-10-23)

**Step 1 in Core 0.49.288**:
- every device gets its uplinks (MikroTik Agent addresses, TR-069 WAN, console, operator target), classified public / CGNAT / private;
- only devices with a public address raise exposure issues and notifications; services on devices behind NAT, CGNAT or on a LAN are informational (*solo rete interna*), and their open issues close by themselves;
- the state is recomputed every cycle from the inventory. A reboot, a PPPoE/WAN reconnection, a new public address (dynamic IP), a NAT ↔ public switch or a configuration change triggers a new check, besides the daily one. A device with several connections is public when any of them is.

Next steps (decided with the user):
- **0.49.291 — configurable external probe**: a small NSM probe service (container) installed on a host outside the operator network (any VPS). NSM sends it signed, rate-limited requests limited to the inventory's public addresses and the planned ports, and stores which vantage point answered.
- **third-party data** (version assigned when started): optional Shodan / Censys API keys (what Internet scanners saw, with their date), shown next to NSM's own result and never used as sole proof.
- When neither is configured NSM keeps probing from its own server, and says so: from inside the network the operator's border filters are not visible.

### SCAN-03 — extended port scanner for ISP/WISP networks (requested 2026-10-23)

Beyond the management ports of each vendor, check the ports of other protocols that matter on customer devices and the ones historically abused for attacks:
- amplification: NTP 123, CLDAP 389, memcached 11211, chargen 19, QOTD 17, WS-Discovery 3702, mDNS 5353, NetBIOS 137, RPC portmapper 111, plus the DNS/SNMP/SSDP/UBNT discovery already checked;
- remote access and file sharing: RDP 3389, VNC 5900, SMB 445, NetBIOS 139, TR-064 49000, Telnet 2323, PPTP 1723, IPMI 623;
- databases and services left open: MySQL 3306, MSSQL 1433, PostgreSQL 5432, Redis 6379, MongoDB 27017, Elasticsearch 9200, Docker 2375/2376;
- IoT and VoIP: RTSP 554, SIP 5060, MQTT 1883, Modbus 502.

Profiles: *vendor* (today's list), *extended* (the list above, default for public devices) and *full* (all TCP ports, only from the external probe, operator-triggered and rate-limited). Each port has a severity and an explanation. Results feed the same exposure findings, Action Center and the ACL button (ACCESS-01). Only addresses of the inventory are ever scanned.

### CYBER-01 — abuse and reputation toolkit for the operator's addresses (requested 2026-10-23)

A complete cybersecurity view for an ISP/WISP, taking as reference the open-source project AbuseBox (`bekkaze/abusebox`, MIT), re-implemented inside NSM on its own inventory:
- **blacklists**: DNSBL checks (tens of providers) of the operator's public IPs (customers, CGNAT pool, servers) and of whole subnets (/24), with links to each provider's removal page and delisting tracking; **DNSBL done (Core 0.49.313)**: *Integrazioni → Blacklist DNS*, IPv4 of the declared prefixes and of the inventory, removal links, Action Center issues with the holder; delisting tracking and charts still open.
- **reputation**: AbuseIPDB score and reports (optional API key), correlated with the customer and device owning the address;
- **availability**: DNS resolution, HTTP/HTTPS status and response time of the operator's public services;
- out of scope for now (user, 2026-10-23): domains, SSL certificates and mail authentication (SPF/DKIM/DMARC);
- **operator's public subnets** (requested 2026-10-23): the operator declares the public prefixes it owns or uses (allocations, NAT pools, server ranges). Every address in them is monitored (DNSBL, reputation, reports) whether or not it is assigned to a customer:
  - a listed **unassigned** address (no session, no device, no static IP at that time) is flagged as such, since it can mean spoofing, a hijacked or misrouted prefix, or a forgotten assignment;
  - a listed address of a NAT pool is reported with the pool and gateway, without a customer (see the attribution scope);
  - a listed address assigned to a customer gets the customer and the device;
- **assets from NSM**: public IPs of devices and customers (and ISPBilling/RADIUS pools) imported automatically, ranges by CIDR, per-asset checks and schedules;
- **monitoring and alerts**:
  - scheduled re-checks and a needs-attention view (listed, poor reputation, down);
  - activity log, with history and charts of listings over time;
  - alerts through the existing NSM channels (email, Telegram, webhook) and Action Center issues; CSV export.

**Step 1 done (Core 0.49.312)**: address history of the MikroTik BRAS PPPoE sessions (`ip_assignments`, retention configurable), *Chi aveva questo IP?* look-up (BRAS session, else ISPBilling static IP; audited), AbuseIPDB issues assigned to the holder at the report time. Next: RADIUS accounting, WAN changes of TR-069/Agent/UISP devices (*probable*), NAT pools.

**IP attribution at the time of the incident (requested 2026-10-23):** every reputation event (AbuseIPDB report, DNSBL listing, CrowdSec decision, abuse e-mail) carries an IP and a time; NSM finds who had that IP *at that moment*, not now, because PPPoE addresses are dynamic:
- **address history**: a table of assignments (IP, device, customer, source, from, to), fed by what NSM already sees:
  - PPP sessions of the concentrators: the MikroTik BRAS connector (BRAS-01, Core 0.49.261, read-only `/ppp/active` every 5 minutes) keeps only the current state per username, so it gets a **session log** (username, address, caller-id, start, end) written at every read; also the PPPoE login/logout lines in the concentrator syslog and the agent `/ppp active` snapshots;
  - the WAN address changes of TR-069 CPEs, MikroTik Agents and UISP devices (SCAN-02 records every change);
  - static IPs of the ISPBilling RADIUS accounts and the RADIUS accounting (Framed-IP-Address with start/stop; the operator confirmed it is available, 2026-10-23), which also covers the time before NSM started reading the BRAS;
- **attribution with a confidence level**:
  - *exact*: a RADIUS/PPP session covering the time;
  - *probable*: the address reported by the device in that window;
  - *not attributed*: NAT pool or CGNAT address, reported with the public IP only (see the scope below);
- **scope decided with the user (2026-10-23)**:
  - **public addresses** (static public IP, or a public IP received from a dynamic pool): the customer is attributed from NSM's own data only, i.e. the BRAS session log, the RADIUS accounting and the static IPs, with no external syslog;
  - **NAT addresses** (customers behind the edge NAT, NAT pools, CGNAT): NSM does **not** look for the responsible customer. It reports the public IP, the time and, when declared, the NAT gateway or pool it belongs to; the operator finds the customer with their own NAT logs;
  - generic for any operator: dedicated BRAS or not, public pools that fall back to NAT pools, static or dynamic NAT profiles. The session log records the address each session really got, so no case needs a special rule;
- later, optional: attribution behind NAT from deterministic NAT rules or from the operator's NAT log server (search on demand, read-only), only if requested;
- the incident is linked to the customer and device found (customer page, Action Center), with the evidence used;
- configurable retention of the history, as operational data. It is not the legal traffic-data register, which stays where the operator keeps it.

### ABUSEIPDB-01 — AbuseIPDB integration (requested 2026-10-25)

First reputation source of CYBER-01. The operator is creating a free AbuseIPDB account for the API key (2026-10-25); NSM must work within the free plan and use more only when the operator upgrades.
- **module and settings**: *Integrazioni → AbuseIPDB* as its own module (MOD-01), off by default; API key stored encrypted, *Test* button, maximum report age (`maxAgeInDays`, default 30), score threshold for an alert (default 25), checks per day left for NSM;
- **quota first**: the free plan has a daily limit on checks and a smaller limit on block checks; NSM reads the remaining quota from the response headers (`X-RateLimit-Remaining`, `Retry-After`), shows it on the module page, spreads the checks over the day and stops before the limit, never retrying on HTTP 429. The exact limits of each plan are read from the account, not hard-coded;
- **what is checked**, inside the operator's own address space only:
  - the declared public subnets (CYBER-01) with `GET /api/v2/check-block` per /24 (each call returns the reported addresses of the block, so a whole /24 costs one call);
  - the public IPs of devices and customers that are not inside a declared subnet, with `GET /api/v2/check`, one per address, oldest check first;
  - every address again when it changes (SCAN-02) or when a report is new;
- **what NSM keeps**: per address, the abuse confidence score, the number of reports and distinct reporters, the categories (port scan, brute force, web attack, spam…), the last report time and, with `verbose`, the last reports' comments and countries; history of the score for charts;
- **results**:
  - an Action Center issue for an address above the threshold, with the customer and device found by the attribution at the time of the reports (CYBER-01 scope: public addresses get the customer, NAT addresses get only the public IP and the pool);
  - the score on the device and customer pages and in the exposure/security views; an unassigned address with reports is flagged as such;
  - alerts through the existing channels; the issue closes when the score falls below the threshold;
- **attacker look-up (later, optional)**: the score of the external addresses seen in NSM's own logs (failed logins in the syslog of routers and CPEs, SIEM-01 rules) to rank them; it uses the same daily quota, so it stays off unless the operator turns it on;
- **reporting to AbuseIPDB (later, opt-in only)**: `POST /api/v2/report` for attackers NSM is sure about (e.g. repeated SSH brute force against a device), with categories and a comment that never contains customer data or internal addresses; disabled by default and rate-limited;
- **privacy**: only IP addresses leave NSM, never names or customer data; the key is never shown again after saving.

### RIPESTAT-01 — prefixes, BGP and RPKI of the operator through RIPEstat (requested 2026-10-25)

RIPEstat Data API (`https://stat.ripe.net/data/<call>/data.json`): free, no account and no key, every request carries `sourceapp=nsm` (checked 2026-10-25). It has no reputation data (the old `blocklist` call no longer exists), so it complements ABUSEIPDB-01 and Shadowserver on the routing side:
- **module and settings**: *Integrazioni → RIPEstat* (MOD-01), off by default; the operator's ASNs; checks every few hours, few calls (one per ASN and per prefix), cached;
- **public subnets from the ASN**: `announced-prefixes` (and the allocations of the ASN holder) proposed as the declared public subnets of CYBER-01, so AbuseIPDB and Shadowserver cover all of them; the operator confirms, nothing is added silently;
- **hijack and route watch**: `routing-status` / `visibility` per declared prefix: alert when a prefix is announced by another origin ASN, a more specific appears, a prefix is no longer announced or its visibility drops (Action Center issue, alert channels);
- **RPKI**: `rpki-validation` (ASN + prefix) for every announced prefix; *invalid* or *not found* (no ROA) reported with what to create in the RIPE portal;
- **registry hygiene**: `abuse-contact-finder` compared with the mailbox NSM reads abuse reports from (SEC abuse workflow); `reverse-dns-consistency` of the prefixes; `maxmind-geo-lite` geolocation of the prefixes, to catch ranges placed in the wrong country (customers blocked by streaming services and banks);
- **read-only**: NSM never changes anything at RIPE; links open the RIPE portal pages to fix ROAs, contacts and reverse DNS.

### CROWDSEC-01 — CrowdSec integration (requested 2026-10-23)

CrowdSec (open-source security engine, free community blocklist) to protect NSM itself and to enrich the security view of the network:
- **protect the NSM server and the external probe**:
  - CrowdSec engine with the parsers for the NSM web/API (Caddy), SSH and the syslog receiver;
  - bouncers that block the attacking IPs (Caddy bouncer, firewall bouncer);
  - installed and updated by the NSM installer.
- **NSM reads the decisions** through the CrowdSec Local API, with a read-only key:
  - IPs blocked and why, on the *Sicurezza → Accessi* page;
  - correlated with the login failures that the customers' devices send through syslog.
- **NSM as a source**: brute-force attempts seen in the devices' syslog become CrowdSec alerts, so an IP that attacks one customer is known for all of them.
- **Reputation of the operator's addresses** (with CYBER-01): customer public IPs that appear in the community blocklist point to a compromised CPE or router, and get an Action Center issue on the owning device.
- **Optional, operator's choice per device**: the blocklist as a RouterOS address list (`crowdsec`) on MikroTik routers through the Agent, with a drop rule on the input chain. It is applied only with explicit confirmation and can be removed, and it is never written by default.

### DOC-01 — platform manual, the complete guide to NSM (requested 2026-10-23)

A complete manual of the whole platform, meant to be its reference ("the bible"):
- written in the **third person** ("the operator opens…, NSM shows…"), step by step;
- with **screenshots** of every step and real-looking **examples**.

The module guides of MOD-01 are chapters of it. Contents:
- **getting started**:
  - installation, first access, setup wizard;
  - modules, users and roles;
  - branding, notifications;
- **customers and sites**:
  - creating a customer, by hand or imported from ISPBilling;
  - sites, contacts, the customer page;
- **adding a device, by type**:
  - MikroTik with the Agent, modern and legacy RouterOS;
  - Ubiquiti through UISP, UniFi through the controller, Cambium through cnMaestro;
  - TP-Link and other CPEs through TR-069 / GenieACS;
  - other devices by hand;
  - bulk onboarding from each console;
- **daily work**:
  - backups (policies, restore, compare);
  - monitoring and graphs, Zabbix;
  - syslog and access logs, alarms and the Action Center;
  - firmware (check, plan, schedule, upgrade);
- **security**:
  - exposure and reachability, CVE and lifecycle;
  - management networks (SNMP, SSH, NTP);
  - reputation, abuse incidents and the other security modules;
- **reports**, the global search, the API (with examples);
- **operations**: upgrades, backups of NSM itself, server migration, troubleshooting, FAQ, glossary.

How it is built:
- **Built into NSM** (*Guida* in the menu, no Internet needed) and exportable as PDF; versioned with the releases, with a "what's new" page from the changelog.
- **Screenshots generated automatically** from the demo dataset (synthetic data only, as the public data-safety rules require) by a browser script in CI, so they follow the interface at every release.
- **Italian first**, then English, French and Spanish.
- A PR that changes a flow updates the matching chapter, and CI reports pages whose screenshots changed.

### MOD-01 — optional modules (requested 2026-10-23)

The security services below, and the other integrations of this roadmap (CrowdSec, external probe, third-party data, UniFi, BRAS, NetFlow collector…), are **modules** that each installation turns on or off. This keeps NSM usable by operators with different networks and needs:
- **one place for each thing, no duplicates** (user, 2026-10-23):
  - a single *Moduli* section lists every module, with what it does, what it needs (API key, mailbox, flow exporter, external host…) and its state: off, on or error;
  - it replaces today's two entry points: the *Integrazioni* hub, whose cards lead to *Amministrazione → Integrazioni → …*, and the separate admin tabs such as *Syslog* or *ACS*;
  - each module has **its own page** with the same tabs everywhere: *Guida*, *Configurazione* (all its settings, and only there), *Stato e diagnosi*, *Dati* (what it collected, e.g. imports or onboarding), *Registro* (its audit events);
  - the ***Guida*** tab is the module's wiki (user, 2026-10-23). It explains:
    - what the module does and what it does not;
    - how it works: data sources, what is read or written on devices and external services, timing;
    - what it needs and the setup steps on the other side (e.g. creating the API key or the read-only user);
    - every setting;
    - permissions, retention and privacy;
    - limits and known issues, with troubleshooting from the *Stato e diagnosi* messages;
    - the version that introduced or changed it.

    The guide is written in the four languages, ships with NSM (no Internet needed), is opened from every *?* next to the module's settings, and is searchable from the global search. A PR that changes a module's behaviour updates its guide, which the static checks verify.
  - *Amministrazione* keeps only the core: users and roles, API keys, branding, system, server migration, demo, authorized management networks (ACCESS-01);
  - device and customer pages show a module's data in their tabs but never its configuration. They link to the module page instead;
  - old addresses (`/admin/integrations/...`, `/admin/syslog`, `/admin/acs`, `/integrations`) redirect to the module page, and global search points to it;
- **off by default** unless it only reads data NSM already has;
- a module that is off has no worker task, no data collection, no outgoing request, no menu entry and no notification. Its data is kept read-only until the operator deletes it;
- each module has its own settings, permissions, retention and audit events (`MODULE_ENABLED` / `MODULE_DISABLED`), and declares the modules it depends on: SEC-12 needs SEC-11 or another incident source, SEC-17 needs SEC-16's collector, SEC-19 can use the external probe;
- **existing integrations become modules too** (user, 2026-10-23):
  - UISP;
  - GenieACS / TR-069 ACS, with the TP-Link extensions and the TR-069 speed test;
  - Zabbix;
  - cnMaestro;
  - ISPBilling (and its RADIUS accounts);
  - UniFi;
  - MikroTik BRAS;
  - syslog receiver and device logs;
  - NVD advisories and vendor CVE matching;
  - legacy FTP backup receiver;
  - notification channels (e-mail, Telegram);
  - The Dude, when it is done;
- a module that runs its own containers (GenieACS, syslog receiver, NAT/flow collectors) starts them only when on, through Docker Compose profiles managed by the installer and `manage.sh`;
- **upgrade without surprises**: on existing installations every integration already configured or in use stays on, and only the new modules start off. A fresh installation asks which modules to enable in the setup wizard;
- the core is never a module: inventory, customers and sites, users and roles, audit, MikroTik Agent, backups, monitoring and graphs, firmware, exposure from configuration;
- existing switches such as `EXPOSURE_EXTERNAL_CHECK` become module settings.

### ISP security services (requested 2026-10-23)

Each service is a module (MOD-01), enabled independently. All linked to customers and devices through the IP attribution of CYBER-01 (public addresses only; NAT addresses reported with the public IP) and to the Action Center and the existing notification channels.

#### SEC-10 — Shadowserver reports
Free daily reports for network owners: open services, infected hosts and botnet members on the operator's addresses. NSM imports them (subscription by e-mail or the Shadowserver reports API) and links each entry to the customer and device that held the IP. Report history is kept per address.

**Status (2026-10-25)**: the operator submitted the subscription for their ASN/subnets to a dedicated mailbox, without a PGP key (not needed: NSM uses the API). The API access was not asked in the form, so it was requested afterwards through the Shadowserver contact form / `report_admin@shadowserver.org`. Shadowserver verifies the network ownership (WHOIS references) before activating; waiting for the reports and the API key and secret.

**Implementation once the credentials arrive**:
- **module and settings**: *Integrazioni → Shadowserver* (MOD-01), off by default; API key and secret stored encrypted, never shown again, *Test* button (list of the reports of the last day); the credentials are entered on the server, never in chat or in the repository;
- **download**: once a day, after the morning generation (UTC): `reports/list` for the previous days not yet imported, requests signed with HMAC-SHA256 of the secret, then each report downloaded by id; CSV parsed by report type, imported once (report id kept), compressed or not;
- **report types first**: open/exposed services (SNMP, DNS resolvers, SSDP/UPnP, NTP, Telnet, RDP, HTTP admin pages…), DDoS amplifiers, compromised hosts and botnet drones (sinkhole reports), vulnerable devices (CVE scans); unknown types kept raw and listed, not lost;
- **results**:
  - each row is matched to the device and customer that held the IP at the report time (CYBER-01 attribution: public addresses get the customer, NAT addresses only the IP and pool), and to the exposure finding NSM already has for that device (SCAN-02/SCAN-03), so an external confirmation and NSM's own check are shown together;
  - Action Center issues by type and severity (e.g. infected host, open resolver, SNMP *public* reachable), closed when the address no longer appears for a configurable number of days;
  - ACCESS-01 / ACCESS-02 buttons (management ACL, UPnP off) offered from the issue when the device supports them;
- **volume**: Delta mode can be asked to Shadowserver when the e-mails are too many; NSM does not need the e-mails once the API works.

#### SEC-11 — incoming abuse e-mails
NSM reads the abuse mailbox (IMAP, read-only folder) and parses the standard formats (ARF, X-ARF, the usual plain-text reports). Each message becomes an incident with IP, port, time and time zone, the attributed customer, and the original message attached.

#### SEC-12 — abuse incident workflow
From the incident to the notice to the customer, with an e-mail template and the evidence, then to closure. States, assignee, deadlines and history are kept, so the record also answers whoever reported it. Repeated incidents on the same customer are grouped.

#### SEC-13 — Spamhaus DROP / EDROP
The free lists are checked daily:
- alert if one of the operator's prefixes appears;
- optional, per device and with confirmation: a RouterOS address list blocking those networks on MikroTik through the Agent.

#### SEC-14 — BGP monitoring of the operator's prefixes
For the operator's prefixes and ASN, using public data (RIPE RIS live, RIPEstat):
- RPKI ROA validity;
- route objects in the RIPE database;
- alerts on hijacks, more-specific announcements or unexpected origin ASNs.

#### SEC-15 — RIPE contact data
Checks that the abuse contact (`abuse-c`) and the objects of the operator's ASN and inetnums are present and current, and that the abuse address answers.

#### SEC-16 — outbound abuse from customers (NetFlow/IPFIX)
MikroTik `traffic-flow` (or any IPFIX exporter) sent to an NSM collector, aggregated per customer IP:
- SMTP 25 spikes (spam);
- scanning (many destinations or ports);
- participation in a DDoS (high outbound rates towards one target).

A likely compromised CPE is flagged before the reports arrive.

#### SEC-17 — inbound DDoS detection
Thresholds per IP or customer on the same flows (packets/s, bits/s, new flows/s), with alerts. Optional, with confirmation: remote-triggered blackhole (RTBH) of the attacked address only, through the operator's edge router.

#### SEC-18 — default credentials on customer devices
Detected from the configuration NSM already reads, never by trying to log in: factory passwords on CPEs and routers (e.g. `ubnt`/`ubnt` on airOS, default TR-069 / web passwords, no password on RouterOS), and default TR-069 connection-request credentials. A finding with a remediation hint is added to the device's security page.

#### SEC-19 — honeypot on unused public addresses
The operator's free public addresses (from the subnets of CYBER-01) are used as a trap, by a light sensor on the NSM probe or a dedicated host:
- a customer IP contacting them from inside the network is likely infected;
- contacts from Internet show the scans in progress.

They feed the reputation and CrowdSec (CROWDSEC-01).

#### SEC-20 — periodic security report
Weekly or monthly, per customer and for the whole network, as PDF or e-mail through the existing report builder: exposures, blacklists, abuse incidents, vulnerable firmware, default credentials, and the trend against the previous period.

### ZBX-01 — Zabbix API integration (requested 2026-10-07)

**Decided with the user (2026-10-08): NSM pushes its devices to Zabbix. Implemented in `main` (Core 0.49.51).** Hosts, groups per customer, templates per manufacturer, tags and inventory; 6.x and 7.x authentication; hosts disabled (not deleted) when devices leave NSM. Zabbix problems are shown inside NSM since Core 0.49.62 (device header and overview, Monitoring). **Since Core 0.49.205 (operator decision 2026-10-22)**: devices already monitored in the operator's Zabbix are linked to their existing host (same IP, else same name, only when exactly one matches) instead of getting a duplicate `nsm-` host; linked hosts are never modified except their interface IP, kept equal to the device address for dynamic public IPs/NAT (option); ambiguous matches are reported; the settings form suggests the templates found in Zabbix. **Since Core 0.49.243 (operator request 2026-10-23)**: *Aggiungi a Zabbix* / *Rimuovi da Zabbix* on every device (opt-in kept on the device, also outside the customer scope) and on every customer (joins/leaves the scope with all its devices); each click pushes at once and reports the result (created, linked, no IP, factory LAN IP); removed hosts are disabled, never deleted; audited `ZABBIX_DEVICE_SCOPE_CHANGED` / `ZABBIX_CUSTOMER_SCOPE_CHANGED`. **Since Core 0.49.282**: hosts get the interfaces their templates need (SNMP for `AirOS8` / MikroTik SNMP templates). **Since Core 0.49.294**: a literal SNMP community is stored encrypted and never returned by the API or written to the audit; `{$MACRO}` values stay in clear.

Integration with the operator's Zabbix (currently 6.x; support both the 6.x API and 7.x, where authentication moved to an API token in the `Authorization` header). How it applies to devices (import hosts/items, link NSM devices to Zabbix hosts, show problems and graphs, or push NSM devices to Zabbix) is to be decided with the user before implementation.

### DUDE-01 — MikroTik The Dude integration (requested 2026-10-07)

**Paused (2026-10-08)**: the user is evaluating whether it is worth adding.

Integration with MikroTik The Dude through the RouterOS API of the Dude server (`/dude` menu: devices, probes, services, notifications, maps). Which features are worth it is to be evaluated with the user. Candidates:
- import or reconcile the Dude device list (name, address, type) with the NSM inventory;
- use Dude up/down and service state as an extra monitoring source on the device page and in alerts, without duplicating polling;
- correlate Dude outages with NSM events (agent heartbeat, firmware jobs, backups);
- link from the NSM device to the Dude map element.

### GUI-OPEN — open the device: web interface, Winbox, management console (requested 2026-10-23)

Buttons on the device page (and in the device list menu) to reach the device where it is managed. The operator always types the credentials: NSM never stores or passes them.

- **Open web interface**, for TP-Link routers, Ubiquiti CPEs, MikroTik and the other devices whose web server is enabled. NSM builds `http(s)://<management address>:<port>` from the port it discovers:
  - **UISP**: the web port of airOS in the device configuration (`httpd` / `httpsd` port of `system.cfg`, or the UISP device fields if exposed);
  - **TR-069**: the CPE's HTTP/HTTPS management port and whether remote web access is enabled (`Device.UserInterface.RemoteAccess.*` and the vendor extensions, e.g. TP-Link);
  - **MikroTik Agent**: `www` / `www-ssl` of `/ip service`, already read by the services snapshot (enabled, port, allowed addresses).

  The button appears only when the web interface is enabled. It warns when the address is private or the service only accepts some addresses (*available-from*).
- **Open in Winbox**, for MikroTik: the Agent knows the address and the `winbox` port of `/ip service`, and the button opens Winbox on the operator's PC with that address and port. Browsers cannot start a program without a registered URL protocol: to be verified whether the Winbox release in use registers one (Winbox 4). Otherwise NSM offers a small optional handler for Windows (e.g. `nsm-winbox://`, registered once per PC) that starts `winbox.exe <address>:<port>`. Fallback: copy `address:port` to the clipboard.
- **Open in the management console**: the device page in the console that manages it, built from the link NSM already stores:
  - *Open in UISP*: UISP device page from the UISP id;
  - *Open in UniFi*: UniFi Network device from the site and MAC (UBNT-09);
  - *Open in cnMaestro*: cnMaestro device page from the MAC;
  - *Open in GenieACS / ACS*: ACS device page from the TR-069 id;
  - *Open in Zabbix*: the linked Zabbix host (already shown as a link where available).

  Every console URL format is checked on the operator's console before the button is enabled.

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

**Implemented in `main`** for per-Device association (serial, then MAC) of TR-069 CPEs of any brand (TP-Link, or Huawei/ZTE/others entered as *Altro produttore* with type router, ONT, wireless CPE or other — Core 0.49.66). The bulk CWMP rewrite of an existing fleet (Core 0.49.202) was removed in Core 0.49.225: unusable on the operator's TP-Link firmware.

- match candidates using reliable ACS identifiers;
- preview before association;
- retain external Device ID;
- preserve NSM Customer/Site authority;
- duplicate/conflict controls.

### ACS-03 — inventory normalization

**Implemented in `main`** for identity, model, firmware, hardware, IP and last Inform (TR-098 and TR-181 paths).

Normalize relevant Device identity and firmware fields from supported data models.

### ACS-04 — parameter profiles

**Implemented in `main` (Core 0.49.264)**: one map of the TR-098/TR-181 paths (`acs_profiles`) used by every ACS module, TP-Link vendor paths only where required, explicit unsupported entries with their reason, and the *Capacità NSM* view of each CPE (read only), tested on sanitized TP-Link TR-181 and Huawei TR-098 structures.

- TR-098 / TR-181 mapping profiles;
- vendor/model-specific profiles only where required;
- explicit unsupported fields;
- tests against sanitized representative structures.

### ACS-05 — monitoring/history

**Implemented in `main` (Core 0.49.206, 0.49.211)**: state at every Inform (CPU, memory, WAN, Wi-Fi, LAN hosts, DSL), 90-day samples and charts on the device ACS tab, online/offline from the last Inform. Alerts on CPE offline, WAN down, WAN reconnections, reboots and DSL degradation (Core 0.49.226). PON optical levels (Rx/Tx, temperature, voltage, bias) from TR-181 `Device.Optical` and TR-098 vendor GPON objects, read hourly, with charts and a low-Rx alert (Core 0.49.263).

Persist only supported, stable operational data with capability-aware unavailable states.

### ACS-06 — diagnostics

**Implemented in `main` (Core 0.49.206)**: ping and traceroute run by the CPE through GenieACS (TR-181 and TR-098), results on the device ACS tab; download/upload speed tests (TR-143) towards NSM itself or a server set by the administrator (Core 0.49.218).

### ACS-07 — backup/restore capability

**Backup implemented in `main` (Core 0.49.217)** as configuration-parameter snapshots archived and comparable in the device Backup tab, scheduled by backup policy (GenieACS has no `Upload` task for the vendor file). **Restore implemented (Core 0.49.269)**: preview of the writable parameters that differ, operator selection, typed confirmation, one `setParameterValues` task, verification by reading back, faulted tasks deleted with their fault.

Only expose backup/restore where the real CPE data model and connector expose a verifiable supported mechanism.

### ACS-08 — firmware workflow

**Implemented in `main` (Core 0.49.219)** for one CPE at a time, on the device Firmware tab like the other devices (installed version against the catalog's newest release): vendor image loaded into GenieACS per model, `download` task after typed confirmation and model check, verified from the reported `SoftwareVersion`, faults and timeout reported, audited. **Campaigns (Core 0.49.270)**: one pilot CPE, then waves (10 by default) inside a time window, automatic stop at the first failed or unconfirmed upgrade, typed confirmation, pause/resume/cancel, per-CPE state page. **Core 0.49.292**: a faulted or never-run `download` task is deleted from GenieACS with its fault, so it no longer blocks the CPE's queue.

- firmware target/evidence model;
- controlled ACS operation;
- progress/result state;
- post-upgrade verification;
- audit/evidence.

### VEND-01 — firmware catalogs for other vendors (requested 2026-10-07)

**Implemented in `main` (Core 0.49.69)**: *Firmware → Cataloghi altri produttori* — Ubiquiti from UISP and from the official update service (`fw-update.ubnt.com`, airOS platform from the installed firmware string or from UISP; checked from the deployed instance in Core 0.49.199, where the request filters were corrected: the service files the platform code under `product` and the family `airmax` under `platform`), other brands from releases entered from the official download pages; device firmware state evaluated per model. **Core 0.49.227**: TP-Link from the official Italian download pages per model and hardware version, with one-click download into the ACS for TR-069 CPEs. **Core 0.49.247**: operator releases are uploaded images; version, models or platform and date are read from the official file name (airOS header too), and only the rest is asked. Next: official machine-readable sources for other brands where they exist.

Release catalogs like the RouterOS one (versions per channel/model, release notes, security classification) for Ubiquiti, TP-Link, Tenda, Cambium and similar vendors. Each catalog is enabled only when the inventory contains devices of that brand, reads only official vendor sources and never guesses versions it cannot verify.

### VEND-02 — cnMaestro integration for Cambium (requested 2026-10-07)

**Step 1 implemented in `main` (Core 0.49.70)**: OAuth2 client credentials, paged inventory and statistics, linking by MAC/serial, identity/firmware/IP/status sync every 15 minutes, metrics history (signal, SNR, throughput, CPU/memory, stations) with Cacti-style graphs on the device *cnMaestro* tab.
**Step 2 implemented in `main`**: compatibility check on the real console (Core 0.49.189) and bulk onboarding like UISP (Core 0.49.192). Field names checked on the operator's cnMaestro (Core 0.49.199; Wi-Fi AP per-band clients and traffic, time online as uptime). **Core 0.49.251**: configuration backup of ePMP devices over SSH (`config show json`), because the cnMaestro API cannot export configurations; shared SSH credentials with per-device override; backup policy option. API v2 done in Core 0.49.259. **Core 0.49.265**: remote syslog of ePMP devices over SSH (the API has no configuration endpoint). **Core 0.49.266**: cnMaestro history in the Monitor tab, last values of offline devices (MON-02). **Core 0.49.271**: reboot through cnMaestro with typed confirmation and verification. The cnMaestro API v2 has no software image or upgrade endpoint (checked GET-only), so **Core 0.49.294** upgrades ePMP devices over SSH: image from the NSM catalog served by a signed link, SSH backup first, the radio's software update agent started with `snmp set` on its console, verification from cnMaestro or the ePMP (live validation pending).

Read-only cnMaestro (cloud/on-premises) connector for Cambium radios: discovery and association like UISP, periodic sync, full monitoring and statistics (signal, link quality, capacity, interfaces, uptime, history) in the style of the MikroTik and UISP views; later operations only where the cnMaestro API supports them.

### ISPB-01 — ISPBilling integration (requested 2026-10-22)

Import the customer registry and sites from the operator's billing system (ISPBilling, `https://ispbilling.it/api/docs`), then automate around it. Approved by the operator: (1) customers, (2) services → sites, (3) RADIUS accounts ↔ devices, (4) warehouse serials/MACs ↔ devices, (5) activities into the device logbook; invoices and payments stay out of NSM.

**Step 1 implemented in `main` (Core 0.49.203)**: read-only connector, mirror refreshed by the worker, import page for customers and their installation addresses as sites (details in `IMPLEMENTED_CAPABILITIES.md` §13c). **Step 2 implemented (Core 0.49.209)**: RADIUS accounts matched to devices (PPPoE user, then static IP) and shown on the device page with their ISPBilling status. **Core 0.49.229**: also by the CPE's own PPPoE user (TR-069) and by the caller-id MAC/address of the PPP session on a concentrator in NSM; customer and site proposed for confirmation. **Step 5 implemented (Core 0.49.262)**: ISPBilling activities of the device's place or customer in the device Activity tab, read on demand. Next: warehouse.

### TAUC-01 — TP-Link Aginet Unified Cloud connector (requested 2026-10-22)

Research and build a connector on the TAUC Open API. Public research (2026-10-22) found only TP-Link product pages stating "Open API", webhooks and API gateways for zero-touch provisioning, with TR-069/TR-369 and TR-181 on the device side; no public API reference. Blocked on the API documentation from the operator's TAUC console or TP-Link service-provider support, and on API credentials entered by the operator.

### ACS-11 — evaluate an alternative ACS with its own dashboard: Oktopus (requested 2026-10-24)

The operator wants a clear, simple dashboard for the TR-069 CPEs. Options reviewed on 2026-10-24: dashboards on top of GenieACS (`safrinnetwork/GACS-dashboard`: separate PHP/MySQL application with its own users and default credentials, a single published commit, Indonesian UI; `aanmochtar80/GenieACS-Dashboard` in Go; the official `genieacs-gui`, unmaintained since 2022) were not adopted. The operator chose to study **Oktopus** (`OktopUSP/oktopus`, open-source TR-369/USP controller with TR-069 support) as a possible replacement for GenieACS.

Feasibility study only, no change to the running ACS. To check on a lab instance before any decision:

- TR-069 coverage of the CPE models in use (TP-Link first): inform, connection requests, parameter read/write, reboot, factory reset, firmware download, vendor `X_TP_*` objects; TR-369 support of the same models;
- northbound API usable by NSM for what it already does through the GenieACS NBI (inventory, monitoring, Wi-Fi, diagnostics and speed tests, firmware and campaigns, configuration snapshots and restore, remote access, ISP default, customer tags);
- dashboard: fleet view, per-CPE detail, search by customer, users and permissions;
- licence, release cadence, maintainers, Docker deployment next to the NSM stack, resource use, security record;
- migration path: CPEs moved per group by changing the ACS URL, rollback to GenieACS.

Outcome: a written comparison (keep GenieACS, move to Oktopus, or run both during a migration) for the operator's decision.

GenieACS customer tags: since Core 0.49.237 NSM tags every linked CPE in GenieACS with its customer, so the GenieACS device list shows whose CPE it is; since Core 0.49.299 the tag is "Name · code", so namesakes are told apart.

## 7. Cross-vendor vulnerability management

### SEC-05 — multi-vendor CVE correlation

**Implemented in `main`** (`app/vendor_cpe.py`): about 30 common manufacturers (Ubiquiti, TP-Link, Cambium, Mimosa, Tenda, Huawei, ZTE, Teltonika, Ruckus, Fortinet, Juniper, Cisco…), NVD queries only for the brands and models in the inventory, generic firmware version comparison, coverage per manufacturer on the NVD page. Exposure is never inferred from the brand alone. Huawei VRP version scheme and Cisco OS families/version trains done in Core 0.49.93.

### SEC-01 — source ingestion framework

**Implemented in `main` — live NVD validation pending.** First adapter: NVD CVE API 2.0 for `cpe:2.3:o:mikrotik:routeros` (`app/advisory_sources.py`, admin page *Integrazioni → Advisory NVD*). Remaining step: one full load and one incremental cycle against the real API from the deployed instance.

- normalized advisory model;
- source identity/fetch timestamp/evidence;
- idempotent refresh;
- parser failure visibility;
- source-specific adapters kept separate from matching logic.

External sources should be documented only after an adapter actually uses them.

### SEC-02 — Device ↔ advisory matching

**Implemented in `main` for MikroTik RouterOS** (`app/advisory_matching.py`). **Core 0.49.267**: version matching for the other vendors through `vendor_cpe` — Ubiquiti airMAX by hardware family from the airOS platform, TP-Link release and build, product names without punctuation; data that is not enough stays *unknown*.

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

### Detection ideas from Wazuh (requested 2026-10-24)

The operator looked at Wazuh (`wazuh/wazuh`, open-source SIEM/XDR) and asked to bring five of its ideas into NSM. NSM already has, from the same family:
- brute-force and access alerts from syslog (LOG-01, `syslog_security.py`);
- tamper-evident syslog (`syslog_integrity.py`);
- MikroTik configuration drift (`config_drift.py`);
- compliance baselines (COMP-01…04);
- CVE matching (SEC-01…05);
- incident correlation (INC-03).

Rules:
- Wazuh is GPLv2: its ideas are re-implemented, its code, decoders and rules are not copied.
- Each item is a module (MOD-01): off by default unless it only reads data NSM already has, with its own *Guida*.
- No Wazuh server, indexer or endpoint agent is part of these items. Endpoint monitoring of customers' PCs and servers through the Wazuh agent was discussed with the operator (2026-10-24); on 2026-10-29 the operator declined it, so it is not planned.

#### SIEM-01 — configurable syslog rules with a rule tester

**Step 1 done (Core 0.49.305)**: *Sicurezza → Accessi → Regole syslog*; operator rules (expression with named fields, vendor/severity/topic, N-in-T grouped by device/address/user, chained rule, ignored sources, throttling, event/notification/Action Center), a tester on a pasted line, templates, audited changes; the built-in access alerts are listed read-only. Next: the built-in patterns as an editable default rule set.

**Step 2 done (Core 0.49.318)**: the built-in login patterns are listed read-only on the same page, each copyable into an operator rule prefilled with the built-in threshold, and the tester also shows what the built-in patterns read from the line.

Today the access patterns are fixed in the code. The operator gets rules editable from the portal:
- **match**: vendor, severity, program/topic, regular expression with named fields (user, source IP, interface…);
- **level** and the alert it raises (notification, Action Center issue, incident evidence);
- **correlation**: N matches in T minutes, grouped by device, source IP or user; a rule that fires only after another one (e.g. success after failures);
- **exceptions and throttling**: ignored sources (NSM itself, technicians' addresses), at most one alert per key in a window;
- **rule tester**: paste a log line (or pick one from a device's live log) and see which rules match, the extracted fields and the resulting level, before saving;
- the current built-in patterns become the default rule set, read-only and copyable; every change is audited and versioned.

#### SIEM-02 — threat-intelligence IP lists on access and firewall logs

Source addresses of failed and successful logins and of logged firewall drops are matched against IP lists:
- public lists, downloaded on a schedule: Spamhaus DROP/EDROP (shared with SEC-13), FireHOL level 1, abuse.ch;
- the CrowdSec community blocklist when CROWDSEC-01 is on, and the AbuseIPDB score of CYBER-01 when its key is set;
- **operator lists**: trusted addresses (NSM, technicians, monitoring) that never alert, and own blocklists;
- effect: a failed login from a listed IP raises the level; a **successful** login from a listed IP is critical; the list and the entry are shown in the alert as evidence;
- one shared list store with SEC-13 and CROWDSEC-01, no second downloader.

#### SIEM-03 — who changed the configuration

**Step 1 done (Core 0.49.303)**: MikroTik exports, legacy Agents included; NSM jobs/actions and syslog logins as evidence; the Agent scripts are left out of the comparison; the Action Center issue only for unknown changes or logins from public addresses. Next: the exact author from RouterOS `changed by` lines (needs a new Agent forwarding `system,info`), TR-069 and UISP changes.

Every configuration change NSM detects is attributed:
- **by NSM**: matched with NSM's own audit (Agent update, profile push, restore, firmware, TR-069 set), e.g. a legacy router's export that grew after the Agent 0.49.23 update because the previous Agent script is kept for rollback;
- **by an operator on the device**: from the device's log (RouterOS `… changed by <user> via winbox/ssh/web from <IP>`, the equivalent lines of the other vendors), with user, service and source address;
- **unknown**: no matching NSM action nor log line.
Sources: MikroTik drift between exports, TR-069 parameter changes, UISP/cnMaestro configuration changes. The Action Center alerts only on **unknown** changes and on changes from public or non-trusted addresses; the device history shows every change with its author.

#### SIEM-04 — active response, opt-in

When an access rule fires (brute force, listed IP), NSM can block the source on the affected MikroTik:
- RouterOS address list `nsm-block` with a timeout (e.g. 1 hour, doubled on repeat), and one input-chain drop rule installed once by the Agent;
- **off by default**, enabled per customer or device by the operator, with a preview of the change and audit of every block and unblock;
- never blocks NSM, the trusted lists of SIEM-02 or the device's management networks (ACCESS-01);
- the same mechanism as the optional blocklists of SEC-13 and CROWDSEC-01 (one address-list writer in the Agent, different list names).

#### SIEM-05 — MITRE ATT&CK and regulatory mapping

- Every alert type, rule (SIEM-01) and compliance control (COMP-01) carries MITRE ATT&CK tactics/techniques (e.g. brute force T1110, valid accounts T1078, exposed remote services T1133) and the requirements it evidences: NIS2 (Italy: D.Lgs. 138/2024, art. 21 measures), ISO/IEC 27001:2022 Annex A, GDPR art. 32.
- Filters by tactic and by framework in *Sicurezza* and the Action Center.
- A report per framework (REP-01…04): for each requirement, the NSM controls that evidence it, their state in the period and the open gaps. It supports the operator's own assessment and is not a certification.

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

### SLA-01 — customer SLA and planned maintenance (requested 2026-10-21)

**Implemented in `main`** (Core 0.49.181 and 0.49.182): monthly availability target per customer with downtime budget, verdicts, per-device outages, CSV export and the target used in customer reports; planned maintenance windows per customer or device excluded from availability, retroactive windows flagged. Map view: not wanted by the operator.

### LOG-02 — device logbook (requested 2026-10-21)

**Implemented in `main`** (Core 0.49.180): operator entries per device in the *Attività* tab, merged with recorded events, audited, never edited.

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

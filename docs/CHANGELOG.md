# Changelog

NSM Core follows `0.49.x`: every release promoted to `deploy` increases the
patch number in `app/app/entrypoint.py` (`APP_VERSION`, shown in the sidebar).
MikroTik Agent versions are independent (`mikrotik_agent_generation.py`).

## 0.49.90 — 2026-10-09

Monitoring — ping from NSM on the fleet page and for a whole customer
- **Operazioni → Monitoring** shows the ICMP state of every device in a *Ping da NSM* column (last RTT and loss, *non risponde*, *non attivo*).
  - Two new views: *Non rispondono al ping* (three silent rounds) and *Perdita pacchetti* (last round with at least 20% loss). They also count in *Da verificare*.
- **Bulk switch.** *Attiva su tutti* / *Disattiva su tutti* turns the ICMP monitor on or off for every device of the selected customer, or of all customers.
  - Devices without a management IP are skipped and counted, with a hint to set the address on the device page.
  - Audited as `ICMP_MONITOR_BULK`.

## 0.49.89 — 2026-10-09

Monitoring — every MikroTik interface can be monitored (fix requested by the operator)
- **Bug.** On some routers only the bridges could be picked as monitored interfaces; no Ethernet LAN ports, no PPP, nothing else. The agent collector read the interfaces in two groups, each inside a single `:do on-error`. One interface whose counters could not be read stopped its whole group. It also skipped ports without link and all dynamic interfaces (PPP server sessions), and its size cap was small.
- **Agent 0.49.22** reads each interface in its own `:do on-error`, in this order:
  1. WAN clients;
  2. Ethernet ports, also without link;
  3. every other static interface (VLAN, bridge, wireless, tunnels, bonding);
  4. active dynamic interfaces (`<pppoe-user>` sessions).
  
  Caps rise to 2400 characters (legacy header) and 12000 (modern). NSM keeps up to 128 interfaces. The agent reaches routers through the automatic update.
- **Picker.** The *Interfacce monitorate* list groups each interface (WAN, Ethernet, VLAN, Bridge, Wireless, Tunnel, Sessione PPP, Altro) and orders them that way.

## 0.49.88 — 2026-10-09

Diagnostics — download as JSON evidence (MTK-05)
- Every completed diagnostic (support snapshot, ping, traceroute, neighbors, DHCP, logs) has a **Scarica JSON** button on its result page. Use it to attach the snapshot to a ticket.
- The file contains the device identity (name, identity, model, serial, RouterOS, management IP), the job (type, parameters, status, times, error) and the stored result.
- The file's SHA-256 is returned in `X-Content-SHA256` and recorded in the audit event `DIAGNOSTIC_EXPORTED`. Read permission is enough, within the user's customer scope.

## 0.49.87 — 2026-10-09

Customer and site graphs — latency, loss and MikroTik radios
- The *Grafici* tab of a customer (or one site) adds:
  - **Latenza (ICMP da NSM)**: average and worst round-trip time of the devices with the ICMP monitor;
  - **Perdita pacchetti**: loss per slot over all their rounds.
- **Segnale radio** now also includes the MikroTik radios read by the agent (worst client of each AP), next to UISP and cnMaestro.

## 0.49.86 — 2026-10-09

Monitoring — wireless signal of MikroTik radios (MON-01)
- **Agent 0.49.21** (modern, legacy 7.12 and RouterOS 6) reads the wireless registration table at every heartbeat:
  - `/interface wireless` (RouterOS 6 and the v7 *wireless* package): signal and CCQ;
  - `/interface wifi` (RouterOS 7.13+ *wifi* package): signal.
- **Routers without a radio are safe.** RouterOS refuses to load a script that names a menu of a missing package. Both readers are therefore compiled at run time with `:parse` inside `:do on-error`, and RouterOS 6 gets only the *wireless* reader.
  - A router without radios sends `v1;`, and nothing is stored.
- **Per interface.** NSM aggregates per heartbeat: registered peers (one on a CPE), min/avg/max signal and average CCQ (`device_wireless_samples`, migration 0040; 90-day retention; 10-minute consolidation after 7 days).
- **Monitor page.** New *Segnale wireless* panel with an interface selector:
  - signal chart (dBm): a single line on a CPE; average with best and worst client on an AP;
  - connected clients and CCQ chart;
  - current value in the header.

## 0.49.85 — 2026-10-09

Monitoring — latency and packet loss measured from NSM (MON-01, any vendor)
- **ICMP from the NSM server**, like the Zabbix/Cacti ping item. Enabled per device, it sends 3 echo requests every 2 minutes to the management IP, or to an address you choose (IPv4/IPv6; loopback, link-local and multicast refused).
  - It works for every manufacturer, with or without agent, when NSM reaches the address (public IP, VPN or management network).
  - It uses Linux unprivileged ping sockets, which Docker enables by default, so the worker keeps `cap_drop: [ALL]`.
- **Charts.** New *Latenza e perdita* panel on the MikroTik Monitor page and on the overview of other devices:
  - round-trip time (average area, min/max lines, legend table) and packet loss %;
  - ranges 1h/24h/7d/30d.
- **Alerts.** Three consecutive rounds without reply open *Apparato non raggiungibile da NSM (ICMP)* in the Action Center, with a notification. The next reply closes it, and so does disabling the monitor.
- **Storage.** `device_ping_samples` (migration 0039), 90-day retention; the consolidation after 7 days averages RTT and sums packets.
- If the host does not allow ping sockets, the panel says so and nothing is stored. `ICMP_MONITOR=0` turns the feature off.

## 0.49.84 — 2026-10-09

Documentation — capabilities and roadmap brought in line with the code (audit of half-done work)
- `IMPLEMENTED_CAPABILITIES.md` still said legacy backup was not implemented and did not mention the newer agent features. It now covers:
  - automatic and legacy self-update, observed policies, 2-minute heartbeat, several jobs per heartbeat, the Agent tab capabilities and the RouterOS 6 onboarding command;
  - the FTP backup, the legacy support snapshot, interface errors and drops, and the consolidation.
- `ROADMAP.md`: MTK-02 (legacy backup) marked done; MTK-03 report integration done; MTK-05 legacy support snapshot done.

## 0.49.83 — 2026-10-09

Monitoring — interface errors and drops (MON-01)
- **Agent 0.49.20** (modern, legacy 7.12 and RouterOS 6) adds the RouterOS `rx-error`, `tx-error`, `rx-drop` and `tx-drop` counters to every heartbeat. It sends only interfaces with a non-zero counter, so the payload stays small (`metrics.iferrs` / `X-NSM-Iferr`). It reaches paired routers through the automatic update.
- **Stored per sample.** NSM stores the errors and drops of each monitored interface in the interval since the previous sample (migration 0038). Counter resets are ignored.
  - Consolidation after 7 days sums them per 10-minute slot.
- **Monitor page.** New *Errori e drop* chart under the traffic graph (Cacti/Zabbix style), with the totals for the selected range. Devices with an older agent show when the data will arrive.
- The traffic API returns `rx_errors`, `tx_errors`, `rx_drops`, `tx_drops` per point and `stats.errors`.

## 0.49.82 — 2026-10-09

MikroTik Agent — legacy agents run several jobs per heartbeat; support snapshot on legacy (audit of half-done work)
- **Several jobs per heartbeat.** **Legacy Agent 0.49.19** polls again after each completed job, up to 8 jobs per heartbeat. Queued operations (snapshots, diagnostics, syslog, backup) no longer wait one heartbeat each. It reaches paired routers through the automatic update.
- **Support snapshot on RouterOS 6.48/6.49 and 7.12.** Before, the request failed on legacy agents ("non ancora disponibile"). For agents with structured snapshots (0.49.3+), NSM now splits it into the read-only sections the agent already runs: resources, addresses, routes, interfaces, PPP, DHCP, logs.
  - It assembles them in the modern support snapshot shape (50 rows per table, like the modern agent).
  - A section that never arrives is listed as missing when the snapshot expires; the snapshot still closes with what it has.
- The *Support snapshot* card in Diagnostics is shown for those legacy agents too.

## 0.49.81 — 2026-10-09

MikroTik Agent — capabilities and texts that match what legacy agents do (audit of half-done work)
- **Agent tab, *Capability effettive*.** Four new rows, for modern and legacy agents:
  - *Riavvio remoto* and *Aggiornamento RouterOS*: from the privilege profile; a read-only legacy agent is told to reinstall once;
  - *Configurazione syslog*: from the agent eligibility;
  - *Aggiornamento agent automatico*: from the update policy.
  
  The backup row names the real method (for example *Agent MikroTik legacy (FTP)*), no longer always "Backup HTTPS". On RouterOS 6 the diagnostics row says traceroute is not available.
- **Legacy backup messages.** Export-only and not executable no longer say "the binary backup requires RouterOS 7.13+". They now say what the full FTP backup needs: the receiver (`./manage.sh legacy-backup-enable`), agent 0.49.16+ and the policies.
- **Other texts.** The Agent fleet filter reads *Legacy 6.x / 7.12*. The workspace legacy note covers RouterOS 6.48/6.49. ROADMAP MON-01 now reflects the 2-minute sampling and the consolidation.

## 0.49.80 — 2026-10-09

Onboarding — command for RouterOS 6.48/6.49
- **Fix.** The guided pairing command refused anything but RouterOS 7 and contained v7-only syntax (`/system/device-mode/get`). RouterOS 6 rejects a whole command line that contains it. So routers with 6.48/6.49 could not be added from the panel, although NSM installs the RouterOS 6 legacy agent.
- **Change.** The onboarding panel shows a second command, *Router con RouterOS 6.48 / 6.49*, using the same one-shot token. It checks the version (and DNS when NSM uses a hostname), then fetches the same bootstrap.
- The RouterOS 7 command, verified on real routers, is unchanged.

## 0.49.79 — 2026-10-09

Syslog — configured automatically when a MikroTik is added through the agent (requested by the operator)
- **At enrollment.** When a MikroTik completes the agent pairing, NSM queues the syslog configuration right away. This covers modern agents, legacy 7.12 and RouterOS 6. The agent applies it at its first heartbeat, seconds after the bootstrap:
  - logging action `nsm` toward NSM, with topics critical, error, warning and account;
  - the device key as prefix, so its logs are accepted from the first line.
- **Default on.** The *Configura automaticamente* setting (Amministrazione → Syslog) is already enabled by default and now also covers enrollment. Turning it off stops both.
- **NSM address.** Without a configured IPv4 address, enrollment uses the address the router used to reach NSM. When neither is usable, the device records `SYSLOG_AGENT_CONFIG_SKIPPED` with the reason.
  - Loopback, unspecified and link-local addresses are never sent to routers.
- **Fix.** The worker's periodic auto-configuration skipped legacy agents, because the worker does not load the web wiring. It now applies the legacy eligibility too.
- The onboarding panel and the Syslog settings say that syslog is configured right after pairing.

## 0.49.78 — 2026-10-09

MikroTik Agent — heartbeat every 2 minutes (requested by the operator)
- **New installations** schedule the agent every **2 minutes** instead of 5. Jobs (reboot, syslog, diagnostics, updates) start within 2 minutes; graphs and offline detection are finer.
- **Installed agents.** **Agent 0.49.18** aligns its own scheduler to 2 minutes at every run, and reaches paired routers through the automatic update. Read-only agents cannot change the scheduler and keep 5 minutes.
- **Server load.** A heartbeat is one small POST; legacy agents add one job poll. 300 routers make about 2.5 requests per second.
- **Storage.** Telemetry is consolidated like an RRD archive (`telemetry_rollup.py`, hourly). Full 2-minute resolution is kept for 7 days. Older CPU/memory and traffic samples become one 10-minute point (average of CPU and bit/s, newest counters and memory).
  - Over the 90-day retention a series stores about 17,000 points instead of the 25,900 of the 5-minute heartbeat.

## 0.49.77 — 2026-10-09

MikroTik Agent — legacy agents update themselves (requested by the operator)
- **Self-update on RouterOS 6.48/6.49 and 7.12.** Legacy **Agent 0.49.17** runs the `agent_self_update` job, like modern agents do. Paired routers get new versions without a new token.
  - The router downloads the source NSM generates for that device.
  - It checks that the download ends with the expected marker (no truncation) and that RouterOS parses it (`:parse`).
  - It keeps the old script as `nsm-agent-heartbeat-prev`, then replaces its own script. The new version runs at the next heartbeat; NSM verifies it from the reported version.
  - An agent that answers without running the handler counts as a failure, and nothing changes.
- **Automatic updates.** The worker queues the update for online agents that can apply it: modern 0.49.0+, and legacy 0.49.17+ with the `write` policy.
  - One update at a time. After a failure, at most one attempt a day.
  - `AGENT_AUTO_UPDATE=0` turns it off.
- **7.12 → 7.13+.** When a router with a legacy agent moves to RouterOS 7.13+, the update installs the **modern agent**. The first modern heartbeat switches the transport (`MIKROTIK_AGENT_TRANSPORT_SWITCHED`).
- **Observed permissions.** The legacy agent reports the RouterOS policies of its script (`X-NSM-Agent-Policy`). NSM derives the profile from them (`legacy-ops-v1` or `legacy-read-v1`).
  - FTP backups also require `ftp`, `policy` and `sensitive`. Agents installed before 0.49.16 lack the last two.
  - RouterOS does not let a script raise its own policies. Read-only agents, and legacy agents before 0.49.17, need **one last reinstall**; after that, updates are automatic.
- **Agent tab.** New *Aggiornamento agent* panel: installed and latest version, automatic or one-time reinstall, last update state, RouterOS permissions, and an *Aggiorna ora* button.

## 0.49.76 — 2026-10-09

Inventory — onboarding through UISP and cnMaestro (requested by the operator)
- **Cambium is its own vendor** in *Nuovo apparato*: *Cambium (cnMaestro)*. Before, it was one of the generic manufacturers; it is removed from the manual list.
- **Ubiquiti (UISP) and Cambium (cnMaestro)**: the device must **already exist in the console**. NSM looks it up by **MAC or serial** when the device is created.
  - **Found once**: the device is created already linked. Name, model, serial, firmware, IP and status come from the console; for Cambium also network and tower, with statistics from the cnMaestro sync. Audited (`UISP_DEVICE_ONBOARDED`, `CNMAESTRO_DEVICE_LINKED`).
  - **Not found, ambiguous, connector missing or unreachable**: the device is **not created**, and the message says to add it to the console first.
- Ubiquiti accepts the serial as an alternative to the MAC. Customer and Site stay those chosen in NSM.

## 0.49.75 — 2026-10-09

Backup — full backups for legacy RouterOS (6.48/6.49 and 7.12) through an FTP receiver (requested by the operator)
- **What changes.** Old RouterOS cannot post a large file from a script (7.12 reads at most 60 KB, RouterOS 6 only 4 KB). The **legacy Agent 0.49.16** now uploads the **encrypted binary backup** and the **.rsc export** with `/tool fetch upload=yes mode=ftp` to an upload-only FTP receiver of NSM. Before, 7.12 had the export only up to 60 KB and RouterOS 6 had no backup.
  - **Archive.** Files are archived like every other backup: run, artifacts, SHA-256, audit, retention. Legacy devices become **protetti** in backup coverage.
  - **Encryption.** The binary backup is encrypted with the job's backup password, kept encrypted in NSM.
  - **Secrets.** On RouterOS 6 the export uses `hide-sensitive`.
- **Receiver** (`app/legacy_ftp_server.py`, Docker profile `legacyftp`, `./manage.sh legacy-backup-enable <IPv4 of NSM>`):
  - one-time account per job; only the password's SHA-256 is stored; three wrong passwords close the connection;
  - only the two expected files of the job can be stored, once each, up to 64 MB; no listing, download or other commands;
  - passive mode only; the data connection must come from the router's address;
  - port 2121 and passive 30100–30109. Open them only from the routers' networks.
- **Agent permissions.** The `legacy-ops-v1` profile gains the `policy` and `sensitive` policies needed by `/system backup save`. Legacy agents must be reinstalled (0.49.16+) to use this.
- `manage.sh` adds Compose profiles without overwriting the existing ones (`acs` and `legacyftp` together).

## 0.49.74 — 2026-10-09

Syslog — only lines carrying the device key (operator decision)
- **Key mandatory, by default and with no exceptions.** The receiver stores a line only when it carries the `NSM-<key>` of a known device: MikroTik logging prefix, Cisco origin-id, or device name for other vendors.
  - Every line without a key is discarded (reason *riga senza chiave NSM*). This also applies to lines from the device's own address or from a single, unambiguous address.
  - An unknown key is discarded as before.
- **Removed.** Attribution by source address and hostname, the per-device *Accetta solo/anche log senza chiave* switch, the *Hostname atteso* field, and the association of discarded senders to a device. In *Mittenti scartati* an address can only be removed from the list.
- **Allowed networks stay** as a first filter before the key is read.
- **The Syslog tab and the admin page** say that only lines with the key are accepted. They also say what to do when a MikroTik has not confirmed the key yet: configure syslog with the agent, or apply the manual commands.

## 0.49.73 — 2026-10-09

Security — exposure check also on private and CGNAT addresses (requested by the operator)
- **Private or CGNAT address as target.** In the *Esposizione* tab the operator can now give a private (RFC 1918) or CGNAT (100.64.0.0/10) address for devices behind the customer's NAT or reached through a VPN or management network. Before, only public IPs were accepted.
- **Warning on the page.** When the address is not public, a visible warning explains when the result is valid. The device must have direct access to the WAN, not sit behind another router or NAT, and the NSM server must reach it on that address. Otherwise the check is distorted: the upstream router answers, or nothing does.
- **Warning on the results.** Results obtained on a non-public IP carry a note on the device page and in *Security → Esposizione*.
- **Still refused.** Loopback, link-local, multicast and reserved addresses. The management IP is used automatically only when it is public; a private one must be confirmed by the operator.

## 0.49.72 — 2026-10-09

Monitoring — graphs per customer and per site (MON-01)
- **New *Grafici* tab on every customer**, for the whole customer or a single site (chips, and a *Grafici* button on each site row).
- **Charts** (Cacti/Zabbix style, 24h / 7d / 30d / 90d):
  - **Traffico WAN aggregato**: the sum of in/out bit/s of the monitored interfaces of the devices in scope. These are the WAN/PPPoE interfaces chosen on each device's Monitor page, so LAN traffic is not counted twice.
  - **Apparati con telemetria**: how many devices sent data in each time slot; a drop is an outage.
  - **CPU apparati**: average and maximum.
  - **Segnale radio**: average and worst, from UISP and cnMaestro.
- **How it is computed.** Slots are computed in SQL. A slot without data is a gap, never a zero.
- **Tables.**
  - *Apparati con più traffico* (last 24 hours), with a link to the device traffic graph.
  - For the whole customer, *Confronto sedi*: devices, online, average in/out and peak per site.
- The renderer gains an integer *count* unit for device counts.

## 0.49.71 — 2026-10-08

Administration — users limited to some customers (delegated administration)
- **What it is.** In *Amministrazione → Utenti*, each non-admin user has **Clienti visibili**. With no selection the user sees all customers, as before. With a selection the user sees only those customers.
- **Central enforcement.** While a limited user is served, every ORM query of the request is filtered (SQLAlchemy `do_orm_execute` + `with_loader_criteria`). It applies to:
  - the assigned customers;
  - every row with a `customer_id` of those customers (devices, sites, incidents, issues, notifications, audit, reports…);
  - every row of their devices (backups, jobs, telemetry, logs…).
- **What changes for the user.** Lists, counters, dashboard, search, Security and Monitoring pages, reports and APIs change accordingly. URLs of other customers' objects answer 404 (pages and actions).
- **Who is not filtered.** Administrators are never limited. Worker and agent endpoints are never filtered, because the filter is bound to the user's request.
- **New customers.** A customer created by a limited user is added to that user's scope.
- **Audit.** Changes are audited (`USER_CUSTOMER_SCOPE_CHANGED`). Migration `0037`.

## 0.49.70 — 2026-10-08

Integrations — cnMaestro for Cambium radios (VEND-02, step 1)
- **New connector *Integrazioni → cnMaestro*** (On-Premises or Cloud, read-only).
  - **Connection.** URL, client id and client secret of the cnMaestro *API Client*, with OAuth2 client credentials. The secret is encrypted. Test, *Sincronizza ora*, and automatic sync every 15 minutes.
  - **Linking.** NSM devices whose manufacturer is *Cambium Networks* are linked by MAC and, without a MAC, by serial. NSM then updates the model, firmware, management IP (only when missing), status and last seen. Customer and Site stay those of NSM. Linking is audited.
  - **Statistics.** Signal, SNR, downlink/uplink throughput, CPU, memory, connected SMs/clients and uptime. They are read defensively, because the field names differ between ePMP, PMP 450 and cnPilot.
  - **History.** Kept 90 days, with at most one sample every 4 minutes. The last sample of an offline device is kept.
- **Device *cnMaestro* tab.** Link state, network/tower, current values and Cacti-style graphs (signal, SNR, throughput, CPU/memory, stations).
- **Integrations hub.** A *cnMaestro (Cambium)* card. The connector is also in the global search.
- Endpoints follow the cnMaestro API v1; field names must be confirmed on a real cnMaestro. Migration `0036`.

## 0.49.69 — 2026-10-08

Firmware — catalogs for the other manufacturers (VEND-01)
- **New page *Firmware → Cataloghi altri produttori*.** There is one catalog per brand present in the inventory; a brand without devices has no catalog. MikroTik keeps the RouterOS catalog.
- **Ubiquiti** is filled automatically from official sources:
  - the latest version that UISP reports for each model;
  - Ubiquiti's public update service (`fw-update.ubnt.com`) for the airOS platforms found in the inventory (`XC`, `WA`, `XW`…, read from the installed firmware string). It is refreshed daily and on demand; to be confirmed from the deployed instance.
- **TP-Link, Tenda, Cambium, Huawei and the other brands.** Operators enter releases from the official download page, which is linked on the page: version, models (pattern such as `archer c6*`), date, link to the release notes, and whether the release fixes security issues. NSM never estimates versions.
- **Evaluation.** Every device of the brand is compared with the newest release for its model or platform. The state appears in the firmware worklist: *update available*, *security update* or *current*. Devices managed by UISP keep the UISP view.
- **Audit.** Adding and deleting releases is audited. Migration `0035`.

## 0.49.68 — 2026-10-08

Ubiquiti — operations through UISP (UBNT-08, step 2)
- **Riavvio.** Sent to UISP after typing `RIAVVIA` (permission *firmware.execute*). NSM records the uptime before the request and verifies the reboot on the device uptime: *verificato* when it restarts, *non verificato* after 20 minutes. Audited.
- **Backup della configurazione.**
  - **How it works.** UISP creates the backup; NSM downloads the new file and archives it like the MikroTik exports (BackupRun + artifact, SHA-256, audit), in the device backup archive.
  - **When.** On demand (*Esegui backup ora*), or by policy when the policy enables *Configurazione dal connector*.
  - **Protection.** Ubiquiti devices linked to UISP with that option now count as **protetti** in backup coverage.
- **Interfacce.** Name, type, state (disabled or unplugged), speed, MTU, rx/tx and errors, read on demand.
- **Eventi UISP.** The last 50 device events.
- **Errors.** Reboot and backup need a UISP token with write permission. 401/403 and 404 answers are explained, and a failed backup is recorded with its reason.
- Endpoints follow the UISP API v2.1 documentation; field names must be confirmed on a real console.

## 0.49.67 — 2026-10-08

Syslog — automatic configuration also with the legacy agent (RouterOS 7.12 and 6.x)
- **MikroTik Agent 0.49.15.** The legacy agent installed with the `legacy-ops-v1` profile configures remote syslog like the modern one:
  - action `nsm` toward the NSM receiver;
  - topics critical, error, warning and account;
  - the device key `NSM-<key>` as prefix.
- **How to use it.** From the *Syslog* tab (*Configura syslog con l'agent*), for all devices from *Amministrazione → Syslog*, or automatically.
- **Requirements.** `/system logging` needs the `write` policy, so read-only legacy agents (`legacy-read-v1`) and agents older than 0.49.15 must be reinstalled from the Agent tab. Until then the Syslog tab shows the manual commands, already with the key.
- **Safety.**
  - A legacy agent without the handler answers "success" with no output; that answer is recorded as a failure.
  - Strict mode is enabled only after the router confirms the configured key.

## 0.49.66 — 2026-10-08

ACS / TR-069 — fixes found in the review of unfinished work
- **The device overview no longer says "Gestione tramite ACS non ancora disponibile".** GenieACS is integrated. TR-069 devices show an *ACS / TR-069* panel: GenieACS ID, last Inform and last check, or what to do to complete the association.
- **TR-069 for every brand.** The *ACS* tab and the GenieACS association were limited to the vendor *TP-Link / TR-069*. They are now also available for Huawei, ZTE and other CPEs entered as *Altro produttore* (types router, ONT, wireless CPE, other) and for any device already managed through TR-069. Switches, APs and OLTs do not get the tab.

## 0.49.65 — 2026-10-08

Monitoring — Cacti/Zabbix-style graphs everywhere (reported by the operator)
- **One renderer for every time-series graph** (`static/rrd_chart.js`). Before, only interface traffic looked like Cacti/Zabbix; the CPU and memory graphs of the Monitor page and the UISP graphs still used the old drawing (tiny labels, points spread by index instead of time, no statistics).
  - **Time axis.** A real time axis with ticks on round times (minutes, hours, days, depending on the range and the width).
  - **Gaps.** Missing samples show as gaps instead of interpolated lines.
  - **Readability.** Labels sized to the screen, and a dashed grid.
  - **Hover.** A crosshair with a tooltip showing every series.
  - **Legend.** A Cacti-style table under each graph: *Attuale / Min / Media / Max*.
- **CPU and memory**: filled areas on a fixed 0–100% scale.
- **Traffic**: inbound as an area, outbound as a line, and the 95th percentile as a dashed line.
- **UISP**: signal in dBm drawn as a line with its own scale; capacity and resources with the legend.

## 0.49.64 — 2026-10-08

Syslog — fix: device key in the manual configuration (reported by the operator)
- **The commands in the *Syslog* tab now carry the device key.** Before, they had no identifier, so a device configured by hand was recognised only by IP.
  - **MikroTik**: every `/system logging add` line has `prefix=NSM-<key>`. The same key is set by the agent's automatic configuration.
  - **Cisco IOS**: `logging origin-id string NSM-<key>`.
  - **Other vendors**: put `NSM-<key>` in the device name (hostname / device name, e.g. `CPE-Rossi-NSM-<key>`). The receiver finds the key anywhere in the line, hostname included.
- **Key created when the tab is opened**, stable afterwards. It is shown in the tab header.
- **Manual strict mode.** For devices configured by hand, *Accetta solo log con la chiave* discards the lines without the key from that device. It is audited (`SYSLOG_STRICT_CHANGED`). MikroTik devices with the agent keep the automatic strict mode.
- The admin page shows the commands with the placeholder `NSM-<chiave del dispositivo>`.

## 0.49.63 — 2026-10-08

Reports — delivery (REP-04)
- **External recipients for scheduled reports.** A schedule can have up to 20 external e-mail addresses (management, auditors, the customer), set when it is created or changed later in *Report*. Each generated report is sent to them as an attachment through the same outbox, with the same retries.
- **Delivery evidence.** Every report delivery leaves an audit event, `REPORT_DELIVERED` or, after the last attempt, `REPORT_DELIVERY_FAILED`, with the report, recipient, channel and error. The evidence stays with the audit log, not with the outbox retention.
- Invalid addresses are refused, and changes to the recipients are audited (`REPORT_SCHEDULE_RECIPIENTS_CHANGED`). Migration `0034`.

## 0.49.62 — 2026-10-08

Integrations — Zabbix (ZBX-01, step 2)
- **Zabbix problems shown in NSM.** Every 5 minutes the worker reads, with one `trigger.get` call, the triggers in PROBLEM state on the hosts NSM pushed (monitored, not dependent, Zabbix 5.0 – 7.x).
  - **Device.** The header shows a badge *N problemi* with the worst severity, on every tab. The overview lists the problems: severity, name, since when, acknowledged or not.
  - **Monitoring.** A *Problemi Zabbix* panel lists the devices with open problems, worst first, with the count still to acknowledge.
  - **Integrazioni → Zabbix.** Shows the open problems and when they were last read.
  - **When Zabbix cannot be reached**, the last list is kept and marked as not updated. Devices are rewritten only when their problems change.

## 0.49.61 — 2026-10-08

Security — exposed services across the fleet (SCAN-01, step 3)
- **New page *Security → Esposizione*.** Every device with its exposure state:
  - **Con servizi esposti** (default view): sorted by severity, with the exposed services and port forwards;
  - **Da configurare**: no public IP to check, or the agent needs an update. The table says what is missing;
  - **Senza esposizioni**, and **Tutti**.
  - KPIs (exposed, critical, clean, to configure, pending), a customer filter, and a link to each device's *Esposizione* tab. The page is also in the global search.
- **Security newsletter.** Devices whose *Servizi critici esposti sulla WAN* issue opened in the period are listed with their services and the open total.
  - When there are no new CVEs, the newsletter is still sent for the new exposures.
- **Evidence report.** Section 3 *Vulnerabilità* adds the current exposure state of the devices in scope: counts and a table of the exposed devices with their services, severity and verification method. Section numbers do not change.

## 0.49.60 — 2026-10-08

Security — exposure check (SCAN-01, step 2)
- **Check from outside for every manufacturer.** Devices without the MikroTik Agent check (Ubiquiti, TP-Link, Cambium, generic devices, and MikroTik without the modern agent) are checked from the NSM server on their public IP.
  - **Target.** The IP given by the operator in the *Esposizione* tab (WAN/PPPoE address or public IP of the customer NAT) or, when it is public, the management IP. Private, CGNAT and reserved addresses are refused and never probed.
  - **Ports.** A fixed list per manufacturer, no port sweep:
    - common: FTP 21, SSH 22, Telnet 23, web 80/443/8080/8443, DNS 53 (open resolver), SNMP 161 (community `public`), SSDP 1900;
    - TR-069 7547 on CPE brands (TP-Link, Huawei, ZTE, Tenda, …);
    - Winbox 8291, API 8728/8729 and bandwidth-test 2000 on MikroTik;
    - Ubiquiti discovery 10001/udp.
  - **Probes.** One TCP connection per port. On UDP, one harmless request: a recursive DNS query, an SNMP read of `sysDescr.0`, an SSDP search, a UBNT discovery.
  - **When.** When the device is added, then every 24 hours, and with *Verifica ora* (at most once every 10 minutes). At most 4 devices per worker cycle. Each check is written to the audit log.
  - **Results.** They use the same tab, Action Center issue and *security* notification as the MikroTik check.
  - **Shared IP.** When the IP is shared (customer NAT), the result is shown, but no issue is opened on the device behind the NAT, because the answer comes from the edge router.
  - **Configuration.** `EXPOSURE_EXTERNAL_CHECK=0` disables the check. The check runs from the NSM server, so when the server is inside the same network, the result can differ from what the Internet sees.

## 0.49.59 — 2026-10-08

Security — exposure check
- **RouterOS raw firewall analysed** (requested by the operator).
  - **MikroTik Agent 0.49.14** adds `/ip firewall raw` to the firewall snapshot.
  - **Order.** The `raw/prerouting` table runs before connection tracking and the filter, so NSM evaluates it first:
    - a raw `drop` for traffic from the WAN (also on the service port) makes the service *protetto*, even when the filter would accept it;
    - raw `accept`/`notrack` only end the raw table, so the filter decides;
    - drops limited to source lists (blacklists) do not protect;
    - jumps or unknown interface lists make the verdict *da verificare*.
  - **Port forwards.** A forward whose public port is dropped in raw (before `dst-nat`) is shown as *protetto (raw)* and does not raise the alert.
  - **Page.** The Esposizione tab shows how many filter and raw rules were read. The "no drop" warning also considers raw/prerouting.

## 0.49.58 — 2026-10-08

ACS / TR-069
- **Integrated GenieACS linked automatically.**
  - When the integrated stack is enabled (`acs-enable`, `GENIEACS_INTERNAL_NBI_URL`) and no GenieACS is configured, NSM connects by itself to `http://genieacs-nbi:7557` on the internal Docker network. No URL needs to be typed.
  - An external GenieACS configured by the operator is never replaced; the form on the page is now *GenieACS esterno (NBI)*.
  - When the integrated stack is not active, the page says how to enable it.

## 0.49.57 — 2026-10-08

Operations
- `manage.sh` run from the Git clone (e.g. `~/nsm-platform`) now switches to the installation directory (`PLATFORM_DIR`, default `/srv/network-platform`). Before, commands such as `acs-enable` failed with `secrets/bootstrap.env: file not found`.

## 0.49.56 — 2026-10-08

ACS / TR-069
- **New *ACS* tab in Amministrazione.**
  - **Apri pannello ACS** opens GenieACS through Caddy on port 7080 (`ACS_UI_PORT`).
  - Caddy lets through only NSM administrators with a valid session (`forward_auth` to `/internal/acs-ui/auth`); others are sent to the NSM login or refused. GenieACS keeps its own login.
- **CPE setup guide**: TP-Link CWMP settings (ACS URL, periodic inform, connection request), network notes, verification in the panel, association in NSM, and authentication with `cwmp.auth`.
- **Base provisioning templates**: one button creates or updates in GenieACS, through the NBI:
  - the provision `nsm-base` (5-minute periodic inform; refresh of device info and WAN/PPPoE address; TR-098 and TR-181);
  - the presets `nsm-tplink` and `nsm-all-cpe`.

## 0.49.55 — 2026-10-08

Shared addresses (customer NAT) across services
- **Device header.** A device whose management IP is shared with other devices shows the badge *condiviso (N)*, listing the other devices.
- **Port forwards (Esposizione).**
  - From the MikroTik NAT rules already in the firewall snapshot, NSM finds the `dst-nat` rules that publish internal addresses on the WAN.
  - On the router: rule number, public port, destination and sensitive services (HTTP, SSH, Telnet, RDP, Winbox, SNMP…). Forwards of management services or of all ports raise the *Servizi critici esposti* alert.
  - On the device behind the NAT: *Raggiungibile da Internet tramite port forward*, which is the only way it is actually exposed.
- **Zabbix.**
  - Hosts sharing one address get the tag `nsm_shared_ip` and are listed in the sync result, since Zabbix on that address reaches only the edge router.
  - *Integrations → Zabbix* lists them with an *IP per Zabbix* per device (for example the LAN IP via VPN or Zabbix proxy), which is used instead of the management IP.

## 0.49.54 — 2026-10-08

Syslog — server hardening
- **Dedicated database account.**
  - `update.sh` generates `SYSLOG_DB_PASSWORD`; the `migrate` service creates and refreshes the role `nsm_syslog` on every deploy (`app/db_roles.py`).
  - The receiver can only insert and read log lines and access events, update the counters of discarded senders, and read devices and settings.
  - Users, backups, credentials and the rest of the database are out of reach.
- **Hardened container.**
  - New launcher `app.syslog_main`; filesystem read-only (`/tmp` in memory); limits of 512 MB RAM, 1 CPU and 128 processes; non-root user without capabilities.
  - Secrets the receiver never needs (`APP_SECRET_KEY`, `ENCRYPTION_MASTER_KEY`, …) are removed from its environment.
- **Host firewall.**
  - `./manage.sh syslog-firewall` generates the `DOCKER-USER` rules from the allowed networks: the published ports bypass ufw, so these rules are the only effective filter. `--apply` applies them.
  - The script refuses to run while no network is configured.
  - The admin page shows the generated rules.
- **TLS (RFC 5425)**: optional listener on 6514 when a certificate and key are mounted (`SYSLOG_TLS_CERT`, `SYSLOG_TLS_KEY`), for devices that support it.
- **Log integrity.**
  - Warning, error and critical lines are chained with SHA-256 per device (migration 0033); the worker writes the chain head into the audit log every day.
  - **Verifica integrità** in the device Syslog tab shows the first altered line, or a whole chain that no longer matches the audit anchor.
  - The incident PDF reports the integrity of the logs it quotes.

## 0.49.53 — 2026-10-08

Syslog — certain identification (decided with the operator)
- **Per-device syslog key.** With **MikroTik Agent 0.49.13** the router logs with the prefix `NSM-<key>` (a per-device key, stripped before storing).
  - Lines are recognised with certainty even when the WAN address changes, when several routers share the customer's NAT, or when routers sit in the same network.
  - Agents configured before the update are reconfigured once automatically.
- **Strict mode** (default on). Once the router has confirmed its key, keyless lines from its address are discarded, which blocks spoofing. On an address shared with a strict router, a keyless line is accepted only when its hostname matches exactly one other device.
- **Allowed networks.** The admin lists CIDR networks and packets from elsewhere are discarded before being read. While the list is empty, only addresses currently tied to a device are accepted, and a warning asks to configure it.
- **Certain or discarded.** Without a key the address must lead to exactly one device:
  - addresses learned from the agent count only while it sends heartbeats (30 minutes), so an address returned to the ISP pool no longer points at our router;
  - on shared addresses the hostname must match exactly one device; other vendors have an *expected hostname* in the Syslog tab;
  - lines that cannot be attributed are **discarded and not stored**, and the old "accept unknown senders" option is gone;
  - only counters and the reason remain (network, key, strict, ambiguous, unknown, rate, quota), never the content;
  - migration 0032 deletes lines stored without a device and the content samples of unknown senders.
- **Limits.**
  - Global rate limit of 2,000 lines per second and a daily quota of 200,000 lines per device.
  - Rate tables capped (most recently used kept) and the unknown-sender table capped, so spoofed traffic cannot exhaust memory nor lock out real devices.
- **Pages.**
  - The admin page shows lines discarded by reason, the allowed networks and strict mode.
  - The device Syslog tab shows strict mode, the expected hostname and the addresses tied with certainty.

## 0.49.52 — 2026-10-08

Security
- **Exposed services on the WAN, MikroTik** (SCAN-01 step 1, as decided: the Agent checks the router itself).
  - **Collection (Agent 0.49.12).** New read-only snapshot section `services`: `/ip service` (with custom ports and allowed addresses), remote DNS, SNMP (only whether the default `public` community is enabled, never the community names), SOCKS, web proxy, bandwidth-test, UPnP and MAC-Winbox.
  - **Evaluation.** NSM evaluates each active service against the firewall `input` rules in order, for traffic arriving on the WAN (PPPoE/LTE/tunnel clients, the interface with a public IP, interface lists such as `WAN` or `!LAN`). The verdict is *esposto*, *limitato* (allowed source addresses or lists), *protetto* or *da verificare* (custom lists, jump), with severity: Telnet, FTP, SOCKS and proxy are critical; Winbox, plain API, HTTP, open DNS resolver and SNMP with `public` are high. NSM warns when the input chain has no drop rule.
  - **When it runs.** As soon as the device is connected (agent 0.49.12+), then every 24 hours, and on demand from the new **Esposizione** tab of every device. No packet is sent to the device.
  - **Alerts.** Critical or high services exposed open an Action Center issue (*Servizi critici esposti sulla WAN*) and a *Sicurezza* notification; the issue closes itself when a later check is clean.
- Devices from other manufacturers show the Esposizione tab with the external check announced (SCAN-01 step 2).

## 0.49.51 — 2026-10-08

Integrations
- **Zabbix** (ZBX-01): NSM sends its devices to Zabbix 6.x and 7.x, as decided with the operator.
  - **Hosts.** Each device with a management IP becomes a host with technical name `nsm-…` and visible name *device · customer*, in the group `<prefix>/<customer>` (created when missing).
  - **Interface.** One interface, either Zabbix agent or SNMPv2 with a community macro.
  - **Templates.** Chosen per manufacturer (MikroTik, Ubiquiti, others). They are added and never unlinked, so templates linked by hand in Zabbix are kept.
  - **Tags and inventory.** Tags `source=nsm`, `nsm_device_id`, `nsm_customer` and `vendor`; inventory with vendor, model, serial, MAC, firmware and site.
  - **Updates.** When the IP or name changes in NSM, the Zabbix host is updated. Hosts of deleted or excluded devices are disabled, never deleted.
  - **API versions.** The version is detected with `apiinfo.version`:
    - Zabbix 7.2+ receives the token in the `Authorization: Bearer` header;
    - Zabbix 5.4–7.1 receives it in the `auth` field;
    - password login uses `username` (5.4+) or `user` (older).
  - **Settings.** *Integrations → Zabbix* sets URL, API token or username/password (encrypted), TLS check, group prefix, interface, templates per manufacturer and scope (all customers or selected ones). It offers *Test connessione*, *Sincronizza ora*, and an automatic sync every 15 minutes.
  - **Visibility.** Integrations hub card, *Zabbix* row in the system health page, and a *Dati in Zabbix* link in the device header.

## 0.49.50 — 2026-10-08

Reports and incidents
- **Syslog in the operational evidence report**: new section *8. Accessi e log di sicurezza*.
  - Devices that send syslog; failed and successful logins.
  - Access alerts by type; most targeted devices and source addresses (public or private).
  - Devices with the most error and critical lines.
  - When no syslog is received the section says it cannot be evaluated, instead of showing zeros.
  - Compliance and the device list move to sections 9 and 10.
- **Incident evidence**: the incident PDF adds *4. Log syslog degli apparati*, with the access events and the warning, error and critical lines of the incident devices inside the incident window.
- **Incident from an access alert**: *Security → Accessi* has *Apri incidente*, which pre-fills device, title, severity and description from the alert. The incident form accepts these values as pre-fill.

## 0.49.49 — 2026-10-08

UISP
- **UISP graphs** (UBNT-08 step 1): the device UISP tab shows the history read from UISP as charts.
  - Charts: radio signal (dBm), link capacity downlink/uplink, CPU and RAM, connected stations.
  - Ranges of 24h, 7 days, 30 days and 90 days, averaged into at most 400 points.
  - Sync interruptions show as holes; charts without data are not shown.
- New API `/api/v1/devices/{id}/uisp-metrics` and a reusable time-series chart component (`static/series_chart.js`).

## 0.49.48 — 2026-10-08

GUI
- **New interface icons** (GUI-02): menu, notification bell, theme, search and sidebar controls now use a consistent outlined icon set in Material style.
  - The set was drawn for NSM and is self-hosted as an SVG sprite (`static/ui-icons.svg`, `ui_icon()` helper).
  - It is CSP-safe and follows light and dark theme.
- **More polished look**:
  - active menu item marked with a side bar;
  - notification badge on the bell, which nudges on hover;
  - gradient brand mark and soft sidebar light;
  - hover shadow on the cards.
- **ISP/WISP background**: a light line drawing (radio tower, radio links to client sites, fibre backbone) at very low contrast.
  - It is fixed at the bottom right, so it never sits under text at readable contrast.
  - It is disabled with high-contrast preferences and when printing; animations respect *reduced motion*.

## 0.49.47 — 2026-10-08

Logs
- **Syslog configured by the MikroTik Agent** (LOG-01 step 2). **Agent 0.49.11** can configure remote syslog toward NSM:
  - it creates or updates its own logging action `nsm` and the rules *critical*, *error*, *warning* and *account* (logins, which feed the access alerts);
  - re-running is idempotent and the operator's other logging rules are untouched.
- **Ways to start it:**
  - from the device Syslog tab, with *Configura syslog con l'agent* and the status of the last request;
  - for every MikroTik from *Amministrazione → Syslog*;
  - automatically by the worker (default on) once the NSM IPv4 address is set. Agents updated later get configured too, and a new address re-configures everyone.
- **Not covered:** legacy agents (RouterOS 7.12 and 6.x) keep the manual commands shown in the tab.

## 0.49.46 — 2026-10-08

Security
- **Access alerts from syslog** (LOG-01 step 3). Received lines are recognised as failed logins (wrong user or password) or successful logins, with user, source address and service.
  - Vendors covered: RouterOS, OpenSSH/Dropbear (airOS, Cambium, Mimosa, Linux CPEs), Cisco IOS, Juniper, FortiGate, Huawei VRP and generic web logins.
  - Every recognised line becomes an access event (migration 0031).
- The worker raises Action Center alerts and notifications in the *Sicurezza* category (e-mail, Telegram and Slack, according to each user's preferences):
  - **brute force**: 5 or more failed logins in 10 minutes from the same address (high when the address is public);
  - **successful login after failures** from the same address (critical: possible guessed password);
  - **successful login from a new public address** not seen in 90 days (warning).
  - Repeated alerts are grouped for 6 hours.
- **Device Syslog tab**: an *Accessi al dispositivo* panel with 24-hour counters, the addresses with the most failures and the latest accesses; failed and successful logins are highlighted in the live log.
- **Security → Accessi**: alerts, most attacked devices and source addresses across the fleet (1h, 24h, 7 days, 30 days).

## 0.49.45 — 2026-10-07

Logs
- **Integrated syslog server** (LOG-01 step 1): new Docker service `syslog` on UDP/TCP 514.
  - Parses RFC 3164, RFC 5424 and the RouterOS format (topics give the severity).
  - Each line is matched to its device by sender IP; with shared NAT the hostname picks the device.
  - Each sender is rate limited; unknown senders are counted, not stored.
  - Warning, error and critical lines are kept for the whole life of the device; info, notice and debug lines are kept for up to 90 days to limit storage.
- **Syslog tab on every device** (any vendor):
  - live log updated every 5 seconds, with pause, severity and text filters, older lines on demand and CSV export;
  - counters for the day;
  - remote syslog configuration for the device's own manufacturer, shown inline.
- **Amministrazione → Syslog** (admin only):
  - server address for the devices, retention, acceptance of unknown senders;
  - receiver status;
  - unknown senders, which can be assigned to a device;
  - instructions for about 18 manufacturers.
- The **Server syslog** connector is added to the system health page.

## 0.49.44 — 2026-10-07

Inventory
- **Fix: MikroTik devices without IP in the device list** (INV-07). Agent-managed devices now get a management IP:
  - the public address configured on the router (PPPoE/WAN);
  - otherwise the address NSM sees the heartbeat from, which also works with older agents;
  - a value typed by an operator is never overwritten.
- The device lists show the management IP with its scope (public, private, CGNAT) and the LAN IP. The device header adds *IP LAN* and *Visto da NSM*.
- **MikroTik Agent 0.49.10** reports the RouterOS IP addresses with every heartbeat: as a metric on modern agents, as the `X-NSM-Addrs` header on legacy agents.

Roadmap
- GUI-02: Material icons for menu and notifications, more attractive GUI with an optional ISP/WISP background.

## 0.49.43 — 2026-10-07

Monitoring
- **Interface traffic graphs** (MON-01) on the MikroTik Monitor page, in Cacti/Zabbix style: in/out bit/s, 1h/24h/7d/30d ranges, current/average/maximum/95th percentile/volume.
  - Counter resets and missing heartbeats show as gaps.
  - WAN interfaces (PPPoE client, LTE, tunnel clients, else ether1) are monitored by default, and the list can be changed per device.
  - Retention is 90 days (migration 0029).
- **MikroTik Agent 0.49.9** sends the interface byte counters with every heartbeat: as a metric on modern agents, as the `X-NSM-Ifaces` header on 7.12 and RouterOS 6 legacy agents. Modern agents self-update; legacy agents need a reinstall to start sending counters.

- **Last telemetry is never lost** (MON-02): retention keeps the newest sample of every device and interface (MikroTik metrics, interface traffic, UISP metrics), so an offline or unreachable device still shows the last data received; the Monitor page shows its date.

Roadmap
- LOG-01: integrated syslog server with live per-device logs, configured by the agent.
- DUDE-01: integration with MikroTik The Dude through the RouterOS API (features to be evaluated).

## 0.49.42 — 2026-10-07

GUI
- **Global search**: compact suggestions grouped by pages, CVEs, devices, customers and sites, with counts and *vedi tutti*; device status dot, manufacturer and matched field; pages filtered by permission; CVE results with severity and exposed devices; recent searches; `/` and Ctrl+K shortcuts; highlight no longer splits values.
- **Manufacturer icons** instead of the two-letter vendor badges (Simple Icons, CC0, self-hosted sprite; generic device icon for brands without a published icon).

## 0.49.41 — 2026-10-07

Security
- **Multi-vendor CVE correlation (SEC-05)**: about 30 common manufacturers available by default (Ubiquiti, TP-Link, Cambium, Mimosa, Tenda, Huawei, ZTE, Teltonika, Fortinet, Juniper, Cisco…); NVD queried only for the brands and models in the inventory, with the full history for newly added products; generic firmware version comparison; never exposure by brand alone. *Produttore* field for manually added devices, coverage per manufacturer on the NVD page, CVE counts for every evaluable brand.
- Roadmap: added MON-01 (telemetry graphs, WAN/PPPoE traffic), ZBX-01 (Zabbix API) and GUI-SEARCH (global search bar).

## 0.49.40 — 2026-10-07

Notifications
- **Vulnerability newsletter** (daily or weekly, chosen in the profile): new CVEs on the inventory with severity, CVSS and number of exposed devices, plus open totals; skipped when there is nothing new.
- **Scheduled report delivery (REP-04)**: generated reports reach the users subscribed to *Report*, as e-mail attachment or link on Telegram/Slack.
- **Worker error alerts**: three consecutive failures of a periodic task raise a high-level notification, and its recovery an informative one.
- Migration 0028.

## 0.49.39 — 2026-10-07

Notifications
- **Telegram and Slack channels**: Telegram bot configured by the administrator (encrypted token, verified on save) with per-user chat ID, and the bot answers `/start` with the chat ID; per-user Slack Incoming Webhook (encrypted, only `hooks.slack.com`). Same level/category filters, outbox and retries as e-mail.

## 0.49.38 — 2026-10-07

Security
- **Two-factor authentication (TOTP)**:
  - setup from the profile with a QR code (Google Authenticator, Microsoft Authenticator, Aegis and similar);
  - ten one-time recovery codes;
  - a second login step with replay protection and throttling;
  - disable with password and code, and administrator reset;
  - 2FA status column on the users page.
  - Migration 0027; new dependency `segno` (pure-Python QR code).
- Roadmap: added UBNT-08 (full UISP management), VEND-01 (firmware catalogs for other vendors), VEND-02 (cnMaestro) and SEC-05 (multi-vendor CVE correlation).

## 0.49.37 — 2026-10-07

Notifications
- **E-mail notifications**: SMTP configuration in *Amministrazione → Notifiche* (encrypted password, test message, delivery log, re-queue); each user sets e-mail address, minimum level and categories in the profile; every in-app notification is queued for the matching users and delivered by the worker with retries and backoff; failed messages stay visible instead of being lost. Migration 0026.

## 0.49.36 — 2026-10-07

TR-069 / ACS
- **GenieACS integrated in the Docker stack** (optional profile `acs`): MongoDB 7 plus the cwmp, nbi, fs and ui services from one image built from the official `genieacs` 1.2.16 npm package, non-root with privileges dropped; ACS URL `:7547` and file server `:7567` published, NBI internal, UI on the host loopback only. `./manage.sh acs-enable` prepares secrets and profile; *Usa GenieACS integrato* points the NSM connector at it. `update.sh` and `install_core.sh` deploy the `genieacs/` build context. Procedure in `docs/OPERATIONS.md`.

## 0.49.35 — 2026-10-07

Development
- **Release discipline guard**: `APP_VERSION` must match the first CHANGELOG entry; entries are unique, consecutive, dated and non-empty; the agent version is documented; Alembic migrations form one numbered chain without gaps, with a single head and a downgrade each. Rules in `docs/DEVELOPMENT_WORKFLOW.md`.

## 0.49.34 — 2026-10-07

Operations / security
- **Deployment hardening**: `no-new-privileges` and `cap_drop: [ALL]` on migrate/api/worker (the image already runs as non-root uid 10001, so behaviour is unchanged); Caddy adds `Cross-Origin-Opener-Policy` and `X-Permitted-Cross-Domain-Policies`; new guard test for published ports, privileges, image user and security headers.
- `docs/OPERATIONS.md`: hardening review with what is in place and the per-installation actions (HTTPS with HSTS and secure cookies, Docker network trust, host firewall).

## 0.49.33 — 2026-10-07

Operations
- **Connector health** on *Amministrazione → Sistema*: one row per data source (UISP, NVD, GenieACS, RouterOS catalog, MikroTik agents) with state, last success and error detail.

## 0.49.32 — 2026-10-07

Operations
- **Capacity and retention** on *Amministrazione → Sistema*: largest tables, backup archive size and 30-day growth, estimated days until the backup volume is full (warning under 90 days), retention applied to each data set.
- `docs/OPERATIONS.md`: capacity/retention table and sizing rule, least-privilege deployment guidance (host, network, roles, API keys, MikroTik agent policies, UISP/GenieACS/NVD credentials).

## 0.49.31 — 2026-10-07

Security / operations
- **Encryption master-key rotation**: retired keys in `ENCRYPTION_PREVIOUS_KEYS` stay readable; `./manage.sh rotate-secrets` re-encrypts connector credentials and MikroTik binary-backup passwords with the new `ENCRYPTION_MASTER_KEY`; *Amministrazione → Sistema* shows secrets per key state and warns when some are unreadable. Rotation procedure in `docs/OPERATIONS.md`.
- Roadmap: the P0 completion-idempotency gate is marked as merged (#112–#116).

## 0.49.30 — 2026-10-07

TR-069 / ACS
- **GenieACS connector (ACS-01..03, read-only)**: NBI configuration with encrypted Basic/Bearer credentials for a reverse proxy and connectivity test; *ACS* tab on TP-Link/TR-069 CPE with lookup by serial number then MAC, preview, explicit association (NSM Customer/Site preserved) and refresh by GenieACS ID; identity, firmware, hardware, IP and last Inform normalized from TR-098/TR-181; integrations hub card with real state. Ported from the unmerged Core 0.27 foundation branch onto the current CSP-safe UI.

## 0.49.29 — 2026-10-07

Ubiquiti
- **UBNT-06 step 1, firmware state from UISP**: installed and latest firmware, latest on the same major, compatibility and pre-release are read at every sync; Ubiquiti devices get the recommended version and *update available* / *current* state and appear in the firmware worklist; new *Firmware da UISP* panel on the UISP tab. No state is inferred when UISP does not expose the latest version.

## 0.49.28 — 2026-10-07

Security
- **Login throttling**: 5 failed logins for the same username from the same address, or 30 from one address, within 15 minutes block further attempts with `429` until the window passes; the account is never locked. Failed and throttled logins are audited and listed on *Amministrazione → Utenti*. Migration 0025.
- **Session lifetime**: idle timeout (`SESSION_IDLE_MINUTES`, default 120); a password change closes every other session; *Esci dalle altre sessioni* (profile) and *Disconnetti* (admin users); the login page explains why the session ended.

## 0.49.27 — 2026-10-07

Security
- **API-key lifecycle**: rotation with a grace period (24 h, 7 days or immediate revoke) keeping name, scopes, Customer and validity length; usage evidence per key (request count, last client IP); review states for expired, expiring (14 days), never used, unused for 90 days and keys without expiry. Expired keys are no longer shown as *Attiva*. Migration 0024.

GUI
- Code blocks (`json-preview`: API authentication examples, raw diagnostic output) had dark text on a dark background; the text is now readable.

## 0.49.26 — 2026-10-07

Operations
- **Worker task isolation**: every periodic worker task runs in isolation; an error in one task (for example an unreachable UISP or NVD) is logged and recorded and no longer skips the remaining maintenance, verification and sync tasks of that cycle.
- **Worker observability**: heartbeat and last outcome of each task in Redis; `/health` adds a `worker` field (status code unchanged); new *Amministrazione → Sistema* page with version, schema revision, database size, backup-volume free space, worker tasks, platform database dumps and restore drills.
- **Restore tooling**: `./manage.sh restore-drill` (non-destructive restore test into a temporary database, recorded as evidence) and `./manage.sh restore-db <dump>` (typed confirmation, safety dump, restore, migrations); runbook `docs/OPERATIONS.md`.

GUI
- `alert warning` and `alert danger` boxes, used on 20+ pages, now have their own styles.

## 0.49.25 — 2026-10-07

MikroTik
- **MTK-05 structured diagnostics**: ping (loss, min/avg/max RTT, jitter, per-packet table), traceroute (hop table, destination reached), neighbors and DHCP leases linked to NSM Devices by MAC, logs newest first with levels; legacy agent output is parsed into the same views. Per-target history of the previous 10 ping/traceroute runs and a one-line outcome in the recent diagnostics list (last 25 diagnostics, no longer crowded out by other jobs).

## 0.49.24 — 2026-10-07

MikroTik
- **MTK-04 step 5, RouterOS upgrade suggestions**: *Firmware → Suggerimenti RouterOS* shows every MikroTik behind its channel head with the next safe step (ready for a plan, legacy upgrade, readiness check, plan open, soak, blocked with reason), security first; non-security releases wait 7 days on the channel. Bulk readiness checks and plan creation (max 25, every gate re-checked, refused plans leave no draft); plans still need the pre-upgrade backup and approval.

## 0.49.23 — 2026-10-07

Security
- **Route-level RBAC guard**: a test walks every registered route; anonymous requests must end on the login page and writes by the read-only auditor must be refused, except an explained allow-list (own notifications read, read-only firmware check). The review found no exposed route.

## 0.49.22 — 2026-10-07

MikroTik
- **MTK-04 RouterOS release catalog**: channel heads and release notes from upgrade.mikrotik.com every 6 hours, security classification with evidence, device firmware state from the catalog when readiness is missing or stale, security escalation from release notes and open CVEs; catalog page and firmware-tab line.
- **Agent scheduler `start-time=startup`** (new installations): avoids the RouterOS 7.24.0–7.24.4 bug where schedulers with default start date/time were not triggered, and sends a heartbeat right after every reboot.

## 0.49.21 — 2026-10-07

Ubiquiti
- **UBNT-02 UISP monitoring**: CPU, RAM, signal, link capacity, uptime, frequency and stations from the UISP overview at every sync, with 90-day history; UISP tab with current/min/avg/max over 24 h; signal in the customer device list; missing values stated, never zero.

## 0.49.20 — 2026-10-07

Ubiquiti
- **UBNT-03 bulk onboarding from UISP**: list of UISP devices with their NSM state, explicit Customer/Site, preview, fresh re-read at confirmation, creation or association of up to 200 devices per batch; *Importa da UISP* on the customer device list.

## 0.49.19 — 2026-10-07

GUI
- **CVE column in the customer device list**: open CVEs per device with the highest severity and the critical/high still to handle, linking to the vulnerability list filtered on that device (new `device` filter); `0` only when an advisory source is loaded and the device is evaluable, `—` otherwise.

## 0.49.18 — 2026-10-07

Lifecycle
- **LIFE-03** EOL/EOS remediation: replacement plan with target date, justified exception with expiry, replacement Device or decommissioning, history and audit; Action Center issue while to handle, overdue or after an expired exception; *Gestione* column in the EOL/EOS worklist; report section and CSV column.

## 0.49.17 — 2026-10-07

MikroTik Agent 0.49.8
- **Bounded modern snapshots**: configuration sections and the support snapshot read rows by id up to a per-menu limit and halve the rows sent until the JSON body fits the 64 KiB RouterOS `http-data` limit (previously a large routing table, firewall or lease list made the upload fail and the snapshot was lost).
- **RouterOS 7.13 – 7.16**: modern agent variant without `json.no-string-conversion` (a 7.17 option that made the whole script fail to load on earlier 7.x) and with `/file read` compiled at run time; chosen at enrollment and self-update.

## 0.49.16 — 2026-10-07

MikroTik Agent 0.49.7
- **`.rsc` backup for RouterOS 7.12 legacy agents**: export read with `/file get contents` (up to ~60 KB) and archived with SHA-256; policies reduced to the export format for these agents; older legacy agents and RouterOS 6 stay explicitly not protected.

## 0.49.15 — 2026-10-07

Fix
- **Pages behind the production CSP**: Caddy sends `default-src 'self'`, which blocks inline scripts, `on*` handlers and inline style attributes, so the new-device vendor fields (Ubiquiti MAC never visible), the backup-policy form, bulk actions, theme init, copy buttons, clickable rows, delete confirmations and the dashboard donut/bar charts did not work in production. All behaviours moved to `static/forms.js` / `static/theme-init.js` driven by `data-*` attributes; a test forbids inline scripts, handlers and style attributes.
- **New device**: one MAC/serial field pair per vendor section (the three `primary_mac` inputs overrode each other), inactive sections disabled, Ubiquiti MAC required; validation errors return to the form with a message instead of a JSON page.
- **UISP connection test**: the error names the cause (invalid/self-signed certificate with the *Verifica certificato TLS* hint, connection refused, DNS, timeout, HTTP/HTTPS mismatch); hint next to the TLS checkbox for local UISP consoles.

## 0.49.14 — 2026-10-07

MikroTik Agent 0.49.6
- **Legacy agents (6.48/6.49, 7.12) can reboot and upgrade RouterOS**: profile `legacy-ops-v1`, fixed handlers acknowledged by NSM before acting, upgrade target from a fresh readiness (same major only), verification by the version reported after the reboot; Firmware tab panel with typed confirmation. Legacy agents must be reinstalled once.

## 0.49.13 — 2026-10-07

MikroTik
- **RouterOS 6.48/6.49 support**: compatibility family `routeros-6-legacy` and a v6 variant of the legacy agent (no v7-only syntax, validated before it is handed out): enrollment, heartbeat, telemetry, diagnostics (ping, neighbours, DHCP, logs), firmware readiness and structured snapshots; traceroute refused with an explicit message.

## 0.49.12 — 2026-10-07

MikroTik Agent 0.49.5
- **Controlled reboot** from the GUI (*Riavvia* on the device header): reason and typed confirmation, agent acknowledgement before `/system reboot`, verification by uptime reset on the next heartbeat, explicit failure after 15 minutes, history and audit.

## 0.49.11 — 2026-10-07

MikroTik Agent 0.49.4
- **Modern backup uploader rewrite** (supersedes #105, #108, #118): privilege profile ops-v2 adds `policy` and `sensitive`, required by RouterOS for `/system backup save` from a scheduled script (reinstall the agent once); settled non-empty file also under `flash/`; progress guard; step-level errors with operator explanation; temporary files always removed; empty artifacts refused server-side.

## 0.49.10 — 2026-10-07

MikroTik Agent 0.49.3
- **RouterOS 7.12.x structured snapshots** (ex #104): resources, interfaces, addresses, routes, firewall/NAT, DHCP leases, PPP/tunnels and logs over the allow-listed `rows-v1` legacy transport, with bounded per-menu collection and a 64 KiB wire cap; legacy agents must be reinstalled (older ones get an explicit reinstall hint).

## 0.49.9 — 2026-10-07

Lifecycle
- **LIFE-01/02** Lifecycle catalog per vendor model (EOL/EOS separate, source and verification date, CSV import/export) and exact model/alias correlation with Devices; ambiguous and unmatched models stay explicitly unknown; manual values with source; *In scadenza* and *Senza dato lifecycle* worklists.

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

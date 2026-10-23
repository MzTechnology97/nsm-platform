# Changelog

NSM Core follows `0.49.x`: every release promoted to `deploy` increases the
patch number in `app/app/entrypoint.py` (`APP_VERSION`, shown in the sidebar).
MikroTik Agent versions are independent (`mikrotik_agent_generation.py`).

## 0.49.239 — 2026-10-23

Exposure: ports filtered by ACL no longer reported as exposed
- **TR-069 port (7547) of CPEs managed by NSM.** NSM is their ACS, so the port always answers the NSM server. It is now shown as *da verificare*, with low severity, and no longer as *esposto, alta*. From the NSM server alone it cannot be told whether others are filtered.
- **Remote web management of TP-Link CPEs.** NSM now reads, through the ACS, whether it is on, on which port and from which addresses it is allowed. That port is checked too (for example 20443). It is reported as open to everyone or limited to the allowed address, as the CPE's configuration says.
- ***Filtrata da ACL…* on the *Esposizione* tab.** For any device, when a service looks open from NSM but is filtered for everyone else by an ACL, declare it with the allowed addresses. It is then shown as limited, with who declared it and when. The declaration stays valid at the next checks and can be removed at any time.
## 0.49.238 — 2026-10-23

Pages on every list
- **The lists now load one page at a time**: 50, 100 or 200 rows, as you choose. They show "1–50 of 838", the page numbers and the previous/next buttons. Your choice of rows per page is remembered.
  - *Clienti* used to load every customer at once and was slow; it now loads one page.
  - *Apparati*, *Firmware*, *Vulnerabilità*, *Lifecycle*, *Compliance*, *Action Center*, *Incidenti*, *Eventi*, *Report*, UISP and cnMaestro onboarding, and the ISPBilling import used to load more rows as you scrolled; they now show pages.
- **No devices hidden on large fleets any more.** *Monitoring*, *Agent*, *Backup* and *Esposizione* showed at most 500 devices; every device is now reachable through the pages.
- Changing a filter, the search or the sort takes you back to page 1. The counters on the chips always cover the whole list, not just the page.
## 0.49.237 — 2026-10-23

Customer name as a tag in GenieACS
- **Every TP-Link or other TR-069 CPE linked in NSM now carries its customer's name as a tag in GenieACS**, so the GenieACS panel shows, and can filter, whose CPE it is. The tag is added after the link.
- **The tag stays aligned on its own:**
  - it follows the device when it moves to another customer, and the old tag is removed;
  - it follows the customer when it is renamed;
  - it is put back if someone removes it in GenieACS.
- **Tags you set yourself in GenieACS are never touched.**
## 0.49.236 — 2026-10-23

Cambium logo on Cambium devices
- **Cambium devices now show the Cambium Networks logo** instead of the generic device icon: ePMP, cnPilot, PMP and the others, in every list, page and in the global search. This also applies to devices added as generic whose manufacturer was recognised as Cambium.
## 0.49.235 — 2026-10-23

ISPBilling
- **Automatic import of new customers and sites.** It is off until an administrator switches it on, in *Integrazioni → ISPBilling → Importazione automatica*. After each read:
  - customers new in ISPBilling are created in NSM with their sites, once their service addresses are read;
  - new installation addresses of customers already in NSM become sites;
  - customers that look like an existing NSM customer (same code or name) are not imported: the page counts them and links to the import page, where they can be linked by hand.
- **Deleted customers and sites are not recreated.** Everything already in the mirror when this version is installed counts as handled (migration 0056), as does everything imported since. A customer or site deleted in NSM afterwards is therefore not created again.

## 0.49.234 — 2026-10-23

Quick import buttons
- ***Clienti* has an *Importa da ISPBilling* button.** It opens the customer import directly. It appears only while ISPBilling is enabled and its last read or test succeeded, and only for users who may import (administrators).
- ***Apparati* has *Importa da UISP*** (and ***Importa da cnMaestro***, when that integration is in use). Each opens the onboarding of that integration. They appear only while the integration is enabled and its last sync or test succeeded, and only for users who may add devices.
## 0.49.233 — 2026-10-23

Search in long customer and device lists
- **Customer fields now have a search box, and show the customer code next to the name** (e.g. *Mario Rossi · C840*), so customers with the same name can be told apart and found by code.
- **Search:** type part of the name or the code: every word you type must appear, in any order, and case and accents are ignored. For example "nicco ross" finds "Niccolò Rossi". Pick with the mouse, or with the arrow keys and Enter.
- **Where:**
  - UISP and cnMaestro onboarding, for both the customer and the UISP site;
  - *Gestisci apparato*, when moving a device to another customer;
  - the customer filters of the lists: devices, Action Center, vulnerabilities, firmware, monitoring, incidents, backups, compliance, exposure, activity log, reports, agents and access;
  - the forms for a new incident, a backup policy, a compliance baseline, a maintenance window and an API key;
  - the device and site fields of backup policies, compliance baselines and maintenance windows, and the replacement device in the lifecycle.
- Short lists, such as vendors, roles and states, stay as normal menus.
## 0.49.232 — 2026-10-23

ACS monitoring refreshed again
- **Fixed: NSM's ACS provisioning never ran at the periodic contacts of the CPEs.** In GenieACS the NSM presets listed three events (bootstrap, boot, periodic). GenieACS applies a preset only when all of them are in the same session, which never happens during normal operation. So uptime, CPU, RAM, WAN traffic, Wi-Fi clients, LAN hosts and DSL were never re-read. The history and charts stayed empty (TP-Link HB210) or frozen (VX230v).
- The presets now run at every contact. Each value is still re-read only when it is older than its interval (5 minutes for monitoring, hourly or daily for the rest).
- After the update, NSM reinstalls the templates in GenieACS on its own. Values reappear at each CPE's next contact.

## 0.49.231 — 2026-10-23

Activity
- **Date filters use the local day.** In the activity log and in a device's jobs, "from" and "to" dates are now days of the server time zone (`APP_TIMEZONE`, Europe/Rome by default). Before, they were UTC days, so events between midnight and 01:00 (02:00 in summer) Italian time fell on the previous day.

## 0.49.230 — 2026-10-23

Tests
- **The activity-log test no longer fails when CI runs just after midnight UTC.** Its date-filter check now starts from the previous day.

## 0.49.229 — 2026-10-23

ISPBilling accounts linked to CPEs
- **NSM finds the device of each ISPBilling PPPoE account in more ways:**
  - the PPPoE username a TP-Link or other TR-069 CPE logs in with;
  - the caller-id (MAC) of the account's PPP session on the PPPoE concentrator, which also finds UISP radios;
  - the address of that session.
  The concentrator must be a MikroTik in NSM.
- **Customer and site are proposed, never applied by themselves.** When the device belongs to the same customer but sits at another site, the device page and *Integrazioni → ISPBilling → Apparati da assegnare* propose the site from ISPBilling, applied with one click.
- **Devices of another customer are only flagged:** NSM never moves them by itself. You can still reassign one from the device page, with a second confirmation.
- ***Ignora*** stops a proposal from coming back. Every assignment is recorded in the audit with the customer and site before and after.
## 0.49.228 — 2026-10-23

Port forwarding, DMZ and UPnP of TR-069 CPEs
- **The *ACS* tab shows what each CPE exposes to the Internet:**
  - the port forwards, with protocol, external port, internal host and port, and whether each one is on;
  - the DMZ, when the CPE reports one, with a warning if it is on;
  - whether UPnP is on.
- **Monitoring only:** nothing is changed on the CPE from NSM. The values are re-read every hour, or on demand with *Rileggi dal CPE*.
- **Every change is recorded in the device activity**, for example a new port forward, the DMZ turned on, or UPnP turned on or off.
## 0.49.227 — 2026-10-23

TP-Link firmware from the official site
- **NSM now knows the latest firmware of every TP-Link model you have.** Every day it reads the official TP-Link Italian download page for the hardware version of your devices, for example V1 for the VX230v. The newest version appears on the device overview, in the firmware list and in *Firmware → Cataloghi*, as for the other brands. Firmware for another hardware version is never proposed, because it can damage the device.
- **One-click download into the ACS.** When a newer version is out, the device's *Firmware* tab offers *Scarica dal sito TP-Link nell'ACS*. NSM downloads the official archive (only from TP-Link's server), extracts the firmware and selects it for the upgrade. The upgrade itself still waits for your confirmation.
- **Automatic download (on by default).** When a newer version is published, NSM downloads it into the ACS by itself, once per version. It is then ready and preselected on the *Firmware* tab of every CPE of that model. Nothing is installed without your confirmation. You can turn it off in *Amministrazione → ACS → Download automatico dei firmware*.
## 0.49.226 — 2026-10-23

Alerts of TR-069 CPEs
- **TP-Link and other TR-069 CPEs now raise alerts** in the Action Center, with a notification:
  - when the CPE stops contacting the ACS;
  - when its WAN is down but the CPE is still reachable, for example over the 4G backup;
  - when the WAN reconnects or the CPE reboots too often;
  - when the DSL line degrades (low SNR margin, or a speed well below the usual one).
- **Each alert closes by itself** when the problem ends. Notifications during planned maintenance are labelled, as for the other devices.
- **No duplicate alerts:** a CPE the ping monitor already reports as unreachable gets no second alert.
- ***Amministrazione → ACS → Avvisi dei CPE*** turns each alert on or off and sets its thresholds.
## 0.49.225 — 2026-10-22

ACS
- **The bulk CPE migration to the self-hosted ACS was removed.** It was meant to rewrite the CWMP ACS URL of a fleet of TP-Link CPEs over the management network, but the TP-Link web login could not be used on the operator's firmware. The *Amministrazione → ACS → migrazione* page, its API and its two tables (migration 0055) are gone. CPEs are pointed at the integrated GenieACS as described in the TP-Link guide on *Amministrazione → ACS*. Past migration events keep their labels in the activity log.

## 0.49.224 — 2026-10-22

Audit label
- **The audit log now names the `DEVICE_VENDOR_CORRECTED` event** (*Vendor dell'apparato corretto*) in all four languages, instead of showing the raw code. It was recorded when a device's vendor was corrected on the server (generic → cambium).

## 0.49.223 — 2026-10-22

UISP backups
- **Two UISP backups of the same device in the same second no longer overwrite each other.** The file name now includes the backup run, for example `20261022T170000Z_ap-tetto_1a2b3c4d_uisp.tar.gz`.

## 0.49.222 — 2026-10-22

Ping from NSM, on and off from the device page
- **Click the *Ping da NSM* tile** on the device overview: a small window opens with an on/off switch.
  - It shows the address that is pinged.
  - It saves at once, and the first result arrives within a minute.
  - Thresholds and a different address stay in *Monitor → Impostazioni monitoraggio*, linked from the window.
- Users without the permission to edit devices see the window read-only. When the device has no address to ping, the switch cannot be turned on and the window says why.

## 0.49.221 — 2026-10-22

Lifecycle from the web
- **NSM looks up the real end of life of every model in the inventory on the web,** through eol.network, a free database of the end-of-sale and end-of-support notices vendors publish on their own sites (MikroTik, Ubiquiti, Cambium, TP-Link and 90+ more). For each model it keeps the official vendor page and the day it was read.
- **New section *Ciclo di vita dal web*** in *EOL / EOS → Catalogo lifecycle*: product found, vendor status, published dates, how sure the match is, and the sources. A model with no notice shows *Nessun avviso di fine vita pubblicato*. When the inventory has a marketing name (e.g. "LiteBeam 5AC"), *Cerca con un altro nome* searches with the code on the label.
- **Nothing changes until you apply it:** *Applica al catalogo* for one model, or *Applica le corrispondenze esatte* for all. The catalog record has the source, link and date like one entered by hand, and the devices are re-correlated. When the vendor gives a status but no date (MikroTik *Discontinued*, Ubiquiti *Vintage*/*Legacy*), the date is the day the source was read, and the note says so.
- **Automatic:** the worker looks up new models and re-checks the others every 30 days. Without a key it does a few models per run (the public limit is 10 requests a minute). With a free key (`EOL_NETWORK_API_KEY`) it does all of them at once. `LIFECYCLE_WEB_LOOKUP=off` turns it off.
- Only vendor and model names are sent.

## 0.49.220 — 2026-10-22

TP-Link CPE syslog through the ACS
- **NSM sets the TP-Link CPEs to send their logs to the NSM syslog receiver,** through GenieACS (TR-069). It writes only four log settings: remote logging on, receiver address, port 514 and minimum level. Local logging and everything else stay as they are.
- **Three ways:**
  - one CPE, from its *Syslog* tab (applied at once when the CPE answers);
  - every associated TP-Link, from *Amministrazione → ACS → Syslog dei CPE TR-069* (applied at each one's next contact);
  - automatically for new CPEs when *Configura automaticamente* is on in *Amministrazione → Syslog* (it is on by default).
- **No identifier needed on TP-Link.** The syslog receiver otherwise accepts only lines with an NSM key, but a TP-Link can't add one. So lines without a key are accepted from the address of a CPE that NSM itself configured this way, and only when no other device uses that address. Every other line without a key is still discarded. Before configuring, NSM refreshes the CPE's management IP from the ACS, which is the WAN address the logs come from.
- Checked on a real TP-Link VX230v. A firewall between the CPE networks and NSM must let UDP/TCP 514 through, as it does for 7547.

## 0.49.219 — 2026-10-23

Firmware upgrade of TR-069 CPEs
- **TP-Link and other TR-069 CPEs now have the *Firmware* tab, like MikroTik devices.** It shows the installed version next to the newest one in the firmware catalog for that model, the same information shown on the device overview and in the firmware list.
- **Upgrade from the *Firmware* tab.** Load the image downloaded from the vendor's site: NSM stores it in the ACS for that model. Then choose it and type the device name to start. The CPE downloads the image from the ACS file server, installs it and reboots.
- **NSM checks the result itself.** The upgrade is confirmed when the CPE reports the new version, and NSM says whether it is the version you expected. If the CPE reports an error, the error is shown. If no new version arrives within 30 minutes, the upgrade is marked as not confirmed. The follow-up runs even when nobody has the page open.
- **Built-in safeguards:** an image loaded for one model cannot be sent to another, only one upgrade runs at a time per CPE, and images already in the ACS can be reused for other CPEs of the same model.
## 0.49.218 — 2026-10-22

Speed test from TR-069 CPEs
- **Download and upload test from the CPE** in the *ACS* tab. The CPE itself downloads or sends a test file (TR-143), so the result is the speed of the customer's line up to the test server, not of the Wi-Fi. Results are in Mbps, with the size and the duration.
- **NSM is the default test server.** The CPE reaches it at the same address it uses for the ACS, on HTTP port 80. Each test gets its own link, valid 15 minutes and only for that test.
- ***Amministrazione → ACS → Test di velocità dei CPE*** lets you set the NSM address if the CPEs reach it elsewhere (for example over HTTPS with a DNS name), other download and upload URLs to measure towards another server, and the size of the test (25 MB by default).
- **One test at a time per CPE.** While it runs, the customer's connection is busy. If the CPE reports an error, its TR-143 code is shown with an explanation, for example when it cannot reach the test server.
## 0.49.217 — 2026-10-22

Configuration snapshots of TR-069 CPEs
- **Snapshot of a CPE's configuration** (TP-Link or other TR-069 device) from the *ACS* tab. It is archived in the device's *Backup* tab as a text file, one parameter per line, with the configuration parameters as the ACS read them. You can open it and compare two snapshots to see exactly what changed, as with RouterOS exports.
- **Backup policies with the TR-069 option** take one automatically, and only when the configuration has changed since the last snapshot. The *Backup* tile of these CPEs therefore shows the last snapshot.
- **Left out of the snapshot:** passwords and keys (CPEs do not report them anyway) and values that change on their own (counters, times).
- **No one-click restore:** a snapshot documents the configuration and shows what changed. To put a setting back, do it by hand or from the Wi-Fi settings.
- ***Rileggi configurazione dal CPE*** makes the ACS read the CPE's whole configuration again. Use it before the first snapshot of a newly linked CPE.

## 0.49.216 — 2026-10-22

Wi-Fi and reboot of TR-069 CPEs from NSM
- **Wi-Fi on the *ACS* tab** of a TP-Link or other TR-069 CPE, for technicians and administrators:
  - per radio (2.4 / 5 GHz): automatic channel or one of the channels the CPE allows, and on/off;
  - per network: name, new password and on/off.
  Networks that are off are grouped apart. Passwords cannot be read, because CPEs do not report them: you can only set a new one.
- **Changes are checked before sending:** name of 1 to 32 characters, password of 8 to 63, allowed channels. Then they are sent to the CPE through the ACS in one go, after a confirmation. If the CPE is not reachable at that moment they apply at its next contact. The activity log records what changed, never the password.
- **Reboot of the CPE** from the same tab: you type the device name to confirm. NSM shows when the CPE came back.

## 0.49.215 — 2026-10-22

Legacy backups over FTP: subnets, port, firewall
- **Choose who may send backups.** *Amministrazione → Sistema → Backup legacy via FTP* lists the subnets allowed to send backups to the FTP receiver of RouterOS 6 and 7.12 routers, one per line or separated by commas. The receiver refuses any other origin within a minute. With no subnet set, it still accepts everyone, as before, and the page warns about it.
- **Custom port.** `./manage.sh legacy-backup-enable <IPv4 of NSM> [port]` sets the port the routers use (default 2121). The passive ports can be changed with `LEGACY_FTP_PASV_PORTS`.
- **Firewall handled by the same command.** It opens the ports in ufw or firewalld when one of them is active. When subnets are set in NSM, it also adds host rules that block other origins before they reach the receiver. `./manage.sh legacy-ftp-firewall --apply` refreshes those rules after the subnets change.

## 0.49.214 — 2026-10-22

Moving NSM to a server with another address
- **New page *Amministrazione → Migrazione server*.** When NSM moves to a server with another IP address, the old server tells the devices the new one, so no router has to be touched by hand:
  - MikroTik agents that update themselves receive a script that talks to the new address, preceded by the syslog setting towards it;
  - TR-069 CPEs receive the new ACS URL through GenieACS — the only CWMP setting that changes;
  - agents that cannot update themselves are listed, to be reinstalled from the new server.
- **Safe by steps:** the new server must be a restore of this database (NSM checks it with a token created with the migration, and refuses an address that leads back to this same server); a pilot on up to 10 devices comes first, and "migrate all" unlocks only after a pilot device has shown up on the new server.
- **Arrivals confirmed by the new server** (last heartbeat or last Inform after the switch). The devices that arrived can then be taken out of service on the old server, so it stops raising "unreachable" alerts for them.
- The step-by-step procedure (backup and restore, settings to check on the new server) is on the page and in `OPERATIONS.md`.

## 0.49.213 — 2026-10-22

Guide: adding a TP-Link CPE to the ACS
- **Step-by-step guide in *Amministrazione → ACS → Configurare una CPE TP-Link*.** One manual step on each router — CWMP on, Inform on, the ACS URL and Periodic Inform — then NSP recognises the CPE. The guide says to save without touching anything else: WAN, PPPoE, Wi-Fi and LAN stay as they are.
- **New section "Cosa fa NSP e cosa non tocca":** what NSP reads (state, last contact, model, firmware, IP, CPU, memory, WAN traffic, Wi-Fi clients, DSL line), the actions it offers (reboot, firmware check, diagnostics) and what it never changes (Wi-Fi, LAN, DHCP, PPPoE). The only setting it writes is the CWMP inform interval.
- **The right ACS URL for the routers:** set `ACS_CWMP_URL` in `.env` to the address the routers reach NSP on (for example `http://<server>:7547`). The guide and the CPE migration then show that address instead of the one the administrator opened NSP with.

## 0.49.212 — 2026-10-22

Legacy MikroTik agent: failures say why
- **A failed job on a RouterOS 7.12 (legacy) router now reports the reason**, not only "RouterOS legacy job execution failed". RouterOS 7.12 cannot pass the error text to the script, so the Agent records the step it was on and the reason. For a backup, the reason can be, for example:
  - the export command failed;
  - the export file was not created;
  - the export is too large for the legacy transport;
  - the export was read only in part ("export read incomplete: 4095/18230 bytes");
  - the upload to NSM failed.
  The reason appears in the backup history and in the device jobs.
- **Agent 0.49.23**: legacy Agents update themselves automatically. Modern Agents get the same version, with no other change.

## 0.49.211 — 2026-10-22

History of TR-069 CPEs
- **The *ACS* tab of a TP-Link or other TR-069 CPE now shows the line and Wi-Fi:**
  - WAN state and type (PPPoE or IP), address, time connected and last connection error;
  - Wi-Fi radios with channel, width and connected clients;
  - LAN hosts;
  - for DSL lines, speed, SNR margin and attenuation.
- **Charts over 24 hours, 7, 30 and 90 days:** WAN traffic, CPU and memory, Wi-Fi clients and LAN hosts, DSL speed and quality, kept for 90 days.
- **Online and offline from the ACS:** a CPE is shown offline after three missed periodic contacts (at least 15 minutes).
- **Firmware without traffic counters:** some versions, like the one on the TP-Link VX230v, leave the WAN byte counters at zero. NSM says so instead of drawing a flat line.
- NSM reads all this from GenieACS every two minutes and sends nothing to the CPEs. The NSM rule in GenieACS makes the CPE report these values at every contact (5 minutes); NSM updates the rule by itself, as from 0.49.210.

## 0.49.210 — 2026-10-22

ACS templates updated automatically
- **The NSM templates in GenieACS follow NSM updates by themselves.** If you installed them once (*Amministrazione → ACS → Installa template base*), NSM checks right after every update, and then every 30 minutes, whether this version brings new ones. If it does, it reinstalls them: for example, to make the CPEs report CPU and memory every 5 minutes. You no longer need to press the button again after an update.
- Templates you never installed are not touched: installing them stays your choice.
- The ACS page shows when the templates were last installed and whether they match this version. Every automatic update is recorded in the activity log.

## 0.49.209 — 2026-10-22

ISPBilling RADIUS accounts on the device page
- **NSM now reads the PPPoE accounts of ISPBilling Radius 2.0** together with customers and services.
- **Each account is matched to the device that uses it:**
  - the MikroTik whose PPPoE client logs in with that username (from the agent's PPP snapshot);
  - otherwise the device whose management IP is the account's static IP.
  When two devices fit equally, nothing is guessed.
- **On the device overview, the *Account ISPBilling* panel** shows the username, the profile (for example *FWA 100/20*), the ISPBilling status (*Attivo*, *Sospeso*, *Terminato*) and the ISPBilling customer. It warns when ISPBilling bills a different customer than the one the device belongs to in NSM.
- *Integrazioni → ISPBilling* shows how many accounts were read and matched. If the API key has no Radius permission, only the accounts are skipped and the page says so.

## 0.49.208 — 2026-10-22

Copy buttons
- **"Copia" works when NSM is opened over plain HTTP.** Browsers allow the clipboard API only on HTTPS, so on an installation reached at http:// these buttons did nothing:
  - the agent install command;
  - the new API key;
  - the 2FA recovery codes;
  - the connector compatibility report.
  They now copy through the browser's older copy command. If the browser refuses that too, a message asks you to select the text and press Ctrl+C.

## 0.49.207 — 2026-10-22

Ubiquiti firmware catalog
- **The Ubiquiti catalog no longer waits a day after a restart.** The worker asks the Ubiquiti update service once a day. Right after a restart it could ask before UISP had reported which airOS platforms are in use; with nothing to ask, the next try was 24 hours later. Now an empty attempt does not count, and the catalog fills in at the next cycle after the UISP sync.

## 0.49.206 — 2026-10-22

TR-069 CPEs: diagnostics and resources
- **Ping and traceroute run by the CPE itself**, on the *ACS* tab of a TP-Link or other TR-069 device linked to GenieACS. The result is what the customer's line sees:
  - ping: replies, minimum, average and maximum time;
  - traceroute: every hop with its address and times.
- **When the test starts:** at once when the CPE accepts GenieACS's connection request, otherwise at its next contact with the ACS. The page follows it until it ends, and gives up after 4 minutes without an answer.
- **Who can run it:** technicians and administrators. Every test is recorded in the activity log. The last result of each test stays on the device.
- **CPU, memory and uptime of TR-069 CPEs** (for example the TP-Link VX230v) are shown on the device page and in the device list, as for MikroTik devices. NSM reads them from GenieACS every 5 minutes without sending anything to the CPE, and does not show values more than 2 hours old.
  - **After the update, press *Installa modelli* again in *Amministrazione → ACS*.** That updates the NSM rule in GenieACS, so the CPE reports these values at every periodic contact (5 minutes) instead of once an hour.
- **Device page of TR-069 CPEs:** the *Backup* tile says *non disponibile* instead of an orange "mai", because backups through the ACS are not supported yet. The data source reads *TR-069 (ACS)*.

## 0.49.205 — 2026-10-22

Zabbix: use the hosts you already have
- **NSM no longer duplicates the devices Zabbix already monitors.** When a Zabbix host has the same IP as an NSM device, NSM links to it. If the IP in Zabbix is out of date, NSM links on the same name instead. A link is made only when exactly one host matches.
  - NSM shows that host's problems on the device page and in *Monitoring*.
  - It does not create an `nsm-` host for that device.
- **Your hosts are not modified.** Groups, templates and tags stay as they are. The one exception is optional and on by default: when a router changes address (dynamic public IP, NAT), NSM updates the IP of the host's main interface.
- **Devices with no host in Zabbix** still get an `nsm-` host, as before.
- **Devices that match several Zabbix hosts** (for example customers behind the same NAT address) are listed on the Zabbix page and not guessed.
- **Factory LAN addresses (the default LAN address of home routers and CPEs) never link a device to a Zabbix host by IP**, because many unrelated devices use them. A device whose address is one of them gets no `nsm-` host either, since Zabbix could not reach it. The Zabbix page lists it, so you can enter the address Zabbix must use.
- **The template fields suggest the templates of your Zabbix**, so the names can be picked instead of typed.
- The device page says when its Zabbix host is one of yours, linked by IP or by name.

## 0.49.204 — 2026-10-22

NVD advisories
- **A slow moment of NVD no longer makes the advisory update fail.** Every request to NVD is tried up to three times, waiting longer each time (30, 60 and 90 seconds), and also when NVD answers with a temporary server error. Before, a single 30-second timeout on one of the products followed made the whole update fail, and the next attempt was hours later.
- NVD's rate-limit answers are still reported at once, as before.


## 0.49.203 — 2026-10-22

Customers and sites from ISPBilling
- **New ISPBilling integration** (*Integrazioni → ISPBilling*): connect NSM to the ISPBilling billing system with a read-only API key. NSM never writes to ISPBilling.
- **NSM keeps a copy of the ISPBilling registry**, refreshed every 6 hours or on request (*Leggi ora*):
  - customers with name, code, kind, status and group;
  - the installation address of every service.
  Fiscal data, contacts and payment data are not copied.
- **Importa da ISPBilling** lists the customers as *Nuovi*, *Da collegare* (an NSM customer with the same code or name exists) or *Già in NSM*. You choose which ones to bring in, see a preview, then confirm:
  - each becomes an NSM customer, or is linked to the existing one;
  - each gets one site per installation address (for example *Sede Centrale Comune*, or the address itself);
  - addresses already present in NSM are reused, and nothing already in NSM is renamed or removed;
  - running the import again only adds new sites.
- Every import is recorded in the activity log.

## 0.49.202 — 2026-10-22

Bulk migration of customer CPEs to the self-hosted ACS
- **Move a whole fleet of TP-Link routers from the vendor cloud (TAUC) onto your own GenieACS** without configuring each router by hand. In *Amministrazione → ACS → Migra i CPE verso questo ACS* you give the management subnet, the port, the target ACS URL (the integrated GenieACS by default) and one shared credential for the fleet.
- **Dry run (available now):** NSM reaches every address on the management network and reports which are TP-Link CPEs, with model and firmware — an inventory of what can be migrated, without sending any credential. Checked on a real TP-Link VX230v.
- **Apply:** rewrites the CWMP ACS URL on each router through its web interface. The TP-Link login differs per firmware and is turned on after validation on the live fleet, so until then each host reports that the change needs validation instead of guessing.
- **Safeguards:** administrators only; IPv4 subnets up to 4096 addresses per run; a host is checked before any credential is sent, so the credential never reaches a non-CPE; the `.0`/`.255` addresses and the ACS server are skipped; the credential is used only during the run and never stored or logged; one run at a time, with per-host results and an audit entry.

## 0.49.201 — 2026-10-22

GenieACS: IP and MAC of TR-181 CPEs
- **NSM now reads the IP address and the MAC addresses of TR-181 CPEs** such as the TP-Link VX230v (checked on a real router). Before, both stayed empty because NSM only looked at the first instance of each object, while these routers keep the WAN on another one (for example `Device.IP.Interface.6`).
  - **IP:** NSM uses the address the CPE gives GenieACS for connection requests, then the first usable address of any interface.
  - **MAC:** NSM reads the MAC of every interface (LAN, WAN, links). A device is found and matched by any of them, so the MAC on the label works whichever port it belongs to. A MAC already written on the NSM record is kept.
- **MAC parameter paths accept `*`** for every instance (`Device.Ethernet.Interface.*.MACAddress`). Connectors still on the old default list switch to the new one automatically; paths written by an administrator are kept.
- **Base provisioning:** it now asks every CPE for its interface MACs. To use it, press *Installa template base* again in *Amministrazione → ACS*.
## 0.49.200 — 2026-10-22

Alerts during planned maintenance
- **Notifications raised during a planned maintenance window say so.** If the device, or its whole customer, is in a maintenance window when the alert is raised, the notification is still sent as usual. It is labelled *in manutenzione programmata*:
  - on the *Notifiche* page, where the label shows the window's reason and end on hover;
  - in the subject of the e-mail, Telegram and Slack messages.
- Nothing is suppressed, and Action Center issues are unchanged.

## 0.49.199 — 2026-10-22

UISP and cnMaestro checked on the real consoles
- **Ubiquiti firmware catalog fixed.** The requests to the Ubiquiti update service used its two filters the wrong way round, so the catalog stayed empty. NSM now gets the recommended airOS version for every platform in use (for example 8.7.x for WA/XC, 6.3.x for XW) and ignores the "-cs" custom-script builds.
- **Ubiquiti devices linked through UISP get a firmware state.** UISP sends only "8.7.15" and no latest version. NSM now reads the airOS platform from UISP so the catalog applies, and marks the device *Aggiornamento disponibile* when UISP says an upgrade exists and nothing else is known.
- **UISP device log:** the kind of event comes from UISP's tags (*stato*, *interfaccia*, *riavvio*…), translated, and so is the level. It used to be empty.
- **cnMaestro Wi-Fi access points (cnPilot):** connected clients and downlink/uplink traffic now come from the 2.4 and 5 GHz radios, so the *Stazioni* and *Throughput* charts fill in. Uptime, which cnMaestro does not send, is shown as the time online for online devices.
- **cnMaestro import** groups devices by tower, else by site, else by network (towers are rarely set).
- The compatibility check looks for these fields too, so its report matches what NSM reads.

## 0.49.198 — 2026-10-22

Device pages
- **No raw codes left on the device pages:**
  - the data source in the identity panel (for example *agent MikroTik*, *UISP*, *manuale* instead of `mikrotik_agent`);
  - the origin of agent enrollments;
  - the kind of backup run (*agent MikroTik · .backup e .rsc* instead of `mikrotik_multi`).
  All of them are translated in the four languages.

## 0.49.197 — 2026-10-22

Documentation
- **Screenshots of the interface** in `docs/images/` and in the README: dashboard (light and dark), device list, customer, device overview (also at phone width), monitor, backups, agents, firmware, vulnerabilities, Action Center, notifications and customer SLA. They come from a demo instance with synthetic data only (documentation IP addresses, `02:` MAC addresses, "Demo" names).

## 0.49.196 — 2026-10-22

Event names in your language
- **Activity events have names in Italian, English, French and Spanish** (for example *Accesso* instead of "User login", *Export visualizzato* instead of "Backup export viewed") on the dashboard, the activity log (also in the type filter), the customer overview and the device *Attività* tab. The code stays visible under the name in the activity log.
- **The source of an event is translated too** (*interfaccia*, *automatico*, *agent MikroTik*, *UISP*…), and so are the result on the customer overview and the severity and category of the customer's open issues.
- **Firmware list:** manufacturer names written properly (*MikroTik*, *TP-Link*) and the update status reported by RouterOS translated (*Nuova versione disponibile*).
- **Smaller fixes:** the *Notifiche* page has its title in the top bar; the facts under a device name no longer start a wrapped line with "·"; the unread counter on the bell uses a darker red, easier to read.
- A test now fails when a new kind of event has no name in the four languages.

## 0.49.195 — 2026-10-22

Phone-width layout
- **No page scrolls sideways on a phone any more** (checked at 375 px on every page of the interface):
  - small summary tables (traffic statistics on the device monitor, customer graphs, backup coverage, access, unassessed vulnerabilities) scroll inside their panel;
  - wide lists such as the event log stay inside their scrolling frame;
  - the "i" help bubbles no longer widen the page while hidden, and fit the screen when shown;
  - the CSV column list on the lifecycle catalog page wraps.
- **Help bubbles inside table headers** are written in normal case instead of capitals.

## 0.49.194 — 2026-10-21

SLA budget alerts
- **NSM warns before a customer SLA is missed.** For every customer with an SLA target, NSM sends a notification:
  - when the downtime of the month has used **75%** of what the target allows ("SLA a rischio");
  - when it has used **all of it** ("SLA non più rispettabile"), because the month's target can no longer be met.
- **Compared with the whole month's allowance,** so a short outage on the first day is not reported as a missed SLA. Planned maintenance on the whole customer is left out, as in the SLA page.
- **Each alert is sent once per customer and month.** It reaches the bell and the e-mail/Telegram/Slack channels of the users who receive monitoring notifications, and links to the customer's SLA page. Every alert is recorded in the activity log.
## 0.49.193 — 2026-10-21

Documentation
- `IMPLEMENTED_CAPABILITIES.md` and `ROADMAP.md` now describe the features delivered from 0.49.176 to 0.49.192:
  - the new interface and its "J" style, navigation counters, device list, dashboard and device page;
  - the device logbook;
  - customer SLA and planned maintenance windows;
  - the UISP and cnMaestro compatibility check;
  - the cnMaestro section (it was missing) with bulk onboarding;
  - the personal notification archive.
- Stale "not implemented" lists for UISP and for approved areas now reflect what is really still open.
## 0.49.192 — 2026-10-21

Import Cambium devices from cnMaestro
- **New "Import from cnMaestro" page** (Integrations › cnMaestro), the same flow as the UISP onboarding.
  - Every cnMaestro device is listed with its state in NSM: new, to link to an existing record, already in NSM, or not importable with the reason (no MAC, MAC repeated in cnMaestro, MAC used by another NSM device, device decommissioned).
  - Filters by text and by tower/network.
- **You choose the NSM customer and site,** NSM never derives them from cnMaestro networks or towers.
  - A preview shows what will happen.
  - At confirmation the cnMaestro list is read again, so a device changed or removed in the meantime is skipped instead of being created from stale data.
- **No duplicates.** A Cambium device already in NSM with the same MAC is linked to cnMaestro and stays in its own customer.
  - New devices get name, model, serial, firmware, IP and status from cnMaestro.
  - Their type comes from the product and mode: APs, subscriber modules and cnMatrix switches.
- Every import is recorded in the activity log.
## 0.49.191 — 2026-10-21

Archive notifications
- **Archive notifications from the Notifications page.**
  - "Archive" on each notification, or "Archive the read ones" to clear every notification already read in one click.
  - Archived notifications leave your list and the bell count. Archiving also marks them read.
- **New "Archived" filter.** It shows what you archived, and "Restore" brings a notification back.
- **The archive is personal.** Notifications are shared, so archiving one hides it only for you; other users keep seeing it, just like the read state.
- Migration 0047 (`notification_reads.archived_at`).
## 0.49.190 — 2026-10-21

Device page fixes
- **"Disattiva dispositivo…" moved into the device header actions,** as a quiet button that turns red on hover. It no longer floats on its own between the header and the tabs. The form with the reason still opens under the header.
- **The tab bar no longer shows a small vertical scrollbar.** It only scrolls sideways when the tabs do not fit.
- **Diagnostics: long outcomes wrap inside their card.** Examples: "Operation not yet available on the legacy RouterOS transport…", and the ping/traceroute summaries. Before, they ran out of the card.
- **Open issues on the device overview:** severities are translated (Critica, Alta, Attenzione…) instead of CRITICAL/HIGH/WARNING. Titles read as text and become links on hover, so they no longer all look like errors in the branding colour.
## 0.49.189 — 2026-10-21

Compatibility check for UISP and cnMaestro
- **New "Compatibility check" panel** on the UISP and cnMaestro integration pages (administrators). It finds out why a Ubiquiti or Cambium device shows missing or wrong data.
  - "Run check" reads a small sample from your console with read-only requests: never a reboot, backup or change.
  - It shows, for every field NSM expects (MAC, model, firmware, status, signal, CPU, interfaces, events…), whether it arrives, in how many devices and under which key.
  - It also shows which endpoints answer with an error, and the key names the console actually returned, so a field read under a different name can be mapped.
- **Safe to share.** The report contains only field names, counts and HTTP codes: no device names, IP or MAC addresses, serial numbers or passwords. Identifier-like keys are masked.
  - "Copy report" puts it on the clipboard to send to support.
  - The last report is kept, and every run is recorded in the activity log.
## 0.49.188 — 2026-10-21

Device page: 30-day availability and tidier tiles
- **New "Availability 30 d" tile on the device overview.** It shows the ping availability of the last 30 days, with the measured downtime and the number of outages.
  - Maintenance windows are left out of the measure.
  - The tile is green from the customer's SLA target (or 99% when none is agreed), amber within one point below, red under that.
  - Without NSM ping it says "not measured" and points to the Monitor tab.
- **Tidier overview tiles.**
  - Words such as "not active" are shown smaller on one line, with the full text on hover. Notes may use two lines.
  - The Backup tile shows when the last backup ran, with the method underneath.
  - The tiles now sit four per row at 1440 px, so none is left alone on a second row. The device list keeps its six tiles on one row.
## 0.49.187 — 2026-10-21

New look, step 5: lists
- **Device list.** Above the filters, six tinted tiles with a ring: online, offline, to link, severe CVEs, firmware to check, end of life. Each shows its share of the devices. The chips ("Label · count"), the dense table with status pills, firmware with its CVE pill and the CPU/RAM bars keep the new look. Every row keeps its vendor icon. On phones the tiles sit two by two.
- **Same look on every list.** Customers, backups, firmware, vulnerabilities, incidents, Action Center, activity log, SLA and the administration lists share the same table header (small grey uppercase labels, now with proper height on every table), row separators, chips and pills.
  - Severity pills no longer break on two lines.
  - The small summary tables (charts, CVE device lists) use the same header style.
  - An empty list inside a panel takes a short band instead of half the screen.
## 0.49.186 — 2026-10-21

New look, step 4: dashboard
- **Live pill in the top bar.** Next to the breadcrumbs, a green pill reads "aggiornata 16:56 · prossimo tra 23 s" and counts down to the next 30-second refresh. On narrower screens the same line stays in the page.
- **KPI tiles with rings.** The eight figures (devices, online, offline, protected backups, severe CVEs, firmware, Action Center, end of life) are tinted tiles with a small ring showing their share of the inventory (for Action Center: the share of critical issues). They still open the same filtered lists.
- **Side column.** "Da guardare" is a dark card: one row per thing to look at, the count first in red, amber or green. "Attività recente" sits below it as a feed with a dot coloured by severity; the result is now in the line under each event.
- **Main column.** "Stato operativo" (offline devices by age, CVE exposure, devices under load) comes right after the tiles, then the coverage rings. Distribution and the latest advisories follow below.
- Devices in the dashboard lists keep their vendor icon.
- On the top bar, the user's name next to the avatar is shown on screens 1600px and wider.
## 0.49.185 — 2026-10-21

New look, step 3: device page
- **Header card.** On one white card:
  - the device's vendor icon on a tinted tile;
  - the name with its status pill and, when CVEs are open, a CVE pill (red with critical/high ones, amber otherwise) that opens the device's vulnerability list;
  - one line of facts: vendor · model · type, firmware, management IP, and how long the device has been up (or when it was last seen);
  - the actions on the right, "Gestisci apparato" as the main button.
- **Overview tiles with rings.** CPU and memory (amber from 75%, red from 90%; shown when the device reports them), ping from NSM (the ring shows the replies), open vulnerabilities (the ring shows the critical and high share), firmware (recommended version when different) and backup (last successful one). Each tile is tinted by its state.
- The separate "Risorse adesso" bars are shown only when the tiles cannot show CPU and memory. The CVE chips by severity stay below the tiles.
- Tabs keep the blue underline of the new look.
## 0.49.184 — 2026-10-21

New look, step 2: branding defaults and sidebar counters
- **Branding defaults on the new palette.** The factory colours are now blue #1769d6 (primary), navy #10192b (sidebar) and amber #f79009 (highlight). Administration › Branding shows them as "Predefinito", and "Predefinito" resets to them.
  - Installations still on the previous factory colours (#1f5f8b, #111827, #f59e0b) move to the new ones automatically (database migration `0046_branding_palette`; the old values are also read as the new ones).
  - A colour chosen by an administrator is kept.
  - Factory colours follow the light and dark themes; a custom colour applies as chosen.
- **Counters in the sidebar.** Next to Apparati (total), Monitoring (offline, red), Vulnerabilità (devices with severe CVEs, red), Firmware (to update, amber) and Action Center (open issues, red when one is critical, amber otherwise).
  - They refresh every minute and are hidden when zero.
  - Each counter only appears with the permission for its page, and only counts the customers the user can see.
  - Screen readers hear them as text, e.g. "9 offline".
- New endpoint `GET /api/ui/v1/nav-counts`: one COUNT query per figure. Decommissioned devices are left out.
- The customer SLA is not counted in the menu: its monthly verdicts add up every ping of the month, too heavy for a request repeated every minute.
## 0.49.183 — 2026-10-21

New look, step 1: colours, shell and base components
- **New palette.** Light grey page, white panels with a thin border and 12px corners, almost no shadows; dark navy sidebar; blue primary colour (#1769d6). Headings are bolder; the font is a rounded humanist sans taken from the system (no web fonts are loaded).
- **Sidebar.** Section titles in small capitals, bold items with their icon, the current page shown as a solid blue pill. The portal name sits next to a round initial (or the uploaded logo) with the tagline under it. On phones the menu still opens as a drawer.
- **Top bar.** White, with where you are on the left ("Operazioni › Apparati"), the search field, the bell, language, theme, and an avatar with the user's initials (the name is shown on wide screens).
- **Base components.** Statuses are coloured pills (green online, red offline, amber waiting, grey otherwise); filter chips read "Label · count"; tables have small uppercase grey headers and denser rows; tabs underline the active one in blue; metric tiles are tinted by meaning; alerts are tinted boxes; buttons and fields share one height and radius; focus is always visible.
- **Dark mode** keeps the same structure on deep blue-grey surfaces, with a lighter blue for links and a darker one behind white text.
- **Sign-in pages** use the same palette: navy band, white card, round mark, blue button.
- **Branding.** Custom colours chosen in Administration › Branding still apply everywhere. Colours left at their factory values now follow the new palette (light and dark) instead of the previous blue.
- No feature, page or API change.
## 0.49.182 — 2026-10-21

Planned maintenance windows
- **Plan maintenance from the Customer SLA page.** Choose a customer, all its devices or just one, a start, an end (at most 7 days) and a reason.
- **Excluded from the measure.** Ping checks inside a window count neither as up nor as down, in the SLA, the report availability section and the customer's 30-day availability.
  - A window on the whole customer also shortens the month the downtime budget is computed on.
  - The SLA table shows how much maintenance time was excluded.
- **Retroactive windows stay visible.** A window written after it started is marked "retroactive", because it changed an availability already measured.
- **Recorded in the activity log.** Every creation and deletion is recorded, and a window on one device also appears in that device's logbook events.
- Needs `customers.write`. Migration 0045 (`maintenance_windows`).
## 0.49.181 — 2026-10-21

Customer SLA
- **New "Customer SLA" page** under Governance. For each customer and calendar month, it shows:
  - the agreed availability target and the measured availability;
  - the downtime budget: how much of the downtime the target allows is already used (99.9% over 30 days allows 43 minutes);
  - the measured devices, and those below target;
  - the verdict: met, missed, not measured, or no target.
- **Month view.** Customers that missed come first. The current month is measured up to now and labelled as running. The last 12 months can be chosen.
- **Per-device detail.** Open a customer to see each device's availability, downtime, outages, longest outage, loss and latency, and how many of its devices have no ping monitoring.
- **Targets.** Set or remove a customer's target from the page (50–100%, two decimals; needs `customers.write`). Every change is recorded in the activity log.
- **CSV export** of the month, recorded in the activity log.
- **Same measure as the report.** Availability is the ICMP measure already used in the report (one ping round every 2 minutes, a reply = up).
  - A customer report and the customer's 30-day availability now compare against the customer's own target instead of the fixed 99%.
  - The customer page shows the target, with a link to the SLA page.
- Migration 0044 (`customers.sla_target_pct`).
## 0.49.180 — 2026-10-21

Device logbook
- **New "Device logbook" in the Activity tab of every device.**
  - Operators, technicians and administrators write what was done on the device. Each entry has a type: note, site visit, maintenance, replacement or customer contact.
  - The date can be set back up to a year, for work recorded later.
  - Entries appear on a timeline with their author.
  - "Also show recorded events" adds the device's events (backups, firmware, configuration changes…) to the same timeline.
- **Entries are not edited.** A wrong entry is deleted by its author or an administrator, then written again. Adding and deleting are recorded in the activity log, the deleted text included.
- **Stays writable after decommissioning,** to record that the device was returned or scrapped. Entries are deleted only with the device.
- **Permissions:** a new `logbook.write` permission is given to technicians and operators. Auditors can read only.
- Migration 0043 (`device_journal_entries`).
## 0.49.179 — 2026-10-21

Device page (UX-3): health, diagnostics and configuration easier to read
- **"Resources now" and "Open CVEs" on the overview.**
  - CPU and memory bars with the uptime.
  - Open CVEs as severity chips (critical, high, medium, low). Each chip opens the vulnerability list filtered on this device and severity.
  - "No known CVE" or "Not assessable" when nothing is open.
- **Readable vendor in the device header** ("MikroTik · RB5009UG+S+").
- **Diagnostics: last run on every card.** "Last 5 min ago · 0% loss · avg 12 ms", or "Never run".
- **Ping chart.** One bar per reply, lost replies in red, the average as a dashed line.
- **Traceroute latency bars.** Each hop shows its best-to-worst range and its average on the scale of the slowest hop: amber with some loss, red from 50% loss.
- **Device log levels.** Chips with the count of critical, error, warning and info lines. Click one to show only those lines.
- **RouterOS syntax colours** in the export viewer and in the diff: section paths, commands, keys, values, strings and comments.
- **Section index.** "Jump to section…" lists every section of the export with its size.
## 0.49.178 — 2026-10-21

Dashboard (UX-3): operations at a glance
- **New "Operations" row.**
  - **Offline devices:** the offline devices counted by how long they have been down (under 1 hour, 1 day, 7 days, older, never seen), and the six most recent outages with vendor icon, customer and "down for 2h 15m".
  - **Resource load:** how many online devices are above 90% or between 75% and 90% CPU or memory, and the five most loaded, with the same bars as the device list.
  - **CVE exposure:** the whole inventory as one bar: severe CVEs, other open CVEs, no known CVE, not assessable. The first two open the filtered device list.
- **Refresh countdown.** "Next refresh in 23 s" next to the update time, with a small ring.
- **Explanations on hover.** An "i" next to the coverage and operations titles explains what is counted and the thresholds. It also opens with the keyboard.
- **Clickable distributions.**
  - Vendors show their readable names (MikroTik, Ubiquiti, TP-Link, Generic) and open the device list filtered by vendor.
  - Models open a device search.
- **Phones.** Fixed the dashboard being wider than the screen on phones, caused by the advisories table.
## 0.49.177 — 2026-10-21

Device list (UX-2): more at a glance, actions on many devices
- **CVE state on every row.** A red badge with the open CVEs when one is critical or high, orange for other open CVEs, green "CVE OK" when the model and firmware are known and nothing is open, grey "CVE ?" when NSM cannot tell (missing model or firmware).
- **Resources in the list.** CPU and memory use as two small bars (amber from 75%, red from 90%), and the uptime under the status, from the MikroTik agent or the UISP and cnMaestro connectors. Offline devices show their last values faded.
- **Readable vendor names.** "MikroTik · RB5009UG+S+" under the device name and in the vendor filter, instead of the internal "mikrotik".
- **Select and act on many devices.** Check the devices (or all loaded ones). A bar offers: export CSV of the selection, back up now, check firmware (MikroTik), decommission with one reason for all, or reactivate in the "Decommissioned" view. The bar shows the progress and which devices failed and why.
- **CSV export.** "Export CSV" downloads the whole filtered list (not only the loaded rows) with customer, site, addresses, firmware, lifecycle, last contact and open CVEs. Every export is audited (`DEVICE_LIST_EXPORTED`).
## 0.49.176 — 2026-10-21

UX foundations (UX-1): faster navigation, clearer loading and empty states
- **Command palette.** Ctrl+K (⌘K on Mac), or the search field in the top bar, opens "Search or go to…": pages, then devices, customers and sites as you type, with vendor icon, status and customer. Recently opened items appear first. Enter opens, arrows move, Esc closes; "All results" opens the search page.
- **Sidebar grouped.** Operations, Security, Governance and Platform headings. The portal name wraps on two lines instead of being cut.
- **Loading placeholders.** Pages and lists show grey placeholders while they load, instead of a "Loading…" line. The device list no longer says "0 devices match the filters" while it is loading.
- **Empty states.** The device list says whether nothing matches the filters (with "Reset filters" and the number of active filters) or no device exists yet (with what to do).
- **Wide and ultrawide screens.** The content grows up to 1920px and stays centred, instead of stopping at 1600px against the sidebar with half the screen empty.
- **Dashboard.** Every figure has its icon and the cards sit in a regular grid (4, 2 or 1 per row), with no card left alone on a row.
- **Theme follows the system.** The profile offers System, Light and Dark. The theme is applied before the page draws, so a dark page no longer flashes white.
- **Sign-in in four languages.** The login and two-step verification pages offer Italiano, English, Français and Español. They follow the browser language until you pick one. The choice, and the language used in the application, are remembered for the next sign-in.
## 0.49.175 — 2026-10-21

Device decommission (Disattiva / Riattiva)
- **Take a device out of service without losing its history.** The device page has "Decommission device…", with a required reason. The device then stops taking part in the platform:
  - scheduled backups, ping monitoring, agent health and self-update;
  - UISP and cnMaestro sync, the Zabbix push (its host is disabled, never deleted) and Zabbix problems;
  - exposure, compliance, CVE matching and firmware checks.
- **Clean state on decommission.** Its open Action Center issues are closed with a note, its queued jobs are cancelled, and no new issue is opened for it.
- **Agent and syslog.** Its agent is refused (403) but keeps its credential, so it resumes on reactivation. Its syslog lines are dropped, never reported as an unknown sender.
- **History stays readable.** Overview, backups, configuration history, diagnostics, jobs, the incidents that involved it and the audit trail still show it. Any other change answers "device decommissioned" until it is reactivated; deleting it is still possible.
- **Out of counters and lists.** It leaves counters, dashboards, worklists and search. The device list has a "Decommissioned" filter, and the page shows a banner with date, author, reason and "Reactivate".
- **No duplicates.** UISP import, connector association and agent enrollment still see decommissioned devices, so the same device is never imported twice. Manual creation says to check the decommissioned devices.
- **Audited.** `DEVICE_DECOMMISSIONED` and `DEVICE_REACTIVATED` events. Migration `0042_device_decommission`.
## 0.49.174 — 2026-10-21

Classic interface removal, final shell cleanup (phase 8, R1)
- **Classic shell removed.** The old sidebar, top bar, flash messages and dashboard template are gone. Only the sign-in pages (login and two-step verification) still use a server template, now with a layout of their own.
- **Smaller sign-in stylesheet.** `app.css` keeps only the sign-in rules (about 7 KB instead of 120 KB). The scripts of the classic pages (charts, forms, search, live dashboard) and the shell background image are removed.
- **No more links to the classic interface.** The device, customer, agent and backup API responses no longer carry `classic` links. Two help texts (compliance baselines, syslog vendor guides) pointed to the classic page and now point to the new one. The "New interface (preview)" badge is gone from the top bar.
- **Dead code removed.** Template helpers and their installers, the classic theme switch (`/profile/theme`), classic page handlers that no longer ran (dashboard, firmware list, backup policy form, device list drill-down) and the orphan helpers they used.
## 0.49.173 — 2026-10-21

Classic interface removal, devices part 2 (phase 8, R1)
- **Classic device tabs removed.** These device pages now exist only in the new interface:
  - Backup archive, file view and restore tests;
  - Configuration and its history, Diagnostics;
  - Firmware upgrade, RouterBOOT, controlled reboot;
  - Agent, UISP, cnMaestro, GenieACS;
  - Exposure, Compliance, Lifecycle.
- **Old addresses keep working.** Each classic address redirects (302) to the same page under `/app`. The file view keeps its comparison (`against`). The former section pages (interfaces, IP addresses, routes, firewall, DHCP leases) open the Configuration tab. Backup downloads, the diagnostic JSON export and the log CSV stay where they were.
- **Removed with the pages.** Their forms (snapshot, diagnostics, baseline, restore test, plan, stage, activate, cancel, RouterBOOT, reboot, agent update and reinstall, UISP/GenieACS actions, exposure check and target, lifecycle) and their feedback wrappers. Also removed: 20 templates and the classic-only template helpers.
- **"Refresh all" in Configuration.** The tab can now ask the agent to read every section in one go, as the classic page did. Sections already waiting are not queued twice.
- **Consistent errors.** Asking for a snapshot on a MikroTik whose agent transport is unknown now returns a coded error, as in the classic page. Associating a UISP device already linked to another record returns a coded error too.
## 0.49.172 — 2026-10-16

Classic interface removal, devices part 1 (phase 8, R1)
- **Classic pages removed.** These device pages now exist only in the new interface:
  - device list and CSV import;
  - device overview and manage;
  - Monitor, Syslog and Activity tabs.
- **Old addresses keep working.** Each classic address redirects (302) to the same page under `/app`, and the device list keeps its filters. The CSV template and log CSV downloads stay where they were.
- **Removed with the pages.** Their forms: alias, enrollment, manage, delete, traffic interfaces, ping settings, syslog configure. Two other buttons lived only on the overview and are removed as well: "Backup ora" and "Verifica firmware". Also removed: the device CRUD, CSV import and backup feedback wrappers, the inventory module and 11 templates.
- **Ping monitoring per device.** The Monitor settings now turn ping on or off, set the address to ping and the latency and loss thresholds. Before, this was only on the classic overview.
- **Zabbix per device.** The device page can now exclude a device from Zabbix and bring it back. It also shows when the host waits for the next sync.
- **Syslog key.** Opening the Syslog tab creates the device key again, so the manual guide shows the real prefix.
- **Agent install commands.** A new device and an agent reinstall show the guided RouterOS 7 command and the RouterOS 6.48/6.49 command, as the classic overview did.
## 0.49.171 — 2026-10-15

Classic interface removal, customers (phase 8, R1)
- **Classic pages removed.** The customer pages now exist only in the new interface:
  - the customer list and the new-customer form;
  - customer overview, sites (with new/edit), devices, graphs, backups, security and history;
  - the customer edit page, new device and the bulk-manage page.
- **Old addresses keep working.** Each classic address redirects (302) to its place in the new interface, keeping the filters it understands:
  - customer devices → `/app/devices?customer=…`;
  - customer history → `/app/events?customer=…`;
  - customer backups → `/app/backups?customer=…`;
  - site forms → the Sites tab.
- **Removed with the pages.** Their form handlers:
  - customer create, edit and delete;
  - site create, edit and delete;
  - device create, bulk move and bulk delete.

  Also removed: two feedback wrappers, the route-precedence module that only promoted these pages, and 15 templates.
- **Device list.** The UISP signal and the LAN IP (when different from the management IP) are shown, as on the classic customer device list.
- **Customer page.** "Import from UISP" opens the UISP onboarding with the customer already selected.
## 0.49.170 — 2026-10-15

Classic interface removal, administration (phase 8, R1)
- **Classic pages removed.** These administration pages now exist only in the new interface:
  - users, API keys, notifications, syslog, system, branding, ACS, Demo Lab;
  - the connector settings for UISP, Zabbix, GenieACS, cnMaestro and NVD;
  - the RouterOS catalogue.
- **Old addresses keep working.** Each classic address redirects (302) to the same page under `/app`.
- **Removed with the pages:**
  - their form handlers: users, roles, scopes, sessions and 2FA reset; API key create, revoke, delete and rotate; SMTP, Telegram and retry; syslog settings and configure-all; branding and logo; ACS templates; demo seed and clear; connector save, test and sync; NVD sync and match; RouterOS refresh;
  - the API key feedback wrapper;
  - an API key module that was never installed;
  - 16 templates.

  The new interface uses the `/api/ui/v1/admin` endpoints, which share the same functions.
- **System page links.** The connector links point straight to the new interface.
- **CI.** The canonical-route contract no longer lists the removed admin pages.
## 0.49.169 — 2026-10-15

Classic interface removal, operations area (phase 8, R1)
- **Classic pages removed.** These pages now exist only in the new interface:
  - Monitoring and agents;
  - global backups, backup policy forms (including the customer policy form) and the export viewer;
  - firmware, firmware catalogs and suggestions;
  - integrations hub and UISP onboarding.
- **Old addresses keep working.** Each classic address redirects (302) to the same page under `/app` and keeps the filters the new page understands. The export viewer address opens the export in the device's backup tab.
- **Removed with the pages.** Their form handlers:
  - ICMP bulk;
  - policy create, edit, toggle and delete;
  - export delete;
  - catalog releases and refresh;
  - suggestions apply;
  - onboarding preview and commit.

  Also removed: the backup policy feedback wrappers, 11 templates and an unused export viewer module. File downloads stay where they were.
- **Zabbix in the new interface.**
  - Monitoring lists the devices with open Zabbix problems, worst first, and follows the customer and vendor filters.
  - The device page links to the host's data in Zabbix. It shows a problem badge in the header and the open problems on the overview.
## 0.49.168 — 2026-10-15

Classic interface removal, governance area (phase 8, R1)
- **Classic pages removed.** These pages now exist only in the new interface:
  - compliance (overview, baselines, results);
  - vulnerabilities and findings;
  - access and exposure;
  - lifecycle and its catalog;
  - incidents;
  - Action Center;
  - audit events and reports;
  - notifications, search and profile.
- **Old addresses keep working.** Each classic address answers with a redirect (302) to the same page under `/app`. The redirect keeps the filters the new page understands. This covers bookmarks, e-mail links, Action Center details and audit entries.
- **Removed with the pages.** Their form handlers, feedback wrappers and templates. The new interface uses the `/api/ui/v1` endpoints, which share the same functions.
- **Access alerts.** The access alerts API returns the involved users and an incident prefill. "Open incident" on an alert starts a new incident with title, severity and summary filled in.
- **Report downloads.** A blocked download (integrity check failed, or report not found) returns to the new Reports page with an explanation.
## 0.49.167 — 2026-10-15

New interface — manual syslog setup, phase 8d
- **Device Syslog tab.** "Manual setup" opens the instructions for the device's manufacturer, with NSM's address and the device key already in the commands. It used to link to the classic Logs page.
- **Syslog administration.** "Instructions per manufacturer" lists all 18 guides. It used to link to the classic page.
- The guide texts are translated; the commands come from the server.
- **Shared code.** The guides live in one module (`syslog_guides`), used by the classic templates too.
- **API.**
  - the device syslog API returns `guides` and `target` instead of `classic_url`;
  - the syslog administration API returns `guides`.

## 0.49.166 — 2026-10-15

New interface — support snapshot output, phase 8c
- **Support snapshot.** "Open" in the device Diagnostics tab shows the collected sections (resources, IP, routes, interfaces, PPP, DHCP, log) inline, each one collapsible. It used to open the classic result page. The JSON export is unchanged.
- **API.** `GET /api/ui/v1/devices/{id}/diagnostics/jobs/{job_id}/output` (`monitoring.read`); the diagnostics list no longer returns the classic `classic_url`.

## 0.49.165 — 2026-10-15

New interface — monitored interfaces, phase 8b
- **Monitored interfaces.** The device Monitor tab chooses which interfaces keep a traffic history (at most 8), with each interface's current rate and a "Back to automatic" option (WAN links, else ether1). It used to link to the classic monitor page.
- **Shared code.** `interface_traffic.set_monitored_record`, used by the classic form too.
- **API.** `GET/POST /api/ui/v1/devices/{id}/traffic-interfaces` (`monitoring.read` / `devices.write` + CSRF).

## 0.49.164 — 2026-10-15

The new interface is the default (phase 8a)
- `/` opens the new interface (`/app/`); anonymous visitors still go to the login page, and a successful login lands there too.
- The new interface no longer offers the way back:
  - the "Classic interface" buttons are gone from every page and from the header;
  - the dashboard has its own navigation entry;
  - navigation, administration, integration, device and customer tabs only use React routes.
- The classic pages still answer at their addresses while they are being removed area by area.

## 0.49.163 — 2026-10-15

New interface (React) — last links to classic pages, phase 7aa
- **Incident evidence.** The incident page has its own "Archived evidence" section: export the evidence PDF (`reports.generate`) and download earlier exports, with state, events, root cause and SHA-256. Before, the button opened the classic page.
- **Action Center by device.** The issues listed on the device overview open the Action Center filtered on that device, with a notice and a "Show all" button. The customer list opens it filtered on the customer.
- **Links that now stay in the new interface:**
  - device names in the exposure list (exposure tab);
  - "My profile" in user administration;
  - the GenieACS link on the ACS page.
- **Shared code.** `incident_evidence.export_record` (also used by the classic page).
- **API.**
  - `POST /api/ui/v1/incidents/{id}/export`;
  - the incident detail API lists `exports`;
  - `GET /api/ui/v1/action-center` accepts `device`.

## 0.49.162 — 2026-10-15

New interface (React) — new incident, phase 7z
- **New incident** (`/app/incidents/new`), opened from the incidents list, the device header and the Action Center:
  - choose the customer, or arrive from a device (customer, site and device preselected);
  - site, title, severity, start (anything but the future) and description;
  - involved devices as a checklist with their vendor icons;
  - title, summary and severity can be prefilled from the address;
  - once created, the incident page opens.
- **Shared code.** `incidents.create_incident_record` validates, links the devices and audits `INCIDENT_CREATED`; the classic form uses it with the same messages. Errors carry codes (`customer`, `title`, `started`, `devices`).
- **API.**
  - `GET /api/ui/v1/incident-form` (`incidents.write`; not under `/incidents/` because of `/incidents/{id}`);
  - `POST /api/ui/v1/incidents` (`incidents.write` + CSRF).

## 0.49.161 — 2026-10-15

New interface (React) — vendor firmware catalogs, phase 7y
- **Firmware catalogs** (`/app/firmware/catalogs`), opened from the firmware page. There is one catalog per manufacturer other than MikroTik, with:
  - the brand icon;
  - devices to update and devices with security fixes;
  - the official download page and a link to the devices.
  - Releases list version, models or platform, date, source (UISP, official API, operator), security flag and notes.
- **Changes** (`firmware.execute`): add an operator release, delete operator releases, and refresh the sources (UISP and the official Ubiquiti update service).
- **Shared code.** `vendor_firmware.add_release_record` and `delete_release_record`; the classic page uses them with the same messages. Errors carry codes (`brand`, `version`, `url`, `date`, `exists`, `not_operator`).
- **API.**
  - `GET /api/ui/v1/firmware/catalogs` (`firmware.read`);
  - `POST .../catalogs/releases`, `.../releases/{id}/delete`, `.../catalogs/refresh` (`firmware.execute` + CSRF).

## 0.49.160 — 2026-10-15

New interface (React) — firmware suggestions, phase 7x
- **RouterOS upgrade suggestions** (`/app/firmware/suggestions`), opened from the firmware page (the customer filter is kept):
  - MikroTik devices behind their channel, with the next safe step (ready for a plan, legacy upgrade, firmware check needed, plan open, soaking, blocked) and its reason;
  - filters by step and customer;
  - security releases are flagged with their reasons; devices keep their vendor icon;
  - batch actions on up to 25 selected devices: queue firmware checks, or create plans (`firmware.execute`). Plans only queue the pre-upgrade backup and wait for approval. Skipped devices are listed with a translated reason.
- **Shared code.** `firmware_suggestions.apply_record` (the classic page uses it with the same messages) returns skipped devices as `{id, name, code, message}`.
- **API.**
  - `GET /api/ui/v1/firmware/suggestions` (`firmware.read`);
  - `POST /api/ui/v1/firmware/suggestions/apply` (CSRF; `firmware.execute` for plans; codes `action`, `empty`, `too_many`).

## 0.49.159 — 2026-10-15

New interface (React) — lifecycle catalog, phase 7w
- **EOL/EOS catalog** (`/app/lifecycle/catalog`), opened from the lifecycle page and from the device lifecycle tab:
  - every model with its aliases, today's state, EOL/EOS dates, source (with link and verification date) and correlated devices;
  - search by model or notes, and a vendor filter;
  - CSV export, and a whole-file CSV import (nothing is written when a row is invalid).
- **Model form** (`.../new`, `.../{id}/edit`): vendor, model, aliases, EOL, EOS, source, link, verification date, notes, with translated checks. Saving re-correlates devices. Records can be deleted.
- **Prefill.** "Add to catalog" on the lifecycle page, and "Add this model to the catalog" on a device whose model is not in the catalog, open the form already filled with vendor and model. This fixes the broken catalog link on the device lifecycle tab.
- **Dates.** Shared `formatDate`: date-only values never shift with the timezone.
- **Shared code.**
  - `lifecycle_catalog.save_record`, `delete_record`, `import_record`, used by the classic pages too;
  - validation raises `LifecycleInputError` with codes (`required`, `source`, `evidence`, `evidence_future`, `date_invalid`, `eos_before_eol`, `url`, `duplicate`).
- **API.**
  - `GET /api/ui/v1/lifecycle/catalog` (`security.read`);
  - `GET .../catalog/form`, `POST .../catalog`, `POST .../catalog/{id}/delete`, `POST .../catalog/import` (`lifecycle.manage`, CSRF for changes).

## 0.49.158 — 2026-10-15

New interface (React) — vulnerability finding, phase 7v
- **Vulnerability on a device** (`/app/vulnerabilities/findings/{id}`), opened from the state of each impacted device on the advisory page:
  - **Status:** installed and fixed version, affected range with confidence, detection date, exception expiry, resolution. The device keeps its vendor icon, and MikroTik devices link to the firmware plan.
  - **Status change** (`security.remediate`): only the transitions the server allows. An exception needs a reason and an expiry within one year. An NVD finding closes by itself, while a manual advisory can be resolved with a note.
  - **History** of every change, manual or automatic.
- **Shared code.** `vulnerability_remediation.finding_status_record`; the classic page uses it with the same messages. `TransitionError` is now an `InputError` with codes (`status`, `same`, `managed`, `not_allowed`, `reason`, `until`, `until_invalid`, `until_range`, `resolution`).
- **API.**
  - `GET /api/ui/v1/vulnerabilities/findings/{id}` (`security.read`);
  - `POST .../findings/{id}/status` (`security.remediate` + CSRF);
  - the advisory API no longer returns the classic `finding_url`.

## 0.49.157 — 2026-10-15

New interface (React) — compliance baselines, phase 7u
- **Baselines** (`/app/compliance/baselines`), opened from the compliance page: every baseline from global to device, with its scope (the device keeps its vendor icon), the controls it sets, version, state and last update. With no baseline at all, `compliance.manage` users can create the NSM default global baseline.
- **Baseline form** (`/app/compliance/baselines/new`, `.../{id}/edit`): name, scope (global, vendor, customer, site, device), description, enabled; each control set to inherit, enabled or disabled, with its parameters. Saving creates a new version and re-evaluates compliance.
- The baseline version on the compliance result page now opens the React form.
- **Shared code.** `compliance.save_baseline_record` and `create_default_baseline_record`; the classic pages use them with the same messages. Errors carry codes (`name_scope`, `vendor`, `customer`, `site`, `device`, `param`, `no_controls`, `exists`).
- **API.**
  - `GET /api/ui/v1/compliance/baselines` (`compliance.read`);
  - `GET .../baselines/form`, `POST .../baselines`, `POST .../baselines/default` (`compliance.manage`, CSRF for changes).

## 0.49.156 — 2026-10-15

New interface (React) — compliance result, phase 7t
- **Compliance result** (`/app/compliance/results/{id}`), opened from the compliance matrix and from the device Compliance tab (the control name and "Manage"):
  - **Outcome:** evidence, evaluation date, unchanged-since date, baseline version, who acknowledged it, and the active exception with its reason;
  - **Handling** (`compliance.manage`, failing results only): acknowledge with a note, record an exception with reason and expiry (at most one year), revoke it;
  - **History:** outcome changes and operator decisions.
  - The device is shown with its vendor icon.
- **Shared code.** `compliance_findings.result_action_record` validates, records the history, audits and syncs the Action Center issue; the classic page uses it and keeps its messages. Errors carry codes (`not_fail`, `reason`, `until`, `until_range`, `no_exception`, `action`).
- **API.**
  - `GET /api/ui/v1/compliance/results/{id}` (`compliance.read`);
  - `POST /api/ui/v1/compliance/results/{id}/acknowledge|exception|revoke` (`compliance.manage` + CSRF).

## 0.49.155 — 2026-10-15

New interface (React) — backup policy forms, phase 7s
- **Policy form** (`/app/backups/policies/new`, `.../{id}/edit`, `.../{id}/clone`):
  - **Name and target:** all devices, a vendor, a customer, one of its sites or a single device. Sites and devices are filtered on the chosen customer, and the chosen device shows its vendor icon.
  - **Schedule:** every 6 hours, daily, weekly or monthly, with the time, weekday or day of the month.
  - **What to back up:** only the options of the target's vendor, or every vendor's options for mixed scopes.
  - **Retention and retries.**
  - Errors are translated.
- **Customer context.** Opened with `?customer=` (and optionally `device=` / `site=`), the form only allows that customer, its sites and devices, as the classic customer form does.
- **Edit page:**
  - pause / resume;
  - duplicate;
  - delete, confirmed by typing the policy name.
- **Links:**
  - Backup overview: "+ Policy" and the policy names, plus "Create policy" on devices without a policy;
  - device Backup tab: edit the device's policy, or create one for the device.
- **Shared code.**
  - `backup_core.create_policy_record`, `update_policy_record`, `toggle_policy_record`, `delete_policy_record`, `customer_policy_state`;
  - the classic handlers (including the customer form) use them with the same messages;
  - validation errors in `backup_core` and `backup_scope_guard` carry codes.
- **API.** `/api/ui/v1/backup-policies` (`backup.configure` + CSRF): `GET /form`, `POST`, `PUT /{id}`, `POST /{id}/toggle`, `POST /{id}/delete`.

## 0.49.154 — 2026-10-15

New interface (React) — backup restore tests, phase 7r
- **Restore test of a file** (`/app/devices/{id}/backups/artifacts/{artifact_id}/restore-test`), opened from "Restore test" in the Backup tab and in the export viewer:
  - integrity re-checked now on the archived file (present, size, SHA-256) and the source RouterOS version;
  - outcome form (`backup.execute`): method, target, target RouterOS version, result, date and notes, with the classic checks. "Passed" cannot be chosen for a file that is not intact;
  - history of the file.
- **Restore test history** (`/app/devices/{id}/backups/restore-tests`): every test of the device, with who performed it. The file identity is kept after retention removes the file.
- **Shared code.** `backup_restore_tests.record_restore_test` validates, stores, syncs the Action Center issue and audits; the classic form uses it and keeps its messages. Validation errors now carry codes (`method`, `result`, `target`, `performed_at`, `performed_future`, `integrity`).
- **API.**
  - `GET /api/ui/v1/devices/{id}/backups/restore-tests` (`backup.read`);
  - `GET/POST /api/ui/v1/devices/{id}/backups/artifacts/{artifact_id}/restore-test` (`backup.read` / `backup.execute` + CSRF);
  - the device backup list no longer returns the classic `restore_url` / `restore_tests` links.

## 0.49.153 — 2026-10-15

New interface (React) — backup text exports, phase 7q
- **Export viewer** (`/app/devices/{id}/backups/artifacts/{artifact_id}`), opened from "Open / compare" in the device Backup tab and from "View" / "Diff" in the configuration history:
  - read-only RouterOS export with line numbers, a search box that keeps the original line numbers, size and SHA-256, download and restore-test links;
  - comparison with any older text export of the same device: added / removed / unchanged counts, changes only with 3 lines of context (or the whole file), a notice when the diff is truncated;
  - binary, too large and invalid comparisons show a translated message.
- The Backup tab stays highlighted on its sub-pages.
- **Shared code.** `device_backup_explorer.artifact_text_record` reads, diffs and audits the export (`BACKUP_EXPORT_VIEWED` / `BACKUP_EXPORT_DIFF_VIEWED`); the classic viewer uses it and behaves as before.
- **API.**
  - `GET /api/ui/v1/devices/{id}/backups/artifacts/{artifact_id}?against=` (`backup.read`); errors are 422 `binary`, `too_large`, `compare_id`, `compare_binary`;
  - the device backup list now returns `viewable` instead of the classic `view_url`.

## 0.49.152 — 2026-10-15

New interface (React) — device lifecycle, phase 7p
- **Device lifecycle** (`/app/devices/{id}/lifecycle`), opened from the lifecycle field on the device overview and from the lifecycle list:
  - **Value:** state, EOL and EOS dates, source and verification date, with their origin (catalog or manual).
  - **Catalog:** the correlation result for the model (match, ambiguous, missing).
  - **Manual value:** the classic checks with translated messages, plus a button to go back to the catalog.
  - **End-of-life decisions:** for an EOL or EOS device, plan the replacement, record an exception, record the replacement (the new device shows its vendor icon) or the decommissioning, and reopen. Every decision keeps its history.
- **Links.** The agent field on the device overview now opens the Agent tab of the new interface.
- **Shared code.** The lifecycle changes are now shared functions, used by both interfaces; the classic page behaves as before, with the same messages.
  - `lifecycle_catalog.set_manual_record`, `use_catalog_record`;
  - `lifecycle_remediation.update_record`.
- **Fix.** `InputError` prints its message alone, so classic pages that show `str(exc)` keep the plain text.
- **API.**
  - `GET /api/ui/v1/devices/{id}/lifecycle` (`security.read`);
  - `POST .../lifecycle`, `.../lifecycle/catalog`, `.../lifecycle/remediation` (`lifecycle.manage` + CSRF).

## 0.49.151 — 2026-10-15

New interface (React) — cnMaestro and GenieACS pages of a device, phase 7o
- **cnMaestro tab** (`/app/devices/{id}/cnmaestro`):
  - link state, with the name, type, network, tower and state read from cnMaestro;
  - the last values with their history charts, from 24 hours to 90 days.
- **ACS tab** (`/app/devices/{id}/genieacs`, TR-069 CPEs):
  - NSM record, connector state and the inventory read from GenieACS;
  - «Search GenieACS» looks up the serial first, then the MAC, without changing anything; then come the association and «Refresh from GenieACS». The NSM customer and site never change.
- **Charts.** The UISP and cnMaestro charts now share the same component.
- **Shared code.** The GenieACS association and refresh are now shared functions, used by both interfaces; the classic page behaves as before.
  - `genieacs_connector.associate_record`, `refresh_record`.
- **API.**
  - `GET /api/ui/v1/devices/{id}/cnmaestro` (`devices.read`), `.../cnmaestro/metrics` (`monitoring.read`);
  - `GET /api/ui/v1/devices/{id}/genieacs` (`devices.read`);
  - `POST .../genieacs/preview`, `/associate`, `/refresh` (`devices.read` / `devices.write` + CSRF).

## 0.49.150 — 2026-10-15

New interface (React) — UISP page of a device, phase 7n
- **UISP tab** (`/app/devices/{id}/uisp`, Ubiquiti):
  - **Record and connector:** the NSM record (customer, site, alias, MAC, UISP ID) and the connector state.
  - **Association:** «Search UISP» finds the exact MAC without changing anything; then come the association and «Refresh from UISP», which keeps the NSM customer and site.
  - **Charts:** signal, link capacity, CPU/RAM and stations over 24 hours to 90 days, plus current, minimum, average and maximum values.
  - **Firmware:** the versions seen by UISP and the compatibility warning.
  - **Operations through UISP:**
    - configuration backup archived in NSM;
    - reboot, confirmed with the device name and verified by NSM;
    - interfaces and events read on request.
- **Shared code.** The UISP association and refresh are now shared functions, used by both interfaces; the classic page behaves as before.
  - `uisp_connector.associate_record`, `refresh_record`.

  The UISP operations reuse `uisp_operations`.
- **API.** A new module, `app/ui_device_connectors.py`:
  - `GET /api/ui/v1/devices/{id}/uisp` (`devices.read`);
  - `GET .../uisp/metrics` (`monitoring.read`);
  - `POST .../uisp/preview`, `/associate`, `/refresh`, `/reboot`, `/backup`, `/interfaces`, `/events`, with the classic permissions and the CSRF header.

## 0.49.149 — 2026-10-15

New interface (React) — RouterOS upgrade plan, phase 7l
- **Firmware tab** (`/app/devices/{id}/firmware-upgrade`), opened from the device tabs and from the firmware worklist:
  - **Prerequisites:** installed and available version, channel, free space, last check and the MikroTik catalogue with security reasons.
  - **Plan steps:** plan creation with the pre-upgrade backup, approval, package download and activation, each a separate step with the same literal confirmation as the classic page (`UPGRADE`, `DOWNLOAD`, `ACTIVATE` + version).
  - **Progress:** the five gates with their state, the plan dates and the post-reboot verification.
  - **Cancel:** the plan can be cancelled with a confirmation; its pending jobs are withdrawn.
  - **Legacy agents:** they get their own upgrade, with the backup acknowledgement and the `AGGIORNA <version>` confirmation.
- **Worklist.** «Prepare upgrade» in the firmware worklist now creates the plan and opens it in the new interface. The new interface no longer sends any classic form.
- **Shared code.** The plan steps are now shared functions, used by both interfaces; the classic pages behave as before, with the same messages and status codes.
  - `firmware_upgrade_planner.latest_plan`, `approve_record`, `cancel_record`;
  - `firmware_package_staging.stage_record`;
  - `firmware_activation.activate_record`;
  - `mikrotik_legacy_operations.queue_legacy_upgrade_record`.
- **API.**
  - `GET /api/ui/v1/devices/{id}/firmware-upgrade` (`firmware.read`);
  - `POST .../firmware-upgrade/plan`, `.../firmware-upgrade/{plan_id}/approve|stage|activate|cancel` and `.../legacy-upgrade` (`firmware.execute` + CSRF).

## 0.49.148 — 2026-10-15

New interface (React) — controlled reboot and RouterBOOT, phase 7k
- **Controlled reboot** (`/app/devices/{id}/reboot`, from «Reboot» on the device page):
  - shows what blocks the reboot (legacy agent, profile, credential, offline device, operation in progress), the last request with uptime before and after, and the history;
  - the reboot needs a reason and the device name typed as confirmation.
- **RouterBOOT** (`/app/devices/{id}/routerboot`):
  - active and available bootloader, workflow state and last check;
  - the four steps (read, flash, reboot, verification) with their state;
  - flash and reboot are separate actions, each confirmed with the same literal text as the classic page.
- **Shared code.** Reboot and RouterBOOT actions are now shared functions, used by both interfaces; the classic pages behave as before, with the same messages and status codes.
  - `mikrotik_device_reboot.queue_reboot_record`, `reboot_history`;
  - `routerboot_lifecycle.stage_record`, `reboot_record`.

  RouterBOOT errors now carry a code (`InputError`).
- **API.**
  - `GET /api/ui/v1/devices/{id}/reboot`, `/routerboot` (`firmware.read`);
  - `POST /api/ui/v1/devices/{id}/reboot`, `/routerboot/stage`, `/routerboot/reboot` (`firmware.execute` + CSRF).

## 0.49.147 — 2026-10-15

New interface (React) — MikroTik Configuration tab and export history, phase 7j
- **Configuration tab** (`/app/devices/{id}/configuration`, MikroTik). It shows the latest agent read of each section, with the same filters as the classic page:
  - **Sections:** resources, interfaces, IP addresses, routes, firewall, DHCP leases, PPP/VPN and warning/error log;
  - **Each section:** counters, search, state/kind/action filters, and the time of the last read;
  - **Refresh section:** asks the agent for a new read, only for an online device; a section already queued is reported.
- **Configuration history** (`/app/devices/{id}/configuration/history`):
  - archived RouterOS exports with the change from the previous one, the approved baseline and the open drift;
  - «Set as baseline» resolves the open drift issue;
  - view and differences open the export viewer.
- **Fix.** The definition list of the Exposure tab now lays out its fields correctly.
- **Shared code.** Configuration refresh and baseline approval are now shared functions, used by both interfaces; the classic pages behave as before.
  - `mikrotik_workspace.queue_snapshot_record`;
  - `config_drift.set_baseline_record`.

  The Resources section now gives its fields a key as well as their label.
- **API.**
  - `GET /api/ui/v1/devices/{id}/configuration` (`devices.read`);
  - `POST .../configuration/refresh` (`devices.write` + CSRF);
  - `GET .../configuration/history` (`backup.read`);
  - `POST .../configuration/history/{artifact_id}/baseline` (`backup.configure` + CSRF).

## 0.49.146 — 2026-10-15

New interface (React) — device Activity, Exposure and Compliance tabs, phase 7i
- **Activity tab** (`/app/devices/{id}/jobs`):
  - agent jobs, newest first, with type, state, creation and completion time, attempts and error, plus the number of active jobs;
  - search, state and type filters, and pages of 50;
  - payloads and results are never shown;
  - a link opens the activity log filtered on the device.
- **Exposure tab** (`/app/devices/{id}/exposure`), same content as the classic tab:
  - services reachable from the WAN, as read by the agent or probed from outside, with state, severity and reason;
  - port forwards, the devices that share the IP (with their vendor icons) and the last check;
  - «Check now» and the public IP to probe, for whoever can remediate.
- **Compliance tab** (`/app/devices/{id}/compliance`): results of the effective baseline, failures first, with exceptions and acknowledgements; failed controls link to their handling page.
- **Shared code.** The Exposure tab data and actions are now shared functions, used by both interfaces; the classic page behaves as before.
  - `device_exposure.page_state`, `request_check_record`, `set_target_record`.
- **API.** A new module, `app/ui_device_tabs.py`:
  - `GET /api/ui/v1/devices/{id}/jobs` (`devices.read`);
  - `GET /api/ui/v1/devices/{id}/exposure` (`security.read`);
  - `POST .../exposure/check`, `.../exposure/target` (`security.remediate` + CSRF);
  - `GET /api/ui/v1/devices/{id}/compliance` (`compliance.read`).

## 0.49.145 — 2026-10-15

New interface (React) — device CSV import, phase 7h
- **CSV import in `/app/devices/import`**, from «Import CSV» in the device list (administrators):
  - **Check first:** a check that saves nothing shows every row with its outcome (ready, already present, error with the reason), the vendor icon, customer, site and address.
  - **Import:** the «Import N devices» button imports only the ready rows, and the imported devices link to their page.
  - **Limits:** the template download, the limits and the columns are shown on the page, which also says that credentials are never accepted.
- **Shared code.** The check and the import are now one shared function (`device_csv_import.process_upload`), used by both interfaces; the classic page behaves as before.
- **API.** `GET` and `POST /api/ui/v1/device-import` (administrators):
  - `POST` takes multipart `csv_file` + `mode` (`validate` or `import`) and needs the CSRF header;
  - an unreadable file returns 422 `file_invalid` with the reason.

## 0.49.144 — 2026-10-15

New interface (React) — device management, phase 7g
- **Manage device in `/app/devices/{id}/manage`**, from «Manage» on the device page:
  - **Assignment:** change the alias, the customer and the site. Only the customers you can see are offered, and only the chosen customer's sites. When the device changes customer, a warning says that its open issues and notifications follow it.
  - **Delete:** the button only becomes active once you type the device name; backup files are removed and a snapshot stays in the activity log.
- **Manage devices of a customer in `/app/customers/{id}/devices/manage`**, from «Manage devices» on the customer page:
  - the devices are listed with their vendor icon, site, IP/MAC and state, and can be filtered;
  - you can select several devices, then move them to another customer and site, or delete them by typing how many they are.
- **Messages.** Validation messages are translated.
- **Shared code.** Device changes now go through shared functions, used by both interfaces; the classic pages behave as before, with the same messages.
  - `crud_extension.update_device_record`, `delete_device_record`, `bulk_devices_record`.
- **API.** All routes need `devices.write`; the `POST` routes need the CSRF header. Validation errors are 422 `{code, message}`.
  - `GET /api/ui/v1/devices/{id}/manage`;
  - `POST /api/ui/v1/devices/{id}`, `/devices/{id}/delete`;
  - `GET /api/ui/v1/customers/{id}/devices/manage`;
  - `POST /api/ui/v1/customers/{id}/devices/bulk`.

## 0.49.143 — 2026-10-15

New interface (React) — new device, phase 7f
- **New device in `/app/customers/{id}/devices/new`**, opened from «+ Device» on the customer page. It follows the classic form in three steps (type, organisation, association), and the help text changes with the vendor:
  - **MikroTik:** the device is created waiting for enrollment. The page then shows the RouterOS command that installs the agent, with a copy button. The command is shown only once; its token is stored hashed.
  - **Ubiquiti and Cambium:** NSM looks the device up in UISP or cnMaestro by MAC or serial. If the connector is not configured, the page says so before submitting and links to its settings.
  - **TP-Link (TR-069):** MAC or serial is required.
  - **Other vendor:** manufacturer (used for CVE matching), MAC, serial, model, management IP and firmware.

  Errors are translated: invalid MAC, site of another customer, MAC already used, connector missing.
- **Agent reinstall** from the device Agent tab now stays in the new interface: it creates a new one-shot enrollment and shows the command there.
- **Shared code.** Device creation, the enrollment command and the agent reinstall are now shared functions, used by both interfaces; the classic pages behave as before.
  - `main.create_device_record`, `main.enrollment_command`;
  - `agent_ui.reinstall_record`.

  Connector onboarding errors now carry a code (`input_errors.InputError`); the classic messages are unchanged.
- **API.**
  - `GET /api/ui/v1/customers/{id}/devices/new` and `POST /api/ui/v1/customers/{id}/devices` need `devices.write`;
  - `POST /api/ui/v1/devices/{id}/agent/reinstall` needs `devices.enroll`.

  The `POST` routes need the CSRF header; answers that contain an enrollment command are `no-store`.

## 0.49.142 — 2026-10-15

New interface (React) — customer Security, Backup and Activity tabs, phase 7e
- **Security tab in `/app/customers/{id}/security`**, with the same data as the classic tab:
  - **Summary:** counters for severe CVEs, firmware, end of life, issues and compliance.
  - **Severe CVEs:** each open critical or high CVE with its device (vendor icon), CVSS score, installed and fixed version.
  - **Firmware:** devices to update, with current and recommended version.
  - **End of life:** EOL and EOS devices, with their date.
  - **Issues:** open security, firmware and lifecycle issues.
  - **Compliance:** compliance summary with the failing controls.

  Each section links to the full page (vulnerabilities and firmware filtered on the customer).
- **Backup and Activity tabs** now open the backup overview and the activity log of the new interface, filtered on the customer. Every customer tab is now in the new interface.
- **Other links.** The «Incidents», «Action Center» and «All events» links of the customer page now stay in the new interface, filtered on the customer.
- **Shared code.** The classic Security tab now reads its data from `customer_tabs.security_data`, which the API uses too; the classic page behaves as before.
- **API.** `GET /api/ui/v1/customers/{id}/security` needs `security.read`; customer scope applies.

## 0.49.141 — 2026-10-15

New interface (React) — customer and site forms, phase 7d
- **New customer** in `/app/customers/new`, opened from «New customer» in the customer list. It asks for a name, an optional unique code and notes, then opens the new customer.
- **Manage customer** in `/app/customers/{id}/edit`:
  - **Details:** edit name, code and notes.
  - **Delete:** the page shows how many devices and sites will go. The button only becomes active once you type the exact customer name; the server checks the name again, and a snapshot stays in the activity log.
- **Sites, in the customer's Sites tab, without leaving the page:**
  - add a site;
  - edit name, address and notes;
  - delete it, after a confirmation that says how many devices stay in the customer without a site.
- **Messages.** Validation messages are translated: name required, customer code already used, confirmation name does not match.
- **Shared code.** Customer and site changes now go through shared functions, used by both interfaces; the classic forms behave as before, except that a blank customer or site name is now refused when created too.
  - `main.create_customer_record`, `create_site_record`;
  - `crud_extension.update_customer_record`, `delete_customer_record`, `update_site_record`, `delete_site_record`.
- **API.** All routes need `customers.write` and the CSRF header. Validation errors are 422 `{code, message}`.
  - `POST /api/ui/v1/customers`, `/customers/{id}`, `/customers/{id}/delete`;
  - `POST /api/ui/v1/customers/{id}/sites`, `/sites/{site_id}`, `/sites/{site_id}/delete`.

## 0.49.140 — 2026-10-14

New interface (React) — profile, phase 7c
- **Profile in `/app/profile`**, opened from your name in the top bar:
  - **Account:** name, username, e-mail, role, last sign-in.
  - **Password change:** the classic checks with translated messages. This session stays signed in (it is re-issued with a new CSRF token) and the other sessions end.
  - **Sessions:** sign out of the other sessions, with a confirmation.
  - **Appearance:** light or dark theme, saved on the account.
  - **Two-step verification:** setup with QR code and manual key, then confirmation with the app code. Recovery codes are shown once, with a copy button. You can also generate new codes (password required) or turn the feature off (password + app code).
  - **Notifications:** e-mail address; for e-mail, Telegram and Slack, the on/off switch, minimum level and categories. The Telegram chat ID is shown; the Slack webhook is never sent back to the browser, and an empty field keeps it. You can also send a test message, set the vulnerability newsletter and see the latest deliveries.
- **Shared code.** The profile logic is now made of shared functions, used by both interfaces; the classic profile page behaves as before:
  - `ui_extension.change_own_password_record`;
  - `login_security.revoke_own_record`;
  - `two_factor.setup_record`, `enable_record`, `regenerate_record` and `disable_record`;
  - `notification_delivery.save_preferences_record` and `send_test_record`;
  - `notification_digest.set_digest`.
- **API.** All routes are open to any signed-in user; the `POST` routes need the CSRF header. Validation errors return 422 with the classic error codes.
  - `GET /api/ui/v1/profile` (`no-store`);
  - `POST /api/ui/v1/profile/password`, `/sessions/revoke`;
  - `POST /api/ui/v1/profile/2fa/setup`, `/2fa/enable`, `/2fa/recovery`, `/2fa/disable` (recovery codes `no-store`);
  - `POST /api/ui/v1/profile/notifications`, `/notifications/test`, `/notifications/digest`.

## 0.49.139 — 2026-10-14

New interface (React) — global search and notifications, phase 7b
- **Search box in the top bar** of the new interface. It opens `/app/search?q=…`, which finds devices, customers and sites by name, model, serial number, MAC (in any notation), IP, external ID or address:
  - every device shows its vendor icon, customer, site, identifiers and state;
  - customers and sites link to their pages;
  - the results are the same as the classic global search, and the customer scope still applies.
- **Notification bell** in the top bar, with the unread count refreshed every minute.
- **`/app/notifications`** lists active notifications, newest first, and can show only unread ones. A notification can show its device (with vendor icon), open its advisory or its source, and be marked as read; all of them can be marked as read at once. The read state stays personal to each user. Only web links and in-app paths are offered as a source.
- **Shared code.** The global search and the notification read logic are now shared functions (`main.search_records`, `notification_rows`, `mark_notification_read_record`, `mark_all_notifications_read_record`), used by both interfaces; the classic pages behave as before.
- **API.** All routes are open to any signed-in user; the `POST` routes need the CSRF header:
  - `GET /api/ui/v1/search`;
  - `GET /api/ui/v1/notifications` and `/notifications/summary`;
  - `POST /api/ui/v1/notifications/{id}/read` and `/notifications/read-all`.

## 0.49.138 — 2026-10-14

New interface (React) — dashboard, phase 7a
- **Dashboard in `/app/`.** The home of the new interface was a list of links; it is now the full dashboard:
  - **KPIs:** devices, online, offline, protected backups, severe CVEs, firmware to update, Action Center issues and end-of-life devices. Each one opens the matching page;
  - **Coverage:** availability and backup coverage rings, with percentages formatted for the chosen language;
  - **Needs attention:** critical issues, severe CVEs, failed backups, firmware to update and pending devices;
  - **Fleet distribution:** the most common vendors, firmware versions and models;
  - **Latest advisories:** recent CVEs with severity and the number of affected devices, linked to the advisory page;
  - **Recent activity:** the latest audit events, linked to the activity log.
  The page refreshes every 30 seconds.
- **Shared code.** The classic dashboard now reads its data from `dashboard_ui.dashboard_data`, which the API uses too; the classic page behaves as before.
- **API.** `GET /api/ui/v1/dashboard` is open to any signed-in user.
- **Phase 7 starts.** The remaining classic pages are migrated one at a time before the classic interface is retired.

## 0.49.137 — 2026-10-14

New interface (React) — UISP onboarding, phase 6q
- **UISP onboarding in `/app/integrations/uisp/onboarding`**, opened from the UISP connector page:
  - **States:** UISP devices by NSM state (new, to link to an existing record, already in NSM, not importable), with counts, search and UISP site filter; each device shows the Ubiquiti icon and, when it is already in NSM, the linked device;
  - **Selection:** devices are selected on the page, then the operator picks the NSM customer and optional site (only that customer's sites are offered);
  - **Preview and confirmation:** the preview shows what will be created, linked or skipped. On confirmation the UISP list is read again, so devices changed in the meantime are skipped and reported; linked devices stay in their customer. Afterwards the page links to the customer.
- The onboarding preview and commit are now shared functions (`uisp_onboarding.target`/`preview_plan`/`commit_selection`), used by both interfaces; the classic page behaves as before.
- **Phase 6 complete.** Every administration page, integration and connector is now in the new interface.
- **API.** All routes need `devices.write`; the `POST` routes also need the CSRF header:
  - `GET /api/ui/v1/integrations/uisp/onboarding`;
  - `POST /api/ui/v1/integrations/uisp/onboarding/preview`, `/commit`.

## 0.49.136 — 2026-10-14

New interface (React) — UISP connector, phase 6p
- **UISP in `/app/admin/integrations/uisp`**, in the integration sub-tabs; it is also where the *Integrations* tab of the administration area now opens:
  - **Connection:** console URL, read-only API token (encrypted, kept when empty, never sent back to the browser), connector enabled, sync interval, TLS check with its warning;
  - **Connector status:** endpoint, read-only mode, TLS, last test and outcome, last sync, last error, with *Test connection*;
  - **Periodic sync:** state, interval, last success, next attempt, consecutive errors, last outcome (linked, updated, missing, conflicts), with *Synchronise now*;
  - **Linking flow** of an Ubiquiti device.
- *Device onboarding* still opens the classic page.
- With this, the four connectors (UISP, GenieACS, Zabbix, cnMaestro), NVD and the RouterOS catalogue are configured in the new interface. The integrations hub opens all of them.
- Saving the connector, the connection test and the manual sync are now shared functions (`uisp_connector.save_config`/`test_connection`/`sync_interval`, `uisp_sync.manual_sync`), used by both interfaces; the classic page behaves as before.
- **API.** All routes need `users.manage`; the `POST` routes also need the CSRF header:
  - `GET /api/ui/v1/admin/integrations/uisp`;
  - `POST /api/ui/v1/admin/integrations/uisp`, `/test`, `/sync`.

## 0.49.135 — 2026-10-14

New interface (React) — GenieACS connector, phase 6o
- **GenieACS / TR-069 in `/app/admin/integrations/genieacs`**, in the integration sub-tabs:
  - **Built-in GenieACS:** when the Docker stack runs it, *Use built-in GenieACS* links NSM to it; otherwise the page shows how to enable it;
  - **External GenieACS:** NBI URL (localhost and loopback refused), authentication (none, HTTP Basic, Bearer), password or token (encrypted, kept when empty, never sent back to the browser), MAC parameter paths, online window, connector enabled, TLS check;
  - **Connector status:** endpoint, read-only mode, authentication, last test, outcome, last CPE read and last error, with *Test connection*;
  - **Linking flow** of a CPE between NSM and GenieACS.
- The integrations hub opens the new page from the GenieACS card.
- Saving the connector, linking the built-in GenieACS and the connection test are now shared functions (`genieacs_connector.save_config`/`use_internal`/`test_connection`/`auth_info`), used by both interfaces; the classic page behaves as before.
- **API.** All routes need `users.manage`; the `POST` routes also need the CSRF header:
  - `GET /api/ui/v1/admin/integrations/genieacs`;
  - `POST /api/ui/v1/admin/integrations/genieacs`, `/internal`, `/test`.

## 0.49.134 — 2026-10-14

New interface (React) — Zabbix connector, phase 6n
- **Zabbix in `/app/admin/integrations/zabbix`**, in the integration sub-tabs:
  - **Status:** Zabbix version and test outcome, last sync, hosts managed by NSM (created, updated, disabled), open problems, excluded devices, missing templates and the errors of the last sync;
  - **Devices with a shared IP:** each device with its manufacturer icon and the address Zabbix must use behind the customer's NAT, editable on the row;
  - **Connection:** URL, API token or username and password (encrypted, kept when empty, never sent back to the browser), TLS check;
  - **Hosts:** group prefix, agent or SNMP interface, SNMP community, templates per vendor;
  - **Scope:** all devices or selected customers, active sync;
  - **Actions:** *Test connection*, *Synchronise now* and *Open Zabbix*, with the authentication notes per Zabbix version.
- The integrations hub opens the new page from the Zabbix card.
- Saving the settings, the connection test, the manual sync and the per-device Zabbix address are now shared functions (`zabbix_connector.save_config`/`test_connection`/`sync_now`/`set_device_ip`/`credential_info`), used by both interfaces. Unreadable stored credentials now give a clear test error instead of a server error.
- **API.** All routes need `users.manage`; the `POST` routes also need the CSRF header, and the device address also needs `devices.write`:
  - `GET /api/ui/v1/admin/integrations/zabbix`;
  - `POST /api/ui/v1/admin/integrations/zabbix`, `/test`, `/sync`, `/devices/{id}/ip`.

## 0.49.133 — 2026-10-14

New interface (React) — cnMaestro connector, phase 6m
- **cnMaestro in `/app/admin/integrations/cnmaestro`**, in the integration sub-tabs:
  - **Status:** last sync with its outcome, devices read from cnMaestro, linked Cambium devices and those without a match, statistics errors, the last failed test;
  - **Connection:** URL, client ID, client secret (encrypted, kept when empty, never sent back to the browser), TLS check, active sync;
  - **Actions:** *Test connection* and *Synchronise now*, with the result shown on the page.
- The integrations hub opens the new page from the cnMaestro card.
- Saving the connection and the connection test are now shared functions (`cnmaestro_connector.save_config`/`test_connection`/`client_id_of`), used by both interfaces. Unreadable stored credentials now give a clear test error instead of a server error.
- **API.** All routes need `users.manage`; the `POST` routes also need the CSRF header:
  - `GET /api/ui/v1/admin/integrations/cnmaestro`;
  - `POST /api/ui/v1/admin/integrations/cnmaestro`, `/test`, `/sync`.

## 0.49.132 — 2026-10-14

New interface (React) — Demo Lab, phase 6l
- **Demo Lab in `/app/admin/demo`**, in the *Demo Lab* tab of the new administration area:
  - the number of demo customers (code `DEMO-*`);
  - *Generate demo data* when there are none;
  - *Remove demo data*, enabled only after typing DEMO; it removes only demo objects and demo backup files.
- After seeding or clearing, every list of the new interface refreshes.
- Seeding and clearing, each with its audit event, are now shared functions (`demo_ui.run_seed`/`run_clear`), used by both interfaces; the classic page behaves as before.
- **With this, every tab of the administration area is in the new interface.** Only the configuration of the UISP, GenieACS, Zabbix and cnMaestro connectors still opens the classic pages.
- **API.** All routes need `users.manage`; the `POST` routes also need the CSRF header:
  - `GET /api/ui/v1/admin/demo`;
  - `POST /api/ui/v1/admin/demo/seed`, `/clear` (body `{"confirm": "DEMO"}`).

## 0.49.131 — 2026-10-14

New interface (React) — ACS (TR-069), phase 6k
- **ACS administration in `/app/admin/acs`**, in the *ACS* tab of the new administration area:
  - **Status:** link state with GenieACS, the ACS URL the CPEs must use (port 7547) and the GenieACS panel port;
  - **ACS panel:** *Open ACS panel* opens it in a new tab, reserved to NSM administrators, then the GenieACS login;
  - **TP-Link CPE setup guide** (CPE, network, templates, checks in the panel, link in NSM) and the security advice, in four languages;
  - **Provisioning templates:** the presets with their condition, the NSM base provision script, and *Install base templates*. When GenieACS is not linked yet, the page says how to link it.
- Installing the base templates is now a shared function (`acs_admin.install_base_templates`), used by both interfaces; the classic page behaves as before.
- **API.** All routes need `users.manage`; the `POST` route also needs the CSRF header:
  - `GET /api/ui/v1/admin/acs`;
  - `POST /api/ui/v1/admin/acs/templates`.

## 0.49.130 — 2026-10-14

New interface (React) — branding, phase 6j
- **Branding in `/app/admin/branding`**, in the *Branding* tab of the new administration area:
  - **Visual identity:** platform name, tagline and the three colours (primary, sidebar, highlight), each with *Default* to restore it.
  - **Logo:** PNG, JPEG or WebP up to 2 MB, with preview and *Remove logo* (with confirmation). Size and format are checked before upload and again on the server.
- **The new interface now uses the branding.** The sidebar shows the logo next to the platform name, and the three brand colours apply to the new interface as on the classic pages, set as CSS custom properties (allowed by the content security policy). Changes show up at once, without reloading.
- Saving the branding, the logo upload and removal and the colour reset are now shared functions (`ui_extension.save_branding`/`store_logo`/`clear_logo`, `workflow_ui.reset_color`), used by both interfaces; the classic forms behave as before.
- **API.** All routes need `users.manage`; the `POST` routes also need the CSRF header:
  - `GET /api/ui/v1/admin/branding`;
  - `POST /api/ui/v1/admin/branding`, `/logo` (multipart), `/logo/remove`, `/colors/{primary|sidebar|highlight}/reset`.

## 0.49.129 — 2026-10-14

New interface (React) — NVD source and RouterOS catalogue, phase 6i
- **NVD advisories in `/app/admin/integrations/nvd`**, with sub-tabs between the configurable integrations (those not yet migrated open the classic pages):
  - configuration: enable, update interval, API key (encrypted, kept when empty, removable, never sent back to the browser);
  - source status: last outcome, last success, mode, next attempt, imported advisories, open matches, last read, unparseable records. *Update now* and *Full reload* are offered when the source is enabled;
  - matches with the inventory, with *Recompute now* and the matching rules;
  - **coverage per vendor**: devices, assessable, without model, without readable version, NVD products. On the classic page this table was placed inside the page title and did not render; here it is in its own section.
- **RouterOS catalogue in `/app/integrations/routeros`** (`firmware.read`): latest version per channel with security fixes as evidence, observed releases with changelog. *Refresh now* is for administrators and reports channels read, new releases and devices with an update.
- The integrations hub links to both pages in the new interface.
- NVD settings, sync and matching and the catalogue refresh are now shared functions (`advisory_admin.save_source`/`sync_now`/`match_now`, `routeros_catalog.refresh_now`), used by both interfaces; the classic forms behave as before.
- **API.**
  - `GET /api/ui/v1/admin/integrations/nvd` (`users.manage`);
  - `POST /api/ui/v1/admin/integrations/nvd`, `/sync`, `/match` (`users.manage` + CSRF header);
  - `GET /api/ui/v1/integrations/routeros` (`firmware.read`);
  - `POST /api/ui/v1/integrations/routeros/refresh` (`users.manage` + CSRF header).

## 0.49.128 — 2026-10-14

New interface (React) — syslog server, phase 6h
- **Syslog server administration in `/app/admin/syslog`**, in the *Syslog* tab of the new administration area.
- **Overview:**
  - receiver state;
  - lines stored today and from how many devices;
  - lines received, stored and discarded since the last restart;
  - recognition keys;
  - discarded lines by reason;
  - a warning when no allowed network is configured.
- **Settings:**
  - address for the devices, retention of info/notice/debug, allowed networks (one CIDR per line, invalid ones reported), automatic configuration on MikroTik devices with the agent;
  - key-only mode stays mandatory;
  - *Configure all MikroTik devices now* queues the configuration and reports how many devices were queued or skipped.
- **Discarded senders:** count, last line and reason, with *Remove from the list*.
- **Protection:** dedicated database account, container, TLS, integrity, and the host firewall script built from the allowed networks. The per-vendor instructions remain on the classic page and in each device's Syslog tab.
- Saving the settings, clearing a sender and configuring every MikroTik are now shared functions (`device_logs.save_syslog_settings`/`clear_unknown_source`, `mikrotik_syslog_config.configure_all_devices`), used by both interfaces; the classic forms behave as before.
- **API.** All routes need `users.manage`; the `POST` routes also need the CSRF header:
  - `GET /api/ui/v1/admin/syslog`;
  - `POST /api/ui/v1/admin/syslog/settings`, `/unknown/clear`, `/configure-all`.

## 0.49.127 — 2026-10-14

New interface (React) — integrations hub, phase 6g
- **Integrations in `/app/integrations`**, also reachable from the sidebar of the new interface. One card per data source, with the connector's real state, as on the classic hub:
  - MikroTik agent (registered, without heartbeat);
  - UISP (linked devices, last sync, consecutive errors);
  - public API (active keys);
  - RouterOS catalogue;
  - NVD advisories (imported, last update);
  - GenieACS (linked and pending CPEs);
  - cnMaestro (Cambium devices, linked);
  - Zabbix (version, devices pushed, link to the Zabbix frontend).
- **Links.** Administrators get *Configure* or *Manage* on each connector, which opens its configuration on the classic page. The agent fleet, firmware, vulnerability and API key links open the new interface.
- The hub state is now a shared function (`integrations_hub.hub_state`), used by both interfaces; the classic page behaves as before.
- **API.** `GET /api/ui/v1/integrations` (any signed-in user, as the classic hub); connector secrets are never returned.

## 0.49.126 — 2026-10-14

New interface (React) — notifications, phase 6f
- **Notifications in `/app/admin/notifications`**, in the *Notifications* tab of the new administration area.
- **E-mail server (SMTP):**
  - server, port, encryption, user, password, sender, sender name and portal address;
  - the password is encrypted and is kept when the field is left empty; the stored value is never sent back to the browser.
- **Delivery status (24 hours):** sent, queued (with those stuck for over an hour), failed, and active recipients. *Send test e-mail* goes to the given address or to the administrator's e-mail and shows the outcome; the last test is also shown.
- **Telegram and Slack:**
  - the Telegram bot token is encrypted and kept when the field is left empty;
  - saving verifies the bot and shows its name or the error;
  - Slack needs no setup.
- **Delivery log:** the last 100 messages, filtered by all, queued, sent or failed, with recipient, channel, category, severity, attempts and error. *Queue failed deliveries again* puts them back in the queue.
- SMTP settings, the test e-mail, requeueing and the Telegram bot are now shared functions (`notification_delivery.save_smtp_settings`/`test_smtp_send`/`requeue_failed`, `notification_chat.configure_bot`), used by both interfaces; the classic forms behave as before.
- **API.** All routes need `users.manage`; the `POST` routes also need the CSRF header:
  - `GET /api/ui/v1/admin/notifications`;
  - `POST /api/ui/v1/admin/notifications/smtp`, `/smtp/test`, `/telegram`, `/retry`.

## 0.49.125 — 2026-10-14

New interface (React) — system status, phase 6e
- **System status in `/app/admin/system`**, in the *System* tab of the new administration area. It is read-only and shows the same data as the classic page:
  - version, schema revision, database size, free backup space;
  - the worker with its heartbeat and each task's last success, duration, last error and consecutive failures;
  - connectors and data sources;
  - capacity: backup archive, 30-day growth, estimated days left, largest tables, retention per data set;
  - encryption of secrets with the current and previous key;
  - platform database dumps and restore drills.
- The same warnings as the classic page appear when something needs action, for example little free space, a stopped worker, secrets to rotate or unreadable, an old dump or an overdue drill.
- Retention entries and secret groups now carry a stable `key` (and the retention days), so the new interface can show them in every language; the classic page is unchanged.
- **API.** `GET /api/ui/v1/admin/system` (`users.manage`).

## 0.49.124 — 2026-10-14

New interface (React) — API keys, phase 6d
- **API keys in `/app/admin/api-keys`**, in the *API Keys* tab of the new administration area.
- **Create** a key for all customers or one, with an expiry of 30, 90 or 365 days or none, and the read permissions to grant (inventory is always included). The token is shown once, with a copy button; the platform keeps only its fingerprint, and the API answers with `Cache-Control: no-store`.
- **Each key shows:**
  - prefix, permissions, customer, creation, expiry, last use with address, number of requests;
  - its state: active, being replaced, expiring, expired, replaced, revoked;
  - the same review hints as the classic page (expiring, never used, unused for 90 days, no expiry);
  - a summary of the keys that need attention appears above the list.
- **Rotate** with an overlap period (revoke now, 24 hours, 7 days); the new token is shown once.
- **Revoke and delete** with confirmation; a key must be revoked before it can be deleted.
- Key creation, revocation, deletion and rotation are now shared functions (`api_keys.create_key`/`revoke_key`/`delete_key`, `api_key_lifecycle.rotate`), used by both interfaces; the classic forms behave as before. The duplicated creation code in `api_key_scope_extension` now uses the same function.
- **API.** All routes need `users.manage`; the `POST` routes also need the CSRF header:
  - `GET /api/ui/v1/admin/api-keys`;
  - `POST /api/ui/v1/admin/api-keys`;
  - `POST /api/ui/v1/admin/api-keys/{id}/rotate`, `/revoke`, `/delete`.

## 0.49.123 — 2026-10-14

New interface (React) — users, phase 6c
- **Administration in `/app/admin/users`**, also reachable from the sidebar of the new interface. The administration tabs are the same as on the classic page; the ones not yet migrated open the classic pages.
- **Accounts:** each account shows its role, status, e-mail, last sign-in, two-step verification and visible customers.
- **Actions** (same rules as the classic page):
  - change role;
  - choose visible customers (none = all);
  - disable or re-enable;
  - sign out every session;
  - reset two-step verification;
  - an administrator cannot demote, disable, sign out or reset themselves from here.
- **Create a user** with a temporary password; it must meet the platform requirements, and a duplicate username is refused.
- **Also on the page:** failed sign-ins in the last 24 hours, with the throttling limits and remaining block time, and the description of each role.
- User creation, role and status changes, customer scope, session sign-out and second-factor reset are now shared functions (`main.create_user_record`/`toggle_user_record`/`change_user_role_record`, `customer_scope.set_user_scope`, `login_security.admin_revoke`, `two_factor.admin_reset_record`), used by both interfaces; the classic forms behave as before.
- **API.** All routes need `users.manage`; the `POST` routes also need the CSRF header:
  - `GET /api/ui/v1/admin/users`;
  - `POST /api/ui/v1/admin/users`;
  - `POST /api/ui/v1/admin/users/{id}/role`, `/toggle`, `/scope`, `/sessions/revoke`, `/2fa/reset`.

## 0.49.122 — 2026-10-14

New interface (React) — reports, phase 6b
- **Reports in `/app/reports`**, also reachable from the sidebar of the new interface.
- **Generate:** a PDF or CSV report for all customers or one, over a period of up to 366 days. The checks are the same as on the classic page: the period cannot end in the future and the end cannot precede the start. After generation the page shows the SHA-256 and a download link.
- **Schedules:**
  - list with frequency, format, scope, external recipients and the outcome of the last run;
  - create, suspend or resume, change recipients, delete (with confirmation);
  - archived reports stay after a schedule is deleted.
- **Archive:** newest first, with author (or *NSM schedule*), period, format, size, SHA-256 and summary. Downloads use the classic route, which re-verifies the SHA-256.
- Report generation and schedule actions are now shared functions (`reports.generate_checked`, `report_schedules.create_schedule`/`toggle_schedule`/`set_recipients`/`delete_schedule`), used by both interfaces; the classic forms behave as before.
- **API.** All `POST` routes need `reports.generate` and the CSRF header:
  - `GET /api/ui/v1/reports` (`reports.read`);
  - `POST /api/ui/v1/reports/generate`;
  - `POST /api/ui/v1/reports/schedules`;
  - `POST /api/ui/v1/reports/schedules/{id}/toggle`, `/recipients`, `/delete`.

## 0.49.121 — 2026-10-14

New interface (React) — activity log, phase 6a
- **Activity log in `/app/events`**, also reachable from the sidebar of the new interface. Audit events are listed newest first.
- **Filters**, the same as the classic page, all kept in the address:
  - text search, which also looks inside the details;
  - device: name, serial, MAC or IP;
  - event type, severity, outcome, customer, from/to date.
- **Rows:** each row shows the readable event name and its code, severity and outcome, the device with its manufacturer icon (or the customer), the user who acted, the source and the details.
- *Export CSV* downloads the classic export with the same filters.
- **API.** `GET /api/ui/v1/audit/events` (`audit.read`), built on the classic query (`activity_worklists._audit_query`). Customer scope applies as on the classic page.

## 0.49.120 — 2026-10-14

New interface (React) — incident detail, phase 5c
- **Incident detail in `/app/incidents/<id>`**, opened from the new incident list.
- **Header and summary:** severity, status, duration, customer, site, start, resolution, author.
- **Status:** writers can change it from the page; the resolution time is required to be after the start and not in the future, as on the classic page.
- **Devices involved:** shown with their manufacturer icon; writers can choose them among the customer's devices.
- **Root cause:**
  - the confirmed cause is highlighted, and every hypothesis shows its status, origin, evidence and rationale;
  - suggested correlations from the timeline can be recorded as hypotheses;
  - confirm and reject require a rationale, as on the classic page; reopen is also available;
  - confirming a new cause sends the previous one back to *to verify*.
- **Timeline:**
  - events recorded around the incident, filtered by everything, facts or operator notes;
  - every device is shown with its manufacturer icon;
  - the period before the start is marked;
  - writers can add a note or an action taken, now or at a past time.
- The evidence export stays on the classic page (*Export evidence* opens it).
- Status, notes, devices, hypotheses and decisions are now shared functions in `incidents`, used by both interfaces; the classic forms behave as before. Rejected input carries a code, so the new interface shows the message in the user's language.
- **API.** All actions need `incidents.write` and the CSRF header:
  - `GET /api/ui/v1/incidents/{id}` (`incidents.read`);
  - `POST /api/ui/v1/incidents/{id}/status`, `/notes`, `/devices`, `/hypotheses`, `/hypotheses/{hid}/decision`.
- **Phase 5 complete.** Action Center and incidents are now in the new interface.

## 0.49.119 — 2026-10-14

New interface (React) — incident list, phase 5b
- **Incidents in `/app/incidents`**, also reachable from the sidebar of the new interface. Incidents are listed newest first.
- **Filters:**
  - status chips with counts: active (the default), open, investigating, monitoring, resolved, all;
  - text search on title and description;
  - customer filter.
  - Every filter stays in the address.
- **Rows:** each row shows the severity, the customer, how long the incident lasted (or has lasted so far) and the confirmed root cause. The devices involved appear with their manufacturer icon; the first three are listed and the rest are counted.
- The incident detail and the *New incident* form still open on the classic pages.
- The list filters and counts are now shared functions in `incidents` (`list_scope`, `status_counts`, `status_filter`), used by both interfaces; the classic page behaves as before.
- **API.** `GET /api/ui/v1/incidents` (`incidents.read`). Customer scope applies as on the classic page.

## 0.49.118 — 2026-10-14

New interface (React) — Action Center, phase 5a
- **Action Center in `/app/action-center`**, also reachable from the sidebar of the new interface. Active issues (open and in charge) are listed most severe first, then newest.
- **Filters:**
  - severity chips with counts;
  - category chips, counted within the chosen severity;
  - status (active, open, in charge, resolved) and customer filters.
  - Every filter stays in the address.
- **Rows:** each row shows the device with its manufacturer icon, the customer, the category and when the issue was opened.
- **Actions:**
  - *Take in charge* (for users with `issues.ack`) works as the classic button and is audited the same way (`ISSUE_ACKNOWLEDGED`);
  - *Open incident* leads to the classic incident form for that device.
- Taking an issue in charge is now a shared function (`acknowledge_issue_record`), used by both interfaces; the classic page behaves as before.
- **API.** `GET /api/ui/v1/action-center` (`issues.read`), `POST /api/ui/v1/action-center/{id}/ack` (`issues.ack`, CSRF header). Customer scope applies as on the classic page.

## 0.49.117 — 2026-10-14

New interface (React) — vendor icon on every device
- **Every device shows its manufacturer icon again**, as on the classic pages: same symbols from `/static/brand-icons.svg`, same brand colours, the generic icon when the manufacturer is not known. For manually added devices, the declared manufacturer is used.
- **Where it appears:** device list and device header; Monitoring, MikroTik agents, backup overview, firmware, exposed services, vulnerabilities (affected and unassessed devices), access, lifecycle, compliance; customer graphs (devices with most traffic); diagnostics (neighbors and DHCP leases linked to NSM).
- Every device returned by the new-interface API now carries `brand` (`symbol`, `color`, `label`, `known`), computed by `vendor_cpe.icon` like the classic pages.

## 0.49.116 — 2026-10-14

New interface (React) — compliance, phase 4d
- **Compliance in `/app/compliance`**, also reachable from the sidebar of the new interface. It shows the result of each control (firmware without security updates, no unhandled severe CVE, recent backup, restore test, vendor support, NSM agent active, approved configuration, cleartext services disabled) per device, worst first.
- **Matrix:**
  - one cell per control: compliant, non-compliant, no evidence, exception, not applicable;
  - the cell shows the evidence on hover and opens the classic result page, where exceptions are handled.
- **Outcomes with counts:** non-compliant (the default, without exceptions), in exception, no evidence, compliant, all. Customer and control filters stay in the address.
- *Evaluate now* (for users who manage compliance) recalculates every device with the same rules as the classic button and reports the outcome. Baselines are still managed on the classic page.
- The compliance matrix and the evaluation are now shared functions in `compliance`, used by both interfaces; the classic page behaves as before.
- **API.** `GET /api/ui/v1/compliance` (`compliance.read`), `POST /api/ui/v1/compliance/evaluate` (`compliance.manage`, CSRF header).
- **Phase 4 complete.** Vulnerabilities, exposed services, access, lifecycle and compliance are now in the new interface.

## 0.49.115 — 2026-10-14

New interface (React) — access and lifecycle, phase 4c
- **Access in `/app/access`**, also reachable from the sidebar of the new interface. It shows failed logins found in the device syslog over 1 hour, 24 hours, 7 or 30 days:
  - the devices with most failures (opening their Syslog tab);
  - the source addresses, marking public ones and those that tried several devices;
  - the latest brute-force alerts.
- **Lifecycle in `/app/lifecycle`**, also in the sidebar:
  - devices out of vendor support (end of life, end of support), those ending within a year, and those without catalog data;
  - the last group is shown per vendor/model with *Add to catalog*;
  - search, customer and vendor filters in the address, EOL/EOS dates, replacement state (to handle, planned, exception…), linked to the device remediation and to the EOL/EOS catalog.
- The lifecycle filters and counters are now shared functions in `lifecycle_drilldown`, used by both interfaces; the classic page behaves as before.
- **API.** `GET /api/ui/v1/access`, `GET /api/ui/v1/lifecycle` (`security.read`).

## 0.49.114 — 2026-10-14

New interface (React) — exposed services, phase 4b
- **Exposed services in `/app/exposure`**, also reachable from the sidebar of the new interface. It holds the same fleet view as the classic page:
  - every device with what it exposes to the Internet, worst first;
  - the check comes from the configuration read by the MikroTik agent (raw and filter rules, port forwards) or from outside on the public IP.
- **Views with counts:** with exposed services (the default), to set up (with the reason: missing IP, agent to update…), no exposure, check in progress, all; customer filter. Views stay in the address.
- **Per device:**
  - exposed services with protocol/port and severity (sensitive port forwards included);
  - worst severity, check mode, target IP, shared or private/CGNAT address, last check.
  - The name opens the device Exposure tab.
- A banner warns when devices expose critical services.
- **API.** `GET /api/ui/v1/exposure` (`security.read`), built on `device_exposure.fleet`.

## 0.49.113 — 2026-10-14

New interface (React) — vulnerabilities, phase 4a
- **Vulnerabilities in `/app/vulnerabilities`**, also reachable from the sidebar of the new interface. It holds the same list as the classic page:
  - CVEs matching the devices, highest CVSS first;
  - severity chips with counts (severe, critical, high, medium, low, unclassified);
  - open / resolved / all, search on CVE, vendor, product or description, customer and vendor filters, and a device filter (`?device=`);
  - everything stays in the address, 100 CVEs at a time.
- **CVE page in `/app/vulnerabilities/<id>`:**
  - severity and CVSS, description, source, dates, affected versions, links to NVD, the vendor advisory and CISA;
  - the devices involved with installed and fixed version, open or resolved (with why it was closed), filtered by customer;
  - the devices of the product that cannot be assessed, with the reason.
  - The state of each finding opens the classic page, where the fix or the risk acceptance is handled.
- The list filters and severity counters are now shared functions in `security_drilldown`, used by both interfaces; the classic page behaves as before.
- The new-interface tests accept a missing frontend build for the `/app` pages, as in the CI integration job.
- **API.** `GET /api/ui/v1/vulnerabilities`, `GET /api/ui/v1/vulnerabilities/{advisory_id}` (`security.read`).

## 0.49.112 — 2026-10-14

New interface (React) — translations loaded per language, phase 3e
- **Faster first load.** The four translations no longer travel in the initial bundle. The interface downloads only the language in use (about 10 KB compressed) before showing the first page, and fetches another language only when the user switches to it. The initial JavaScript drops from about 153 KB to 122 KB compressed.
- No untranslated keys appear while switching: the language changes only once its file has arrived. The tests still check that the four languages have exactly the same keys.
- **Phase 3 complete.** Monitoring, MikroTik agents, backup overview and firmware are now in the new interface.

## 0.49.111 — 2026-10-14

New interface (React) — firmware worklist, phase 3d
- **Firmware in `/app/firmware`**, also reachable from the sidebar of the new interface. It holds the same worklist as the classic Firmware page.
- **States with counts:** to update (the default), security update, critical, unknown state, up to date, all. Search on device, vendor, model, serial or version, plus customer and vendor filters; everything stays in the address. Devices load 100 at a time (*Load more*).
- **Per device:**
  - customer and site, vendor, model and channel;
  - installed and recommended version (highlighted when newer), with the readiness note;
  - firmware state in the user's language, last check.
- **Actions, with the same rules as the classic page:**
  - *Check now* queues the read-only RouterOS check (MikroTik online). The button reports whether the check was queued, was already queued or needs the agent.
  - *Prepare upgrade* (MikroTik online with the modern agent, a newer recommended version and permission to execute firmware) and *Plan* open the classic upgrade plan.
  - Links to *Suggestions* and *Vendor catalogs*.
- The list filters and state counters are now shared functions in `firmware_worklist`, used by both interfaces; the classic page behaves as before.
- **API.** `GET /api/ui/v1/firmware` (`firmware.read`; `q`, `customer`, `vendor`, `state`, `offset`), `POST /api/ui/v1/firmware/{device_id}/check` (CSRF header).

## 0.49.110 — 2026-10-14

New interface (React) — backup overview, phase 3c
- **Backup in `/app/backups`**, also reachable from the sidebar of the new interface. It holds the same worklist as the classic Backup page.
- **Cards:** protected devices, devices blocked (policy present but method not executable), devices without a policy, open backup alerts, active policies.
- **Views with counts:** needs attention (last run failed, blocked, never run), last failed, blocked, never run, without policy, protected, all. Search on device, serial, IP or customer and a customer filter; everything stays in the address.
- **Per device:**
  - protection state in the user's language with the backup method;
  - effective policy;
  - last backup with outcome, time and error.
  - The name opens the device Backup tab.
- **Coverage per customer** (protected, blocked, without policy, alerts) linked to the customer page, and the **policies** with their scope and schedule in words. Users allowed to configure backups can edit a policy or create one (*+ Policy*) on the classic forms.
- The protection state of the fleet is now built by shared functions in `backup_workspace_capabilities`, used by both interfaces; the classic page behaves as before.
- **API.** `GET /api/ui/v1/backups` (`backup.read`).

## 0.49.109 — 2026-10-14

New interface (React) — MikroTik Agent fleet, phase 3b
- **Agents in `/app/agents`**, also reachable from the sidebar of the new interface. It is the same worklist as the classic Agent fleet:
  - agents needing attention by default, with suspect installations first;
  - chips with counts: healthy, offline, old heartbeat, no credential, pending, suspect installation, expired token, transport not detected, all;
  - search (name, IP, MAC, serial, model), customer, transport and installation filters, all kept in the address.
- **Per agent:** health with its first reason (the others in the tooltip), installation check, pending tokens, transport and version, next version available and the state of a running update, last heartbeat and source address. The name opens the device Agent tab.
- **Summary:** total agents, healthy, modern and legacy, and how many installed agents can be updated (devices still waiting for their first pairing are not counted).
- The fleet rows are now built by shared functions in `agent_fleet`, so the agent-update reasons added at start-up apply to both interfaces; the classic page behaves as before.
- **API.** `GET /api/ui/v1/agents` (`devices.read`).

## 0.49.108 — 2026-10-14

New interface (React) — fleet Monitoring, phase 3a
- **Monitoring in `/app/monitoring`**, also reachable from the sidebar of the new interface. It is the same worklist as the classic page:
  - devices needing attention by default, the worst first;
  - chips with counts: offline, agent without heartbeat, CPU ≥ 80%, free memory < 10%, no reply to the NSM ping, packet loss ≥ 20%, all;
  - filters by customer and vendor, all kept in the address.
- **Per device:** state and attention badges, agent heartbeat age, CPU and free memory (in red beyond the thresholds), uptime, last NSM ping (RTT and loss, or why it is off). The name opens the device Monitor tab.
- **NSM ping for the fleet or one customer:** how many devices are monitored and how many could be. Users who may change devices can enable or disable it after a confirmation; devices without a management IP are counted and skipped. The change is audited like the classic button.
- The worklist rows and the bulk switch are now shared functions in `fleet_monitoring`, used by both interfaces; the classic page behaves as before.
- **Lighter first load.** The new sections (Monitoring from now on) load only when opened; the initial bundle is about 149 KB compressed.
- **API.** `GET /api/ui/v1/monitoring`, `POST /api/ui/v1/monitoring/icmp` (CSRF header).

## 0.49.107 — 2026-10-14

New interface (React) — customer graphs and availability, phase 2c
- **Graphs tab in `/app/customers/<id>/graphs`** for the whole customer or one site (selector, or *Graphs* from the Sites tab). Range 24 hours, 7 days, 30 days or 90 days; site and range stay in the address.
- **Six uPlot charts**, each with gaps where data is missing, values under the cursor and a current/min/avg/max legend:
  - aggregated WAN traffic of the monitored interfaces;
  - devices sending telemetry (whole numbers on the axis);
  - average and maximum CPU;
  - radio signal (UISP, cnMaestro, MikroTik);
  - latency and packet loss from the NSM ping.
- **Sites compared** over the last 24 hours (devices online, average in/out, peak; devices without a site on their own row). Clicking a site narrows every chart to it.
- **Devices with most traffic** (24 hours) linked to their Monitor tab, and **availability over 30 days**: average, devices below the target, the 15 least available with downtime, outages and RTT.
- Counter charts (devices, errors) now use whole-number steps on the axis, in the device Monitor tab too.
- **API.** `GET /api/ui/v1/customers/{id}/graphs/summary` (`monitoring.read`). The charts reuse `/api/v1/customers/{id}/graphs`.
- **Phase 2 complete.** Customer list, customer page with overview, sites and graphs in the new interface; Backup, Security and Activity of the customer still use the classic pages.

## 0.49.106 — 2026-10-14

New interface (React) — customer page, phase 2b
- **Customer page in `/app/customers/<id>`**, opened from the customer list and from the device breadcrumb. It has the header (code, sites, devices, notes), buttons shown only when allowed (Incidents, Manage customer, + Device) and the same tabs as the classic page.
- **Overview:**
  - six cards: devices online, devices with severe CVEs, firmware to update, open issues, devices protected by backup (with blocked and without-policy counts), devices out of support;
  - quick links to the device list filtered on the customer (offline, severe CVEs, to update, EOL);
  - the latest open issues (linked to their device) and recent activity.
- **Sites:** each site with address and notes, devices with online count and offline devices (both open the filtered device list), and links to graphs and edit. A search box appears when there are more than five sites; devices without a site are counted.
- The *Devices* tab opens the device list filtered on the customer. Graphs, Backup, Security and Activity still use the classic pages.
- **API.** `GET /api/ui/v1/customers/{id}` (`customers.read`; customers outside the user's scope are not found).

## 0.49.105 — 2026-10-14

New interface (React) — customer list, phase 2a
- **Customers in `/app/customers`**, also reachable from the sidebar of the new interface.
- **Per-customer health on one row:**
  - devices with the online count, offline devices, firmware to update, devices with severe CVEs;
  - open Action Center alerts, with backup alerts noted apart.
  - Each number opens the device list already filtered (customer + status, firmware or severe CVEs); alerts open the Action Center of the customer.
- **Chips** for all, with alerts, with offline devices, with severe CVEs, with firmware to update, each with its count. Text search on name and code.
- **Sorting** on every column: counters start from the largest. Filters and sorting stay in the address, so links and the back button restore the same view.
- Users limited to some customers see only theirs; *+ Add customer* appears only with permission.
- **API.** `GET /api/ui/v1/customers` (`customers.read`; `q`, `filter`, `sort`, `dir`).

## 0.49.104 — 2026-10-14

New interface (React) — Diagnostics tab, phase 1d
- **Diagnostics tab in `/app/devices/<id>/diagnostics`** for MikroTik: ping, traceroute, neighbors, DHCP lookup, device log and support snapshot.
- **Launching a diagnostic** uses the same checks as the classic page:
  - allow-listed operations only; target, source and query are validated;
  - the device must be online and the user needs permission to change devices;
  - traceroute is hidden on RouterOS 6; the support snapshot needs a modern agent or legacy 0.49.3+.
- **Recent results** (latest 25), refreshed every 3 seconds while something runs. Each has an outcome in the user's language (loss and average latency, hops reached, neighbors, leases, serious log lines) and expands to its details:
  - ping replies with min/avg/max and jitter;
  - traceroute hops with loss and times;
  - neighbors and DHCP leases linked to the NSM device when the MAC is known;
  - device log by severity.
- JSON export for every completed diagnostic; the support snapshot opens its classic result page.
- **API.** `GET /api/ui/v1/devices/{id}/diagnostics`, `POST …/diagnostics/{diagnostic}` (CSRF header, JSON body `target`/`source`/`query`).
- **Phase 1 complete.** Overview, Monitor, Syslog, Backup, Agent and Diagnostics now open in the new interface. Exposure, Configuration, Firmware, Compliance and Activity still use the classic pages.

## 0.49.103 — 2026-10-14

New interface (React) — Agent tab, phase 1d
- **Agent tab in `/app/devices/<id>/agent`** for MikroTik.
- **Cards:** installation state, transport, agent version with credential state, last heartbeat (marked when stale). Installation problems are shown with their explanation; a critical state also names the RouterOS log command to check.
- **Agent update:** installed and latest version, outdated or current, automatic or one-time reinstall, last update state, RouterOS permissions, script drift. *Update to x.y.z* queues the self-update (`devices.enroll`); while an update is running the page refreshes every 15 seconds.
- **Installation check:** token → one-shot pairing → per-device credential → heartbeat after installation, each with its time.
- **Transport and pairing details, effective functions** (heartbeat, diagnostics, snapshots, backup, telemetry, reboot, RouterOS upgrade, syslog, automatic update), credential metadata and recent enrollments.
- *Regenerate / reinstall agent* posts the classic form. The one-shot install command is still shown only on the classic page and never goes through the new API.
- **API.** `GET /api/ui/v1/devices/{id}/agent`, `POST …/agent/update` (CSRF header). The credential exposes only its dates and state, never the key or its hash.

## 0.49.102 — 2026-10-14

New interface (React) — Backup tab, phase 1d
- **Backup tab in `/app/devices/<id>/backups`**, for every vendor.
- **Run bar:** backup method of the device and *Back up now*, with the same rules as the classic page (MikroTik, online, agent enrolled, effective policy, nothing already queued). When the button is unavailable, the reason is shown in the user's language. While a backup is queued the page refreshes every 15 seconds.
- **Summary:** last successful backup, effective policy with its schedule in words (e.g. "every Monday at 02:30"), archive size and readable exports, last restore test.
- **Archive:** the last 100 runs with their files, filterable by file type and outcome. For each file:
  - size, SHA-256, restore-test result;
  - *Open / Diff* for `.rsc` exports, *Download*, *Restore test* (classic pages);
  - *Delete* with confirmation, for users allowed to configure backups; audited like the classic page.
- **API.** `GET /api/ui/v1/devices/{id}/backups`, `POST …/backups/run`, `DELETE …/backups/artifacts/{artifact_id}` (CSRF header).
- The immediate MikroTik backup is now queued by one shared function used by both the classic form and the new interface; behaviour is unchanged.

## 0.49.101 — 2026-10-14

New interface (React) — Syslog tab, phase 1d
- **Syslog tab in `/app/devices/<id>/syslog`**, for every vendor.
- **Today's counters:** critical/error, warning, other lines since midnight (UTC), and the last line received with its source address.
- **Live log.** The newest lines arrive every 5 seconds. The tail pauses while the browser tab is hidden or when the user presses *Pause*. Filters by severity and text; older lines load in blocks of 200. *Export CSV* keeps the active filters.
- **Device logins** (last 24 hours): failed and successful logins, the addresses with most failures, the latest 15 events.
- **Integrity check** of the warning/error hash chain on demand, showing where the chain breaks.
- **Syslog setup:** receiver state, device key and how the device is recognised, last agent configuration job. *Configure syslog with the agent* queues the job with the same rules as the classic page (`devices.write`, agent version/profile, NSM address). Each refusal reason has its own translated message. Manual setup stays on the classic page.
- **API.** `GET /api/ui/v1/devices/{id}/syslog`, `GET …/syslog/integrity`, `POST …/syslog/configure` (CSRF header). Reading never creates the device syslog key.
- UI errors from the API can now carry a code (`detail: {code, message}`) so messages are translated.

## 0.49.100 — 2026-10-14

New interface (React) — Monitor tab, phase 1c
- **Monitor tab in `/app/devices/<id>/monitor`**, with charts drawn by uPlot (fast on 30 days of samples). A single range selector (1 hour, 24 hours, 7 days, 30 days) drives every chart; data refresh every minute.
- **Resources.** CPU and memory used, 0–100%.
- **Traffic.** In (area) and out (line) for the chosen monitored interface, with the 95th percentile as a dashed line and a table with current, average, maximum, 95th percentile and volume. Errors and drops have their own chart; with agents older than 0.49.20 a note explains why it is missing.
- **Latency and loss** (ping from NSM), when ICMP monitoring is on: average/maximum/minimum RTT and packet loss, with target and range summary.
- **Wireless signal**, when the device has wireless interfaces: signal (average, best and worst client), connected clients and CCQ.
- **Charts.** Gaps where samples are missing (no invented lines), legend with current/min/avg/max, values under the mouse cursor, dates and numbers in the user's language, light and dark palettes.
- **Settings stay classic for now:** the *Interfaces and settings* button opens the classic Monitor page (monitored interfaces, ICMP thresholds).
- **Lighter first load.** The Monitor tab and uPlot download only when the tab is opened (about 25 KB compressed).
- Texts in Italian, English, French and Spanish.

## 0.49.99 — 2026-10-14

New interface (React) — device overview, phase 1b
- **Device page in `/app/devices/<id>`**, opened from the new device list. It has breadcrumbs (devices › customer › site) and the same action buttons as the classic page (reboot, open incident, manage), shown only when the user is allowed.
- **Tabs.** The same tabs as the classic page, according to the vendor (Monitor, Agent, Diagnostics… for MikroTik; UISP for Ubiquiti; cnMaestro; ACS). *Panoramica* is in the new interface; the others still open the classic page.
- **Overview.**
  - Five cards: status and last contact, firmware with the recommended version, open vulnerabilities (critical/high), backup method and last success, ping from NSM.
  - Identity: identity, management/LAN IP, address seen by NSM, MAC, serial, architecture, RouterBOOT, uptime, data source, lifecycle, agent.
  - Open Action Center issues.
  - Refreshed every minute.
- **UI API.** `GET /api/ui/v1/devices/{id}` returns all of the above in one call, with the tabs available for the device and the actions allowed for the user. A device of another customer answers 404 to a scoped user.
- Texts in Italian, English, French and Spanish.

## 0.49.98 — 2026-10-14

New interface (React) — device list, phase 1a
- **Apparati in `/app/devices`.** The first migrated section; the menu entry now opens it in the new interface.
  - Chips with counts: all, online, offline, to be enrolled, firmware to check, severe CVEs, out of support.
  - Text search (name, identity, IP, MAC, serial, model) and customer, site and vendor filters.
  - Sortable columns.
  - The filters live in the address, so a filtered list can be shared or bookmarked.
- **Performance.** Rows are loaded in blocks of 100 while scrolling and only the visible ones are drawn (virtualized), so thousands of devices stay fluid. A device opens its classic page until the device detail is migrated.
- **UI API.**
  - `GET /api/ui/v1/devices`: same filters as the classic inventory, plus `sort`/`dir`, `offset`/`limit` (max 200), counts per status within the customer/site/vendor scope, and severe CVEs per device.
  - `GET /api/ui/v1/devices/facets`: customers, sites and vendors for the filters.
  - Both respect the user's customer scope.
- Texts in Italian, English, French and Spanish; last contact shown as relative time in the user's language.

## 0.49.97 — 2026-10-14

New interface (React) — foundation, phase 0 of the gradual migration (decided with the operator)
- **What it is.** A new React interface served under **`/app`** from the same origin as NSM. It reuses the same session, login, two-factor authentication, idle timeout, permissions and customer scope. Sections arrive one at a time; those not migrated yet open the classic page, and the classic sidebar links to the preview (*Nuova interfaccia (anteprima)*).
- **Languages.** Italian, English, French and Spanish (i18next). The choice is saved per user (migration 0041, `user_preferences.language`); without a choice the browser language is used. Light and dark theme are shared with the classic interface.
- **Stack.** React 19, TypeScript, Vite, TanStack Router and TanStack Query.
  - Plain CSS with the same design tokens as `app.css`; no inline scripts or styles, so the production CSP stays unchanged.
  - Dependencies are pinned with a lockfile. The initial bundle is about 124 KB gzip.
- **UI API `/api/ui/v1`.** `GET /session` returns user, permissions, preferences, branding and CSRF token; `PATCH /session/preferences` saves language and theme.
  - Every change requires the session CSRF token in `X-CSRF-Token`.
  - Permission checks stay on the server.
- **Build and CI.** The Docker image builds the interface in a Node stage and copies it to `app/static/ui`. `/app/*` returns the shell (`no-cache`), and hashed assets are served from `/static/ui`. A new CI job `frontend` runs typecheck, unit tests, production build and an inline-script/style check.

## 0.49.96 — 2026-10-09

Customer graphs — availability over 30 days
- The *Grafici* tab of a customer, or of one site, shows **Disponibilità (ultimi 30 giorni)** from the ping of NSM:
  - weighted average and devices below 99%;
  - the 15 least available devices, with availability, downtime, outages and average RTT.
- Same calculation as the report section. Without the ICMP monitor the panel explains how to enable it.

## 0.49.95 — 2026-10-09

Reports — availability measured by the ping from NSM
- **New report section.** Periodic and on-demand reports have **10. Disponibilità (ping da NSM)** (*Apparati* becomes section 11). For each device with the ICMP monitor in the period:
  - availability %, downtime, number of outages and the longest one;
  - packet loss and average RTT;
  - worst first, with the weighted average and the devices below 99%.
- **How it is counted.** A round (3 echoes every 2 minutes) is up when at least one reply arrives. The 10-minute consolidated points count for the rounds they represent.
  - Devices without the monitor are not listed: NSM never invents an availability figure.
- **CSV.** The export has a new last column, `availability_pct`.

## 0.49.94 — 2026-10-09

Monitoring — latency and loss thresholds per device
- **Thresholds.** The *Latenza e perdita* panel accepts two optional thresholds: average RTT in ms and loss in %. They are evaluated over the last 30 minutes, with at least 5 rounds.
- **Alert.** When exceeded, *Latenza o perdita oltre soglia* opens in the Action Center with a notification and the measured values. It is updated while the problem lasts, and closes when the last 30 minutes are back within the thresholds or the thresholds are removed.
- The new *Latenza* category has its label in the Action Center and is a connectivity candidate for incidents.

## 0.49.93 — 2026-10-09

Vulnerabilities — Huawei and Cisco version schemes (SEC-05)
- **Huawei VRP** versions such as `V200R019C10SPC800` (with optional `SPH` hot patch) are read and compared by version, release, customization, service pack and patch. Before, they were not interpretable, so these devices could not be evaluated. Dotted versions of other Huawei products still work.
- **Cisco versions.**
  - IOS `15.2(4)M3` / `12.2(55)SE12`, ASA `9.12(4)18`, NX-OS `9.3(8)` and IOS-XE `17.3.4a` are parsed with their rebuild numbers and letters.
  - Versions of different release trains (e.g. `M` and `E`) are never ordered against each other, so they never produce a false match.
- **Cisco OS family** (`ios`, `ios_xe`, `ios_xr`, `nx-os`, ASA) comes from the firmware text, else from the model (ISR4/Cat9 → IOS-XE, Nexus → NX-OS, ASA/Firepower → ASA, Catalyst/ISR classic → IOS). Nothing is inferred from the brand alone.

## 0.49.92 — 2026-10-09

Incidents — ping evidence from NSM
- **Timeline.** A new *Misura (ping da NSM)* source lists, for each involved device with the ICMP monitor, every period without replies: start, approximate duration, target, failed rounds, and when it answered again. These entries are observed facts.
- **Correlation candidates.** A ping outage between one hour before and 15 minutes after the start is a *connectivity* candidate with medium confidence. As always, it becomes a root cause only after the operator confirms it.
- Issues of the categories *Raggiungibilità* and *Errori interfacce* are connectivity candidates; *Esposizione* issues are security candidates.

## 0.49.91 — 2026-10-09

Monitoring — alert on interface errors (MON-01)
- **What it checks.** Every 10 minutes the worker sums the receive and transmit errors of each monitored interface over the last hour (Agent 0.49.20+).
- **Opening.** At 50 errors or more (`INTERFACE_ERROR_ALERT_PER_HOUR`, `0` disables) the device gets *Errori sull'interfaccia* in the Action Center, with a notification. It lists the interfaces and points to the Monitor page: check cable, port, duplex or radio signal.
- **Closing.** One issue per device; it closes by itself when every interface is below the threshold.
- **Drops never alert.** Queues and shaping drop packets on healthy links; drops stay on the graph.
- The Action Center shows Italian labels for the *Esposizione*, *Raggiungibilità* and *Errori interfacce* categories.

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

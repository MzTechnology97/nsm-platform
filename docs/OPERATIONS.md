# Operations runbook — backup, restore, health

This runbook covers the platform's own data. Device configuration backups are
managed in the portal (*Backup*) and live under `data/backups/device-files/`.

All commands run from the runtime directory (default `/srv/network-platform`)
with `sudo` (the backup folders are owned by `root`).

## What must be protected

| Data | Where | How it is protected |
|---|---|---|
| PostgreSQL (customers, devices, history, settings, encrypted secrets) | `data/postgres/` | `./manage.sh backup-db`, plus an automatic dump before every `update.sh` |
| Device backup files | `data/backups/device-files/` | copy off-host (rsync/restic/snapshot of the volume) |
| Secrets (`secrets/bootstrap.env`, `.env`) | runtime directory | copy off-host **once**, separately from the dumps: without `ENCRYPTION_MASTER_KEY` the encrypted fields in a dump (MikroTik binary backup passwords, connector credentials) cannot be decrypted, so `.backup` files cannot be opened |

Dumps are plain `pg_dump` SQL, gzip-compressed, in `data/backups/platform-db/`.
Copy them off the host: a dump on the same disk does not survive a disk loss.

## Routine

1. **Daily** (cron or systemd timer): `sudo ./manage.sh backup-db` and copy the
   new file off-host.
2. **At least every 90 days**: `sudo ./manage.sh restore-drill` (see below).
3. **Check** *Amministrazione → Sistema*: worker *Attivo*, no failing task,
   database dump *Recente* (younger than 7 days), restore drill *recente*.

## Restore drill (non-destructive)

```bash
sudo ./manage.sh restore-drill                     # latest dump
sudo ./manage.sh restore-drill data/backups/platform-db/network_platform_<stamp>.sql.gz
```

The drill restores the dump into a temporary database `nsm_restore_drill`,
checks that tables, the Alembic revision and the devices table are present,
then drops the temporary database. The live database is never touched. Every
drill appends one line to `data/backups/platform-db/restore-drills.jsonl`
(time, dump, result, duration, tables, revision, devices), shown on
*Amministrazione → Sistema* as evidence.

## Full restore (destructive)

Use when the database is lost or corrupted, or to roll back a failed upgrade.

```bash
sudo ./manage.sh restore-db data/backups/platform-db/network_platform_<stamp>.sql.gz
```

The command asks to type `RIPRISTINA`, then:

1. takes a safety dump of the current database (`backup-db`);
2. stops `api` and `worker`;
3. drops and recreates `network_platform`, loads the dump;
4. runs the migrations (`migrate`), so an older dump is brought up to the
   installed code version;
5. starts `api` and `worker`.

Afterwards check `./manage.sh health` and *Amministrazione → Sistema*.
Agents keep working: their credentials are in the restored database. Devices
enrolled after the dump was taken must be enrolled again.

### On a new host

1. Install the same release (`install_core.sh`), stop the stack.
2. Restore `secrets/bootstrap.env` and `.env` from the off-host copy **before**
   restoring the database (the encryption key must match).
3. Copy the device backup files into `data/backups/device-files/` (owner
   `10001:10001`, mode `0750`).
4. `sudo ./manage.sh up`, then `sudo ./manage.sh restore-db <dump>`.

## Failed upgrade

`update.sh` writes a dump before changing anything and prints its path when the
health check fails (`Backup DB disponibile per recovery: …`).

1. Read the cause: `./manage.sh logs api` / `./manage.sh logs worker`.
2. Deploy the previous release from the Git clone (`git checkout <previous tag
   or commit>`, then `./update.sh`).
3. If the failed release had already migrated the database, restore the
   pre-upgrade dump with `restore-db`.

## Integrated GenieACS (TR-069)

The Docker stack can run GenieACS next to NSM (optional compose profile `acs`), built from the official `genieacs` npm package and run as a non-root user:

| Service | Port | Exposure |
|---|---|---|
| `genieacs-cwmp` | 7547 | published: ACS URL for the CPE, `http://<server>:7547` |
| `genieacs-fs` | 7567 | published: firmware/config files downloaded by the CPE |
| `genieacs-nbi` | 7557 | internal only, read by NSM at `http://genieacs-nbi:7557` |
| `genieacs-ui` | 3000 | host loopback only (`ssh -L 3000:127.0.0.1:3000 <server>`) |
| `genieacs-mongo` | — | internal; data in `data/genieacs-mongo` |

1. `sudo ./manage.sh acs-enable`: generates `GENIEACS_UI_JWT_SECRET` in `secrets/bootstrap.env`, sets `COMPOSE_PROFILES=acs` and `GENIEACS_INTERNAL_NBI_URL` in `.env`, builds and starts the services. Later `update.sh` runs keep them running.
2. *Amministrazione → Integrazioni → GenieACS / TR-069 → Usa GenieACS integrato*, then *Test connessione*.
3. Open the GenieACS UI through the SSH tunnel and create its administrator **at the first access** (the first visitor sets it, which is why the UI is not published).
4. Configure the CPE (TR-069 ACS URL `http://<server>:7547`); allow 7547 and 7567 only from the customer networks on the host firewall.
5. Include `data/genieacs-mongo` in the off-host backups.

## Integrated syslog server

The `syslog` service receives device logs on **514 UDP/TCP**: the process listens on 5514 as a non-root user and the host publishes 514 (`SYSLOG_HOST_PORT`).
- **Firewall.** Open 514 only toward the device networks or the public addresses of the customers' routers.
- **Identification (2026-10-08).** MikroTik routers with Agent 0.49.13+ log with a per-device key (prefix `NSM-…`). With strict mode, keyless lines from their address are discarded. Other devices are recognised by address only when certain, using the hostname on shared addresses; anything else is discarded and only counted.
- **Allowed networks.** Configure the customers' and devices' networks (PPPoE pools, management networks) in *Amministrazione → Syslog*; the receiver discards everything else before parsing.
- **Accepted senders.** Lines are stored only when the sender address matches a device: management IP, heartbeat source address, RouterOS addresses, or an address assigned in *Amministrazione → Syslog*. Lines that cannot be attributed with certainty are discarded; only counters and the reason are kept.
- **Dedicated account.** `update.sh` adds `SYSLOG_DB_PASSWORD` to `secrets/bootstrap.env`. The `migrate` service then creates the role `nsm_syslog`, limited to writing logs and reading devices, and the receiver uses it. *Amministrazione → Syslog* shows whether it is active.
- **Host firewall.** Docker-published ports are not filtered by ufw. After configuring the allowed networks, run `./manage.sh syslog-firewall` to review the `DOCKER-USER` rules, then `./manage.sh syslog-firewall --apply`. Re-run it after every change of the networks.
- **TLS (optional).** For devices that support syslog over TLS:
  1. Mount a certificate and key into the `syslog` container, e.g. with a `docker-compose.override.yml` adding `volumes: ["./secrets/syslog-tls:/tls:ro"]`, `ports: ["6514:6514/tcp"]` and `environment: {SYSLOG_TLS_CERT: /tls/cert.pem, SYSLOG_TLS_KEY: /tls/key.pem}`.
  2. Point the devices to port 6514.
- **Integrity.** Warning, error and critical lines are hash-chained per device and anchored daily in the audit log; use *Verifica integrità* in the device Syslog tab before quoting logs as evidence.
- **Rate limit.** Each sender is limited to 50 lines per second, with bursts of 200.
- **Retention.** Warning, error and critical lines of a device are kept for its whole life and removed only with the device. Info, notice and debug lines are kept for up to 90 days (configurable, never more). The worker deletes old lines in batches.
- **Status.** The receiver publishes its status every 10 seconds; *Sistema → Connettori* shows it as "Server syslog".

## Capacity and retention

*Amministrazione → Sistema → Capacità e retention* shows the largest tables, the size of the backup archive, its growth over the last 30 days and how long the backup volume lasts at that rate (warning under 90 days).

| Data | Retention |
|---|---|
| MikroTik telemetry | 90 days |
| UISP metrics | 90 days |
| Failed-login records | 1 day (audit events stay) |
| Device backup files | per backup policy (daily / weekly / monthly) |
| Audit events, jobs, snapshots, incidents, generated reports | kept as evidence, no automatic deletion |

Sizing rule of thumb: the backup volume dominates. Estimate *devices × average backup size × retained copies per policy* and keep at least 30% free; the database grows mostly with audit events and job results, typically far less than the backup archive.

## Least-privilege deployment

- **Host**: only administrators in the `docker` group; `secrets/bootstrap.env` and `.env` mode `0640` owned by `root:docker`; backup folders `0750` (the api/worker containers run as uid `10001`).
- **Network**: publish only Caddy (80/443); PostgreSQL, Redis, the api and the worker stay on the internal Docker network. Use HTTPS with a valid certificate before enrolling agents over the internet.
- **Portal users**: give the *Auditor* role to whoever only needs to read; *Operator* for acknowledgements and predefined jobs; *Technician* for device operations; *Administrator* only for platform administration.
- **API keys**: one key per consumer, scoped to the needed read scopes and, when possible, to one Customer; set an expiry and rotate.
- **MikroTik agent**: the agent user gets only the policies of its profile (`ops-v2`: `ftp,reboot,read,write,policy,test,sensitive`; legacy `legacy-ops-v1`: `ftp,reboot,read,write,test`), no `winbox`, `ssh`, `web` or `api`.
- **UISP**: a read-only API token. **GenieACS**: the NBI only on the management network or behind a reverse proxy with authentication; NSM only reads it. **NVD**: an API key used only for reading.

## Deployment hardening review

In place (guarded by `app/tests/deployment_hardening_smoke.py`):

- only Caddy publishes a host port; PostgreSQL (SCRAM authentication), Redis (password, no persistence) and the api/worker stay on the internal network;
- the application image runs as the non-root user `app` (uid `10001`); `migrate`, `api` and `worker` also run with `no-new-privileges` and all Linux capabilities dropped;
- Caddy removes the `Server` header, disables its admin API and sends `Content-Security-Policy` without `unsafe-inline`, `X-Frame-Options: DENY`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy` and `X-Permitted-Cross-Domain-Policies`;
- login throttling, idle timeout and session invalidation in the portal (see `IMPLEMENTED_CAPABILITIES.md`).

Still to do on each installation:

- **HTTPS**: give the platform a DNS name and replace `:80` in `config/Caddyfile` with it (Caddy obtains the certificate automatically); then set `SESSION_COOKIE_SECURE=true`, add `Strict-Transport-Security "max-age=31536000"` to the Caddy headers and reinstall the MikroTik agents so they verify the certificate. Until then agent secrets and portal sessions travel in clear text.
- **Docker network**: the api trusts `X-Forwarded-For` from any container on `network-platform-net` (Caddy overwrites the header for external clients). Do not attach other containers to that network.
- **Host**: firewall allowing only 80/443 (and SSH from management addresses), automatic security updates, off-host copies of dumps and secrets.

## Rotating the encryption master key

`ENCRYPTION_MASTER_KEY` (in `secrets/bootstrap.env`) encrypts connector credentials and the passwords of MikroTik binary backups. To rotate it, for example after a suspected leak:

1. `sudo ./manage.sh backup-db` and keep the dump together with the **current** key.
2. Generate a new key (for example `openssl rand -base64 48`).
3. In `secrets/bootstrap.env` set `ENCRYPTION_MASTER_KEY=<new key>` and `ENCRYPTION_PREVIOUS_KEYS=<old key>` (comma separated if more than one).
4. `sudo ./manage.sh restart`: everything keeps working, new secrets use the new key.
5. `sudo ./manage.sh rotate-secrets`: re-encrypts every stored secret with the new key (audit event `SECRETS_REENCRYPTED`).
6. *Amministrazione → Sistema → Cifratura dei segreti* must show no secret on a previous key and none unreadable; then remove `ENCRYPTION_PREVIOUS_KEYS` and restart.

Dumps taken before the rotation still need the old key: store it with them. If the page reports **unreadable** secrets, the key was changed without declaring the previous one in `ENCRYPTION_PREVIOUS_KEYS`: put it back before doing anything else.

## Health and monitoring

- `GET /health` (also `/api/v1/health`): `200` when PostgreSQL and Redis
  answer, `503` otherwise. The `worker` field reports `alive` (heartbeat
  younger than 180 s), the heartbeat age and the names of failing tasks; it is
  informational and does not change the status code. Point external
  monitoring at it and alert on `503`, `worker.alive == false` and non-empty
  `worker.failing_tasks`.
- *Amministrazione → Sistema*: connector and data-source health (UISP, NVD,
  GenieACS, RouterOS catalog, MikroTik agents), version, schema revision, database size, free
  space on the backup volume (warning under 10%), worker heartbeat and the last
  outcome of every periodic task, platform dumps and restore drills.
- Each periodic worker task is isolated: an error is logged and recorded and the
  other tasks keep running.

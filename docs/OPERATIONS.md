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
| Secrets (`secrets/bootstrap.env`, `.env`) | runtime directory | copy off-host **once**, separately from the dumps: without `ENCRYPTION_MASTER_KEY` the encrypted fields in a dump (agent secrets, backup passwords, connector tokens) cannot be decrypted |

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

## Health and monitoring

- `GET /health` (also `/api/v1/health`): `200` when PostgreSQL and Redis
  answer, `503` otherwise. The `worker` field reports `alive` (heartbeat
  younger than 180 s), the heartbeat age and the names of failing tasks; it is
  informational and does not change the status code. Point external
  monitoring at it and alert on `503`, `worker.alive == false` and non-empty
  `worker.failing_tasks`.
- *Amministrazione → Sistema*: version, schema revision, database size, free
  space on the backup volume (warning under 10%), worker heartbeat and the last
  outcome of every periodic task, platform dumps and restore drills.
- Each periodic worker task is isolated: an error is logged and recorded and the
  other tasks keep running.

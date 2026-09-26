# NSM Platform

Private WISP network operations and security management platform.

## Core 0.2

Core 0.2 introduces the long-term application structure:

- professional sidebar/topbar UI and global search;
- Customer → optional Site → Device inventory;
- operator alias (`display_name`) separated from observed device identity;
- server-side device filters and pagination;
- one-shot MikroTik enrollment token and bootstrap inventory;
- Notification Center with per-user unread state;
- Action Center foundation;
- CVE/security advisory → impacted device data model;
- lifecycle EOL/EOS fields;
- backup policies with global/vendor/customer/device precedence;
- backup run evidence fields and SHA256;
- administration users and initial RBAC roles;
- append-only audit events;
- safe update script with pre-migration PostgreSQL dump.

## Runtime layout

Git repository:

```text
~/nsm-platform
```

Active runtime:

```text
/srv/network-platform
```

Runtime secrets, PostgreSQL data and `.env` are **not** stored in Git.

## Upgrade workflow

After a release is merged into `main`:

```bash
cd ~/nsm-platform
git switch main
git pull --ff-only
./update.sh
```

`update.sh` creates a compressed `pg_dump`, deploys only application/config files,
builds the containers, runs Alembic through Compose and verifies `/health`.

## Automatic validated deployment

The optional unattended flow is:

```text
main -> GitHub Actions CI -> deploy -> NSM-CDA systemd timer -> update.sh
```

Install it once with:

```bash
sudo ./scripts/install-auto-update.sh
```

If a deployment fails, the server publishes a sanitized diagnostic file named
`log` on the `runtime-logs` branch. Runtime secrets are not intentionally
included in that report.

## Security note

The current MikroTik bootstrap is a one-shot enrollment/inventory mechanism. It
does not implement arbitrary remote command execution. Persistent authenticated
agent jobs (backup, heartbeat, firmware workflow) are intentionally implemented
as a separate capability layer.

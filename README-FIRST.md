# NSM Platform

Private WISP network operations and security management platform.

## Core 0.3

Core 0.3 extends the operator experience while preserving the Core 0.2 inventory and security model:

- self-service password change for the signed-in user;
- per-user persistent Light/Dark theme;
- responsive mobile navigation and collapsible desktop sidebar;
- configurable platform name, tagline and GUI colors;
- custom PNG/JPEG/WebP logo stored in PostgreSQL;
- branding applied to login and navigation;
- audited password, theme and branding changes;
- modular UI extension loaded through `app.entrypoint`.

## Core 0.2 foundation

Core 0.2 introduced the long-term application structure:

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

## Automatic validated deployment

The unattended flow is:

```text
main -> GitHub Actions CI -> deploy -> deployment host systemd timer -> update.sh
```

Install it once with:

```bash
sudo ./scripts/install-auto-update.sh
```

The installer derives the deployment Git user/repository from the local installation context and stores host-specific paths in `/etc/default/nsm-auto-update`, rather than hard-coding deployment identifiers in the repository.

`update.sh` creates a compressed `pg_dump`, deploys only application/config files,
builds the containers, runs Alembic through Compose and verifies `/health`.

If a deployment fails, a sanitized diagnostic report is retained locally by default at `/var/lib/nsm-auto-update/last-failure.log`. Remote publication is disabled unless `NSM_PUBLISH_FAILURE_LOG=1` is explicitly configured. Do not enable remote failure-log publication for a public repository.

## Security note

The current MikroTik bootstrap is a one-shot enrollment/inventory mechanism. It
does not implement arbitrary remote command execution. Persistent authenticated
agent jobs (backup, heartbeat, firmware workflow) are intentionally implemented
as a separate capability layer.

# NSM Platform — deployment / operator quick start

This file is the operational entry point for installing and updating NSM. For project status, architecture and development context start with [`README.md`](README.md) and [`docs/README.md`](docs/README.md).

The repository is public. Runtime secrets, database data, backup artifacts and deployment-specific diagnostics must remain outside Git.

## Documentation

- current product status: [`README.md`](README.md)
- implemented capabilities: [`docs/IMPLEMENTED_CAPABILITIES.md`](docs/IMPLEMENTED_CAPABILITIES.md)
- roadmap: [`docs/ROADMAP.md`](docs/ROADMAP.md)
- architecture: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- engineering workflow: [`docs/DEVELOPMENT_WORKFLOW.md`](docs/DEVELOPMENT_WORKFLOW.md)
- public-repository safety: [`docs/PUBLIC_REPOSITORY_DATA_SAFETY.md`](docs/PUBLIC_REPOSITORY_DATA_SAFETY.md)

## Runtime layout

A normal installation keeps the Git source checkout separate from the active runtime tree.

Typical active runtime:

```text
/srv/network-platform
```

Do not run the runtime copy of `update.sh` as if it were the Git source. The update script now validates this condition and fails closed rather than deleting/copying from the same tree.

## Container stack

The default Compose stack contains:

- PostgreSQL;
- Redis;
- one-shot Alembic migration service;
- FastAPI/Uvicorn API;
- worker;
- Caddy.

Backup artifacts are persisted through the configured host-mounted backup storage path.

## Initial installation

Use the repository installation scripts from a validated source checkout. Review local environment/secrets before starting the stack.

The repository intentionally provides `.env.example` without production secrets. Never commit a populated production `.env` or secret bootstrap file.

## Manual update

Run `update.sh` from the Git source checkout (or provide the supported explicit source override), not from `/srv/network-platform`.

The validated update sequence performs:

1. source/runtime safety checks;
2. PostgreSQL logical backup using `pg_dump`;
3. deployment of application/configuration files;
4. container build/restart;
5. Alembic migration;
6. application health verification.

Do not replace the logical PostgreSQL backup with a raw copy of a live database data directory.

## Automatic validated deployment

The intended unattended flow is:

```text
main -> CI -> deployment host systemd timer/service -> update.sh -> health check
```

Install/refresh the auto-update components from the repository with:

```bash
sudo ./scripts/install-auto-update.sh
```

Host-specific Git identity/path values are stored outside the repository in the local system configuration used by the auto-update unit.

## Auto-update diagnostics

If deployment fails, a sanitized report is kept locally by default:

```text
/var/lib/nsm-auto-update/last-failure.log
```

Remote publication of runtime failure diagnostics is disabled by default. Keep it disabled for a public repository unless an intentionally controlled private destination is configured and the resulting data has been reviewed.

## Backup-storage ownership

The application containers run as a non-root runtime user. Installation/update code prepares the backup artifact directory with compatible ownership and permissions.

After deployment, storage writeability should be verified from the application container rather than assuming host ownership is sufficient.

## MikroTik onboarding security

MikroTik enrollment uses a short-lived one-time token and then a unique per-Device persistent credential. RouterOS initiates outbound HTTPS communication with NSM.

The Agent uses explicit allow-listed operations. It does not expose a generic arbitrary command/shell channel.

## Public repository safety

Operational troubleshooting data does not belong in Git. Before creating a test/fixture from a real incident, convert it to synthetic/public-safe values according to [`docs/PUBLIC_REPOSITORY_DATA_SAFETY.md`](docs/PUBLIC_REPOSITORY_DATA_SAFETY.md).

Use the repository documentation and current open PRs to determine which physical RouterOS acceptance tests are still required before treating a capability as fully validated.

# Public repository data-safety rules

NSM source, documentation and automated tests must not contain values copied from a production or customer environment.

## Test and documentation fixtures

Use deterministic synthetic fixtures rather than production-like values:

- IPv4 examples: RFC 5737 TEST-NET ranges `192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`;
- DNS names: `example.test`, `example.invalid` or clearly synthetic host labels;
- MAC addresses: locally administered addresses, preferably with a `02:` first octet;
- serial numbers, software IDs, usernames and customer/site/device labels: `TEST-*`, `CI*` or values generated during the test;
- passwords, tokens and API keys: test-only values or values generated at runtime; never copy a credential from a deployed system.

Random UUID suffixes are appropriate when a test needs uniqueness. Network fixtures used in assertions should normally remain deterministic and come from reserved documentation ranges so failures are reproducible.

## Automated guard

`scripts/check-public-data-safety.py` is executed by the isolated smoke suite. It performs two checks:

1. a repository-wide pass for high-signal credential signatures and deployment-derived identifiers previously found during audit;
2. a stricter fixture/documentation pass that rejects RFC1918 example addresses, globally administered unicast MAC fixtures and deployment-like home-directory paths.

Do not weaken the guard to make a copied production value pass. If a literal is technically required for a regression test or explanatory document, add `public-data-safety: allow` on that line and document nearby why the value is intentionally safe and necessary.

## Real-device diagnostics

A bug reproduced on physical hardware may be documented, but the public write-up must replace deployment-specific data before commit. Preserve only the technical behavior needed to understand the bug. In particular, replace:

- management/server IP addresses;
- hostnames and site/POP labels;
- globally assigned hardware addresses;
- serial/software IDs;
- customer names and account identifiers;
- credentials, enrollment tokens and per-device secrets.

## Deployment configuration

Do not hard-code deployment-specific operating-system usernames, home directories, SSH key paths or repository checkout paths in public source. Discover them during installation or provide them through host-local configuration such as `/etc/default/...` or environment variables.

## Runtime data

Runtime `.env`, secrets, database contents, backups and diagnostic logs are not source artifacts and must not be committed. A public Git repository must not be used as an unsanitized runtime-log destination. Remote publication of sanitized diagnostics must remain explicit opt-in and should stay disabled for public repositories.

## Git history

Replacing a value in a new commit does not remove it from earlier Git commits or old branches. If a genuine credential or token is ever committed, rotate it immediately and then perform a dedicated history-cleanup operation. Low-sensitivity infrastructure identifiers should also be removed from active source and documentation when discovered.

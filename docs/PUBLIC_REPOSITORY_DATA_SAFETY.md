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

## Real-device diagnostics

A bug reproduced on physical hardware may be documented, but the public write-up must replace deployment-specific data before commit. Preserve only the technical behavior needed to understand the bug. In particular, replace:

- management/server IP addresses;
- hostnames and site/POP labels;
- globally assigned hardware addresses;
- serial/software IDs;
- customer names and account identifiers;
- credentials, enrollment tokens and per-device secrets.

## Runtime data

Runtime `.env`, secrets, database contents, backups and diagnostic logs are not source artifacts and must not be committed. A public Git repository must not be used as an unsanitized runtime-log destination.

## Git history

Replacing a value in a new commit does not remove it from earlier Git commits or old branches. If a genuine credential or token is ever committed, rotate it immediately and then perform a dedicated history-cleanup operation. Low-sensitivity infrastructure identifiers should also be removed from active source and documentation when discovered.

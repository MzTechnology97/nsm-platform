# Core 0.30 — Adaptive MikroTik Enrollment & Firmware Readiness

## Scope

Core 0.30 keeps the RouterOS 7.12.1-compatible bootstrap introduced in Core 0.28, but avoids degrading newer routers to the legacy transport.

- RouterOS 7.12.x receives the legacy transport.
- RouterOS 7.13+ receives the modern NSM agent with jobs, snapshots, diagnostics and HTTPS backup support.
- The selected transport is stored in observed inventory and audited.

## Firmware readiness

The new `firmware_readiness` job is read-only. It gathers:

- RouterOS update channel;
- installed RouterOS version;
- latest version reported by RouterOS;
- RouterOS update status;
- free HDD space;
- current RouterBOARD firmware;
- available RouterBOARD upgrade firmware.

It never runs package install/download actions, RouterBOARD upgrade, or reboot.

Both modern and legacy transports can execute the readiness check. Legacy RouterOS uses the Core 0.29 plain-text allow-listed job protocol and does not require `:serialize` / `:deserialize`.

## Future upgrade workflow

Actual firmware execution is intentionally out of scope for 0.30. A future release must require `firmware.execute`, explicit operator confirmation, a safe pre-update backup strategy, and post-reboot health verification before it can be considered complete.

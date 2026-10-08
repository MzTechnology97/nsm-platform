"""Deployment hardening guard: compose and Caddy settings that must not regress."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def service_blocks(text: str) -> dict:
    blocks, current = {}, None
    for line in text.splitlines():
        match = re.match(r"^  ([a-z][a-z-]*):\s*$", line)
        if match:
            current = match.group(1)
            blocks[current] = []
        elif current and (line.startswith("    ") or not line.strip()):
            blocks[current].append(line)
        elif not line.startswith(" "):
            current = None
    return {name: "\n".join(lines) for name, lines in blocks.items()}


def main():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    services = service_blocks(compose.split("\nservices:\n")[1].split("\nnetworks:\n")[0])
    anchor = compose.split("\nservices:\n")[0]
    for name in ("migrate", "api", "worker", "syslog"):
        block = services[name]
        assert "no-new-privileges:true" in block and "cap_drop: [ALL]" in block, f"{name} must drop privileges"
    published = sorted(name for name, block in services.items() if re.search(r"^\s{4}ports:", block, re.M))
    assert published == ["caddy", "genieacs-cwmp", "genieacs-fs", "genieacs-ui", "syslog"], f"unexpected published ports: {published}"
    # The syslog receiver binds an unprivileged port in the container; the host publishes 514.
    assert ":5514/udp" in services["syslog"] and ":5514/tcp" in services["syslog"] and "app.syslog_main" in services["syslog"]
    for setting in ("read_only: true", "mem_limit:", "pids_limit:", "tmpfs:"):
        assert setting in services["syslog"], f"syslog container hardening: {setting}"
    assert "app.db_roles" in services["migrate"], "least-privilege roles refreshed on every deploy"
    assert '"127.0.0.1:3000:3000"' in services["genieacs-ui"], "the GenieACS UI is bound to the host loopback only"
    assert "ports:" not in services["genieacs-nbi"] and "ports:" not in services["genieacs-mongo"], "NBI and MongoDB stay internal"
    for name in ("genieacs-cwmp", "genieacs-nbi", "genieacs-fs", "genieacs-ui"):
        assert "<<: *genieacs" in services[name], f"{name} uses the shared GenieACS settings"
        assert 'profiles: ["acs"]' in anchor or 'profiles: ["acs"]' in services[name]
    assert "no-new-privileges:true" in anchor and "cap_drop: [ALL]" in anchor and 'profiles: ["acs"]' in services["genieacs-mongo"]
    assert "USER node" in (ROOT / "genieacs" / "Dockerfile").read_text(encoding="utf-8"), "GenieACS runs as a non-root user"
    assert "--auth-host=scram-sha-256" in services["postgres"] and "--requirepass" in services["redis"]
    assert "USER app" in (ROOT / "app" / "Dockerfile").read_text(encoding="utf-8"), "the application image runs as a non-root user"

    caddy = (ROOT / "config" / "Caddyfile").read_text(encoding="utf-8")
    for header in ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "Permissions-Policy", "Cross-Origin-Opener-Policy",
                   "X-Permitted-Cross-Domain-Policies", "Content-Security-Policy", "-Server"):
        assert header in caddy, header
    csp = re.search(r'Content-Security-Policy "([^"]+)"', caddy).group(1)
    assert "'unsafe-inline'" not in csp and "frame-ancestors 'none'" in csp and "default-src 'self'" in csp
    assert "admin off" in caddy
    print("Deployment hardening smoke passed")


if __name__ == "__main__":
    main()

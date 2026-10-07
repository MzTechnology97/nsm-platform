"""Deployment hardening guard: compose and Caddy settings that must not regress."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def service_blocks(text: str) -> dict:
    blocks, current = {}, None
    for line in text.splitlines():
        match = re.match(r"^  ([a-z]+):\s*$", line)
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
    services = service_blocks(compose.split("\nnetworks:\n")[0])
    for name in ("migrate", "api", "worker"):
        block = services[name]
        assert "no-new-privileges:true" in block and "cap_drop: [ALL]" in block, f"{name} must drop privileges"
    published = [name for name, block in services.items() if re.search(r"^\s{4}ports:", block, re.M)]
    assert published == ["caddy"], f"only Caddy may publish host ports: {published}"
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

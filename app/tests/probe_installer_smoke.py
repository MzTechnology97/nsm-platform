"""SCAN-02 probe installer (probe/install.sh): files, secret, plain and HTTPS layouts, validation, with a stub docker."""
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "probe" / "install.sh"
BASH = shutil.which("bash")

if not BASH or os.name == "nt":
    print("SKIP probe installer smoke: needs a POSIX bash")
    sys.exit(0)

STUB = """#!/usr/bin/env bash
echo "docker $*" >> "$DOCKER_LOG"
case "$*" in
  "compose version") echo "Docker Compose version v2.test" ;;
  *" ps -q nsm-probe") echo abc123 ;;
  inspect*) echo healthy ;;
esac
exit 0
"""


def run(work: pathlib.Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    # NSM_PROBE_TEST lets the installer run without root; the stub docker records the calls.
    env = {**os.environ, "PATH": f"{work / 'bin'}:{os.environ['PATH']}", "DOCKER_LOG": str(work / "docker.log"), "NSM_PROBE_TEST": "1"}
    result = subprocess.run([BASH, str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)
    if check:
        assert result.returncode == 0, (args, result.stdout, result.stderr)
    return result


def env_of(path: pathlib.Path) -> dict:
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and not line.startswith("#"))


with tempfile.TemporaryDirectory() as tmp:
    work = pathlib.Path(tmp)
    (work / "bin").mkdir()
    (work / "bin" / "docker").write_text(STUB)
    (work / "bin" / "docker").chmod(0o755)

    # Plain HTTP: the probe publishes its port, the allowed sources (plus loopback for the healthcheck) reach it.
    plain = work / "plain"
    out = run(work, "install", "--source", str(ROOT), "--dir", str(plain), "--allow", "203.0.113.10, 198.51.100.0/24", "--name", "probe-ci", "-y").stdout
    values = env_of(plain / ".env")
    assert len(values["PROBE_SECRET"]) == 64 and all(c in "0123456789abcdef" for c in values["PROBE_SECRET"]), values
    assert values["PROBE_SECRET"] in out and "probe-ci" in out and ":8788" in out, out
    assert values["PROBE_ALLOWED_SOURCES"] == "203.0.113.10,198.51.100.0/24,127.0.0.1/32", values
    assert stat.S_IMODE((plain / ".env").stat().st_mode) == 0o600
    assert (plain / "build" / "nsm_probe.py").read_text() == (ROOT / "probe" / "nsm_probe.py").read_text()
    assert (plain / "build" / "port_probes.py").read_text() == (ROOT / "app" / "app" / "port_probes.py").read_text()
    assert "COPY port_probes.py nsm_probe.py ./" in (plain / "build" / "Dockerfile").read_text()
    compose = (plain / "docker-compose.yml").read_text()
    for needle in ('"8788:8788"', "read_only: true", "cap_drop: [ALL]", "no-new-privileges:true", "healthcheck:", "max-size", "/v1/health"):
        assert needle in compose, needle
    assert "caddy" not in compose and not (plain / "Caddyfile").exists()
    assert "up -d --build --remove-orphans" in (work / "docker.log").read_text()

    # Reinstall keeps the secret and the saved choices; only the given options change.
    run(work, "install", "--source", str(ROOT), "--dir", str(plain), "--port", "9788", "-y")
    again = env_of(plain / ".env")
    assert again["PROBE_SECRET"] == values["PROBE_SECRET"] and again["PROBE_NAME"] == "probe-ci", again
    assert '"9788:8788"' in (plain / "docker-compose.yml").read_text()
    assert run(work, "show-secret", "--dir", str(plain)).stdout.strip() == values["PROBE_SECRET"]
    status = run(work, "status", "--dir", str(plain)).stdout
    assert "probe-ci" in status and values["PROBE_SECRET"] not in status, status

    # HTTPS: Caddy in front filters the sources, the probe port is not published and the probe trusts Caddy.
    https = work / "https"
    run(work, "install", "--source", str(ROOT / "probe"), "--dir", str(https), "--allow", "203.0.113.10", "--domain", "probe.example.test",
        "--email", "ops@example.test", "--secret", "s" * 40, "-y")
    caddyfile = (https / "Caddyfile").read_text()
    assert "probe.example.test {" in caddyfile and "@nsm remote_ip 203.0.113.10" in caddyfile and "respond 403" in caddyfile, caddyfile
    assert "email ops@example.test" in caddyfile
    compose = (https / "docker-compose.yml").read_text()
    assert "caddy:2" in compose and '"443:443"' in compose and '"8788:8788"' not in compose and 'expose:' in compose, compose
    assert env_of(https / ".env") == {**env_of(https / ".env"), "PROBE_SECRET": "s" * 40, "PROBE_ALLOWED_SOURCES": ""}

    # Validation: nothing is written for bad input.
    for bad in (["--port", "0"], ["--name", "bad/name"], ["--allow", "nsm.example.test"], ["--secret", "short"],
                ["--domain", "bad_domain"], ["--bogus"]):
        result = run(work, "install", "--source", str(ROOT), "--dir", str(work / "bad"), "-y", *bad, check=False)
        assert result.returncode != 0 and result.stderr.strip(), (bad, result.stdout, result.stderr)
    assert not (work / "bad").exists()

    run(work, "uninstall", "--dir", str(https), "-y")
    assert not https.exists()

print("probe installer smoke OK")

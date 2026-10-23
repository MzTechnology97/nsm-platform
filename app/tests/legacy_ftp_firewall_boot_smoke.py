"""Legacy backup FTP firewall kept by nsm-legacy-ftp-firewall.timer: installed with the automatic update, applied at
boot and when the subnets change, removed when none is left. The script runs against stub iptables/docker commands."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "nsm-legacy-ftp-firewall.sh"

# iptables/ip6tables stub: DOCKER-USER lives in a file, every call is logged.
IPTABLES = """#!/bin/sh
state="$NSM_TEST_DIR/docker-user.$(basename "$0")"
echo "$*" >> "$NSM_TEST_DIR/calls"
case "$1" in
  -S) [ "$2" = DOCKER-USER ] || exit 1; echo "-N DOCKER-USER"; cat "$state" 2>/dev/null ;;
  -C) shift; grep -qxF -- "-A $*" "$state" 2>/dev/null; exit $? ;;
  -I) shift; echo "-A $*" >> "$state" ;;
  -D) shift; grep -vxF -- "-A $*" "$state" > "$state.new"; mv "$state.new" "$state" ;;
esac
exit 0
"""
DOCKER = """#!/bin/sh
cat "$NSM_TEST_DIR/rules"
"""
RULES = """#!/bin/sh
echo applied >> "$NSM_TEST_DIR/applied"
iptables -C DOCKER-USER -p tcp --dport 2121 -j NSM-LEGACY-FTP 2>/dev/null || iptables -I DOCKER-USER -p tcp --dport 2121 -j NSM-LEGACY-FTP
"""


def static_checks():
    installer = (REPO / "scripts" / "install-auto-update.sh").read_text(encoding="utf-8")
    assert '"$REPO_DIR/scripts/nsm-legacy-ftp-firewall.sh" /usr/local/sbin/nsm-legacy-ftp-firewall' in installer
    assert "nsm-legacy-ftp-firewall.timer" in installer and "systemctl enable --now nsm-legacy-ftp-firewall.timer" in installer
    unit = (REPO / "deploy" / "systemd" / "nsm-legacy-ftp-firewall.service").read_text(encoding="utf-8")
    assert "ExecStart=/usr/local/sbin/nsm-legacy-ftp-firewall" in unit and "After=docker.service" in unit
    timer = (REPO / "deploy" / "systemd" / "nsm-legacy-ftp-firewall.timer").read_text(encoding="utf-8")
    assert "OnBootSec=" in timer and "OnUnitActiveSec=2min" in timer and "WantedBy=timers.target" in timer


def run_script(tmp: Path, env: dict) -> str:
    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout


def applied(tmp: Path) -> int:
    path = tmp / "applied"
    return len(path.read_text().splitlines()) if path.exists() else 0


def jumps(tmp: Path) -> list[str]:
    path = tmp / "docker-user.iptables"
    return [line for line in path.read_text().splitlines() if line.endswith("-j NSM-LEGACY-FTP")] if path.exists() else []


def behaviour():
    with tempfile.TemporaryDirectory() as name:
        tmp = Path(name)
        stubs, runtime, state = tmp / "bin", tmp / "runtime", tmp / "state"
        for directory in (stubs, runtime, state):
            directory.mkdir()
        for tool, body in (("iptables", IPTABLES), ("ip6tables", IPTABLES), ("docker", DOCKER)):
            (stubs / tool).write_text(body)
            (stubs / tool).chmod(0o755)
        (runtime / ".env").write_text("LEGACY_FTP_ENABLED=1\n")
        (tmp / "rules").write_text(RULES)
        env = {**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}", "NSM_TEST_DIR": str(tmp),
               "NSM_RUNTIME_DIR": str(runtime), "NSM_STATE_DIR": str(state)}

        assert "applicate" in run_script(tmp, env) and applied(tmp) == 1 and len(jumps(tmp)) == 1
        assert (state / "legacy-ftp-firewall.sh").read_text() == RULES
        run_script(tmp, env)
        assert applied(tmp) == 1, "unchanged and present: not applied again"

        (tmp / "docker-user.iptables").unlink()  # reboot: DOCKER-USER starts empty
        run_script(tmp, env)
        assert applied(tmp) == 2 and len(jumps(tmp)) == 1, "applied again after a reboot"

        (tmp / "rules").write_text(RULES + "# 198.51.100.0/24 added\n")  # subnets changed in NSM
        run_script(tmp, env)
        assert applied(tmp) == 3 and len(jumps(tmp)) == 1

        (tmp / "rules").write_text("# Nessuna subnet consentita configurata\nexit 1\n")
        assert "rimosse" in run_script(tmp, env)
        assert jumps(tmp) == [] and not (state / "legacy-ftp-firewall.sh").exists() and applied(tmp) == 3

        (runtime / ".env").write_text("LEGACY_FTP_ENABLED=0\n")
        (tmp / "rules").write_text(RULES)
        calls = (tmp / "calls").read_text()
        run_script(tmp, env)
        assert applied(tmp) == 3 and (tmp / "calls").read_text() == calls, "receiver off: nothing touched"


def main():
    static_checks()
    if os.name == "posix" and shutil.which("bash") and shutil.which("flock"):
        behaviour()
    else:
        print("behaviour checks skipped: they need bash and flock (Linux CI)")
    print("Legacy FTP firewall boot smoke passed")


if __name__ == "__main__":
    main()

#!/usr/bin/env bash
# Keep the host firewall of the legacy backup FTP receiver in line with the subnets allowed in NSM
# (Amministrazione -> Sistema). Run by nsm-legacy-ftp-firewall.timer at boot and every 2 minutes, as root.
# The rules are applied only when they changed or are missing (after a reboot the DOCKER-USER chain is empty).
set -Eeuo pipefail

RUNTIME_DIR="${NSM_RUNTIME_DIR:-/srv/network-platform}"
STATE_DIR="${NSM_STATE_DIR:-/var/lib/nsm-auto-update}"
APPLIED="$STATE_DIR/legacy-ftp-firewall.sh"
CHAIN=NSM-LEGACY-FTP

exec 8>"$STATE_DIR/legacy-ftp-firewall.lock"
flock -n 8 || exit 0
cd "$RUNTIME_DIR" 2>/dev/null || exit 0
grep -q '^LEGACY_FTP_ENABLED=1' .env 2>/dev/null || exit 0
command -v iptables >/dev/null 2>&1 || exit 0
iptables -S DOCKER-USER >/dev/null 2>&1 || exit 0  # Docker not started yet: next run

script=$(mktemp)
trap 'rm -f "$script"' EXIT
# The API container renders the rules; while it is down (boot, deployment) the next run retries.
docker compose exec -T api python -m app.legacy_ftp_firewall > "$script" 2>/dev/null || [ -s "$script" ] || exit 0

if grep -q '^exit 1' "$script"; then
  # No subnet allowed in NSM: the receiver accepts any origin, so the host rules go too.
  for tool in iptables ip6tables; do
    command -v "$tool" >/dev/null 2>&1 || continue
    while read -r rule; do
      "$tool" -D ${rule#-A } || true
    done < <("$tool" -S DOCKER-USER 2>/dev/null | grep -- "-j $CHAIN\$" || true)
    "$tool" -F "$CHAIN" 2>/dev/null && "$tool" -X "$CHAIN" 2>/dev/null || true
  done
  if [ -f "$APPLIED" ]; then
    rm -f "$APPLIED"
    echo "Nessuna subnet consentita in NSM: regole firewall del ricevitore FTP rimosse."
  fi
  exit 0
fi

if [ -f "$APPLIED" ] && cmp -s "$script" "$APPLIED" && iptables -S DOCKER-USER | grep -q -- "-j $CHAIN\$"; then
  exit 0
fi
sh "$script"
install -m 0600 "$script" "$APPLIED"
echo "Regole firewall del ricevitore FTP applicate dalle subnet configurate in NSM."

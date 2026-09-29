#!/usr/bin/env bash
set -Eeuo pipefail

REFRESH_COMPONENTS_ONLY=0
case "${1:-}" in
  --refresh-components-only)
    REFRESH_COMPONENTS_ONLY=1
    shift
    ;;
  "")
    ;;
  *)
    echo "Uso: $0 [--refresh-components-only]" >&2
    exit 2
    ;;
esac
[[ $# -eq 0 ]] || { echo "Uso: $0 [--refresh-components-only]" >&2; exit 2; }

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="${NSM_REPO_DIR:-$DEFAULT_REPO_DIR}"
STATE_DIR="${NSM_STATE_DIR:-/var/lib/nsm-auto-update}"
RUNTIME_DIR="${NSM_RUNTIME_DIR:-/srv/network-platform}"
PUBLISH_FAILURE_LOG="${NSM_PUBLISH_FAILURE_LOG:-0}"
CONFIG_FILE="/etc/default/nsm-auto-update"

if [[ $EUID -ne 0 ]]; then
  if [[ $REFRESH_COMPONENTS_ONLY -eq 1 ]]; then
    exec sudo --preserve-env=NSM_REPO_DIR,NSM_RUNTIME_DIR,NSM_STATE_DIR,NSM_GIT_USER,NSM_GIT_HOME,NSM_SSH_KEY,NSM_KNOWN_HOSTS,NSM_PUBLISH_FAILURE_LOG \
      "$0" --refresh-components-only
  fi
  exec sudo --preserve-env=NSM_REPO_DIR,NSM_RUNTIME_DIR,NSM_STATE_DIR,NSM_GIT_USER,NSM_GIT_HOME,NSM_SSH_KEY,NSM_KNOWN_HOSTS,NSM_PUBLISH_FAILURE_LOG "$0"
fi

[[ -d "$REPO_DIR/.git" ]] || { echo "Repository non trovato: $REPO_DIR" >&2; exit 1; }
[[ -f "$REPO_DIR/scripts/nsm-auto-update.sh" ]] || { echo "Script auto-update mancante." >&2; exit 1; }
[[ -f "$REPO_DIR/deploy/systemd/nsm-auto-update.service" ]] || { echo "Unit systemd mancante." >&2; exit 1; }
[[ -f "$REPO_DIR/deploy/systemd/nsm-auto-update.timer" ]] || { echo "Timer systemd mancante." >&2; exit 1; }

install_components(){
  install -m 0755 "$REPO_DIR/scripts/nsm-auto-update.sh" /usr/local/sbin/nsm-auto-update
  install -m 0644 "$REPO_DIR/deploy/systemd/nsm-auto-update.service" /etc/systemd/system/nsm-auto-update.service
  install -m 0644 "$REPO_DIR/deploy/systemd/nsm-auto-update.timer" /etc/systemd/system/nsm-auto-update.timer
  systemctl daemon-reload
}

# During a normal validated deployment, update.sh invokes this mode to refresh
# the installed executable/unit files. If host-local configuration already
# exists, preserve it exactly: it can contain intentional operator overrides.
if [[ $REFRESH_COMPONENTS_ONLY -eq 1 && -s "$CONFIG_FILE" ]]; then
  install_components
  echo "Componenti auto-update aggiornati; configurazione host preservata in $CONFIG_FILE."
  exit 0
fi

# Migration path for installations created before /etc/default/nsm-auto-update
# existed. The repository owner is the safest generic fallback when this script
# is invoked by the root systemd updater and SUDO_USER is not available.
EXPLICIT_GIT_USER="${NSM_GIT_USER:-}"
GIT_USER="${EXPLICIT_GIT_USER:-${SUDO_USER:-}}"
if [[ -z "$GIT_USER" || ( "$GIT_USER" == "root" && -z "$EXPLICIT_GIT_USER" ) ]]; then
  repo_owner="$(stat -c '%U' "$REPO_DIR" 2>/dev/null || true)"
  if [[ -n "$repo_owner" && "$repo_owner" != "UNKNOWN" && "$repo_owner" != "root" ]]; then
    GIT_USER="$repo_owner"
  elif [[ -z "$EXPLICIT_GIT_USER" ]]; then
    GIT_USER=""
  fi
fi
[[ -n "$GIT_USER" && "$GIT_USER" != "UNKNOWN" ]] || { echo "Impossibile determinare l'utente Git. Imposta NSM_GIT_USER." >&2; exit 1; }

GIT_HOME="${NSM_GIT_HOME:-}"
if [[ -z "$GIT_HOME" ]]; then
  GIT_HOME="$(getent passwd "$GIT_USER" 2>/dev/null | awk -F: '{print $6}' || true)"
fi
[[ -n "$GIT_HOME" ]] || { echo "Home non trovata per l'utente Git $GIT_USER. Imposta NSM_GIT_HOME." >&2; exit 1; }

SSH_KEY="${NSM_SSH_KEY:-$GIT_HOME/.ssh/id_ed25519}"
KNOWN_HOSTS="${NSM_KNOWN_HOSTS:-$GIT_HOME/.ssh/known_hosts}"
[[ -r "$SSH_KEY" ]] || { echo "Chiave SSH GitHub non trovata per $GIT_USER: $SSH_KEY" >&2; exit 1; }
[[ -r "$KNOWN_HOSTS" ]] || { echo "known_hosts SSH non trovato per $GIT_USER: $KNOWN_HOSTS" >&2; exit 1; }

install -d -m 0750 "$STATE_DIR"
install -d -m 0755 /etc/default
{
  printf 'NSM_REPO_DIR=%s\n' "$REPO_DIR"
  printf 'NSM_RUNTIME_DIR=%s\n' "$RUNTIME_DIR"
  printf 'NSM_STATE_DIR=%s\n' "$STATE_DIR"
  printf 'NSM_GIT_USER=%s\n' "$GIT_USER"
  printf 'NSM_GIT_HOME=%s\n' "$GIT_HOME"
  printf 'NSM_SSH_KEY=%s\n' "$SSH_KEY"
  printf 'NSM_KNOWN_HOSTS=%s\n' "$KNOWN_HOSTS"
  printf 'NSM_PUBLISH_FAILURE_LOG=%s\n' "$PUBLISH_FAILURE_LOG"
} > "$CONFIG_FILE"
chmod 0640 "$CONFIG_FILE"

install_components

if [[ $REFRESH_COMPONENTS_ONLY -eq 1 ]]; then
  echo "Componenti auto-update aggiornati e configurazione host creata in $CONFIG_FILE."
  exit 0
fi

git_head="$(runuser -u "$GIT_USER" -- git -C "$REPO_DIR" rev-parse HEAD)"
printf '%s\n' "$git_head" > "$STATE_DIR/deployed_commit"
printf '%s\n' "$git_head" > "$STATE_DIR/last_attempted_commit"
printf 'success\n' > "$STATE_DIR/last_result"
chmod 0640 "$STATE_DIR/deployed_commit" "$STATE_DIR/last_attempted_commit" "$STATE_DIR/last_result"

touch /var/log/nsm-auto-update.log
chmod 0640 /var/log/nsm-auto-update.log

systemctl enable --now nsm-auto-update.timer

# Safe first check. If deploy does not exist yet, the service simply exits.
systemctl start nsm-auto-update.service || true

echo
echo "NSM automatic update installato."
echo "Repository: $REPO_DIR"
echo "Utente Git: $GIT_USER"
echo "Configurazione: $CONFIG_FILE"
echo "Commit iniziale registrato: $git_head"
echo
echo "Timer:"
systemctl --no-pager status nsm-auto-update.timer || true
echo
echo "Ultimi log locali:"
tail -n 10 /var/log/nsm-auto-update.log || true

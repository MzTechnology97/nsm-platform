#!/usr/bin/env bash
set -Eeuo pipefail

REPO_DIR="${NSM_REPO_DIR:-/home/cda/nsm-platform}"
GIT_USER="${NSM_GIT_USER:-cda}"
STATE_DIR="${NSM_STATE_DIR:-/var/lib/nsm-auto-update}"

if [[ $EUID -ne 0 ]]; then
  exec sudo --preserve-env=NSM_REPO_DIR,NSM_GIT_USER,NSM_STATE_DIR "$0" "$@"
fi

[[ -d "$REPO_DIR/.git" ]] || { echo "Repository non trovato: $REPO_DIR" >&2; exit 1; }
[[ -f "$REPO_DIR/scripts/nsm-auto-update.sh" ]] || { echo "Script auto-update mancante." >&2; exit 1; }
[[ -f "$REPO_DIR/deploy/systemd/nsm-auto-update.service" ]] || { echo "Unit systemd mancante." >&2; exit 1; }
[[ -f "$REPO_DIR/deploy/systemd/nsm-auto-update.timer" ]] || { echo "Timer systemd mancante." >&2; exit 1; }
[[ -r "/home/$GIT_USER/.ssh/id_ed25519" ]] || { echo "Chiave SSH GitHub non trovata per $GIT_USER." >&2; exit 1; }
[[ -r "/home/$GIT_USER/.ssh/known_hosts" ]] || { echo "known_hosts SSH non trovato per $GIT_USER." >&2; exit 1; }

install -d -m 0750 "$STATE_DIR"
install -m 0755 "$REPO_DIR/scripts/nsm-auto-update.sh" /usr/local/sbin/nsm-auto-update
install -m 0644 "$REPO_DIR/deploy/systemd/nsm-auto-update.service" /etc/systemd/system/nsm-auto-update.service
install -m 0644 "$REPO_DIR/deploy/systemd/nsm-auto-update.timer" /etc/systemd/system/nsm-auto-update.timer

git_head="$(runuser -u "$GIT_USER" -- git -C "$REPO_DIR" rev-parse HEAD)"
printf '%s\n' "$git_head" > "$STATE_DIR/deployed_commit"
printf '%s\n' "$git_head" > "$STATE_DIR/last_attempted_commit"
printf 'success\n' > "$STATE_DIR/last_result"
chmod 0640 "$STATE_DIR/deployed_commit" "$STATE_DIR/last_attempted_commit" "$STATE_DIR/last_result"

touch /var/log/nsm-auto-update.log
chmod 0640 /var/log/nsm-auto-update.log

systemctl daemon-reload
systemctl enable --now nsm-auto-update.timer

# Safe first check. If deploy does not exist yet, the service simply exits.
systemctl start nsm-auto-update.service || true

echo
echo "NSM automatic update installato."
echo "Repository: $REPO_DIR"
echo "Commit iniziale registrato: $git_head"
echo
echo "Timer:"
systemctl --no-pager status nsm-auto-update.timer || true
echo
echo "Ultimi log locali:"
tail -n 10 /var/log/nsm-auto-update.log || true

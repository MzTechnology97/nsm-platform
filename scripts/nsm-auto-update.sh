#!/usr/bin/env bash
set -Eeuo pipefail

REPO_DIR="${NSM_REPO_DIR:-/home/cda/nsm-platform}"
RUNTIME_DIR="${NSM_RUNTIME_DIR:-/srv/network-platform}"
STATE_DIR="${NSM_STATE_DIR:-/var/lib/nsm-auto-update}"
GIT_USER="${NSM_GIT_USER:-cda}"
GIT_HOME="${NSM_GIT_HOME:-/home/cda}"
SSH_KEY="${NSM_SSH_KEY:-/home/cda/.ssh/id_ed25519}"
KNOWN_HOSTS="${NSM_KNOWN_HOSTS:-/home/cda/.ssh/known_hosts}"
DEPLOY_REF="${NSM_DEPLOY_REF:-refs/remotes/origin/deploy}"
LOG_BRANCH="${NSM_LOG_BRANCH:-runtime-logs}"
LOCAL_LOG="${NSM_LOCAL_LOG:-/var/log/nsm-auto-update.log}"
LOCK_FILE="$STATE_DIR/lock"
DEPLOYED_FILE="$STATE_DIR/deployed_commit"
ATTEMPTED_FILE="$STATE_DIR/last_attempted_commit"
RESULT_FILE="$STATE_DIR/last_result"

mkdir -p "$STATE_DIR"
touch "$LOCAL_LOG"
chmod 0640 "$LOCAL_LOG"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  exit 0
fi

SSH_COMMAND="ssh -i $SSH_KEY -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$KNOWN_HOSTS"

log(){
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOCAL_LOG"
}

git_user(){
  runuser -u "$GIT_USER" -- env HOME="$GIT_HOME" GIT_SSH_COMMAND="$SSH_COMMAND" \
    git -C "$REPO_DIR" "$@"
}

git_tmp(){
  local dir="$1"; shift
  runuser -u "$GIT_USER" -- env HOME="$GIT_HOME" GIT_SSH_COMMAND="$SSH_COMMAND" \
    git -C "$dir" "$@"
}

sanitize_report(){
  local source="$1" destination="$2"
  sed -E \
    -e '/(password|secret|token|authorization|cookie|api[_-]?key|private[_-]?key|encryption_master_key)/I {s/.*/[REDACTED: sensitive line]/;}' \
    -e 's/enr_[A-Za-z0-9._-]+/[REDACTED_ENROLLMENT_TOKEN]/g' \
    "$source" > "$destination"
}

publish_failure_log(){
  local report="$1" target="$2"
  local safe_report worktree group
  safe_report="$(mktemp)"
  worktree="$(mktemp -d /tmp/nsm-runtime-log.XXXXXX)"
  group="$(id -gn "$GIT_USER")"
  sanitize_report "$report" "$safe_report"
  chown "$GIT_USER:$group" "$worktree"

  # Refresh remote refs. A GitHub/network failure is logged locally; deployment
  # must never be marked successful merely because reporting failed.
  git_user fetch --prune origin >/dev/null 2>&1 || true

  if git_user show-ref --verify --quiet "refs/remotes/origin/$LOG_BRANCH"; then
    if ! git_user worktree add --detach "$worktree" "refs/remotes/origin/$LOG_BRANCH" >/dev/null 2>&1; then
      log "Impossibile creare worktree per $LOG_BRANCH; log disponibile solo localmente."
      rm -rf "$worktree" "$safe_report"
      return 1
    fi
  else
    if ! git_user worktree add --detach "$worktree" "$target" >/dev/null 2>&1; then
      log "Impossibile creare worktree iniziale per i log runtime."
      rm -rf "$worktree" "$safe_report"
      return 1
    fi
    git_tmp "$worktree" switch --orphan "$LOG_BRANCH" >/dev/null 2>&1
    git_tmp "$worktree" rm -rf . >/dev/null 2>&1 || true
  fi

  install -o "$GIT_USER" -g "$group" -m 0644 "$safe_report" "$worktree/log"
  git_tmp "$worktree" add log
  git_tmp "$worktree" -c user.name="NSM Auto Update" \
    -c user.email="nsm-auto-update@localhost" \
    commit -m "runtime: deploy failure ${target:0:12}" >/dev/null

  if git_tmp "$worktree" push origin "HEAD:refs/heads/$LOG_BRANCH" >/dev/null 2>&1; then
    log "Errore pubblicato su branch $LOG_BRANCH, file log."
  else
    log "Push del log runtime fallito; copia locale conservata in $LOCAL_LOG."
  fi

  git_user worktree remove --force "$worktree" >/dev/null 2>&1 || rm -rf "$worktree"
  rm -f "$safe_report"
}

fail_attempt(){
  local report="$1" target="$2" reason="$3"
  printf '%s\n' "$target" > "$ATTEMPTED_FILE"
  printf 'failed\n' > "$RESULT_FILE"
  {
    echo
    echo "=== AUTO UPDATE FAILURE ==="
    echo "timestamp_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "host=$(hostname -f 2>/dev/null || hostname)"
    echo "target_commit=$target"
    echo "reason=$reason"
    echo
    echo "=== DOCKER STATUS ==="
    if [[ -d "$RUNTIME_DIR" ]]; then
      (cd "$RUNTIME_DIR" && docker compose ps) || true
      echo
      echo "=== LAST CONTAINER LOGS ==="
      (cd "$RUNTIME_DIR" && docker compose logs --tail=220 postgres redis migrate api worker caddy) || true
    fi
  } >> "$report" 2>&1
  log "Deploy ${target:0:12} fallito: $reason"
  publish_failure_log "$report" "$target" || true
}

[[ $EUID -eq 0 ]] || { echo "nsm-auto-update deve essere eseguito come root." >&2; exit 1; }
[[ -d "$REPO_DIR/.git" ]] || { log "Repository non trovato: $REPO_DIR"; exit 1; }
[[ -r "$SSH_KEY" ]] || { log "Chiave SSH non leggibile: $SSH_KEY"; exit 1; }
[[ -r "$KNOWN_HOSTS" ]] || { log "known_hosts non leggibile: $KNOWN_HOSTS"; exit 1; }

report="$(mktemp)"
trap 'rm -f "$report"' EXIT

echo "NSM automatic deployment report" > "$report"
echo "started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$report"

if ! git_user fetch --prune origin >> "$report" 2>&1; then
  log "Git fetch fallito; nessun deploy eseguito."
  exit 1
fi

if ! target="$(git_user rev-parse --verify "$DEPLOY_REF" 2>>"$report")"; then
  log "Branch deploy non ancora disponibile; nessun deploy eseguito."
  exit 0
fi

deployed="$(cat "$DEPLOYED_FILE" 2>/dev/null || true)"
attempted="$(cat "$ATTEMPTED_FILE" 2>/dev/null || true)"
last_result="$(cat "$RESULT_FILE" 2>/dev/null || true)"

if [[ "$target" == "$deployed" ]]; then
  exit 0
fi

# A failed commit is tried once. A new validated deploy commit automatically
# clears this condition and is attempted on the next timer tick.
if [[ "$target" == "$attempted" && "$last_result" == "failed" ]]; then
  exit 0
fi

log "Nuovo commit validato rilevato: ${target:0:12}."
printf '%s\n' "$target" > "$ATTEMPTED_FILE"
printf 'running\n' > "$RESULT_FILE"

if [[ -n "$(git_user status --porcelain --untracked-files=normal 2>>"$report")" ]]; then
  fail_attempt "$report" "$target" "repository con modifiche locali"
  exit 1
fi

if ! git_user switch main >> "$report" 2>&1; then
  fail_attempt "$report" "$target" "impossibile selezionare branch main"
  exit 1
fi

if ! git_user reset --hard "$target" >> "$report" 2>&1; then
  fail_attempt "$report" "$target" "impossibile allineare il repository al commit deploy"
  exit 1
fi

{
  echo
  echo "=== DEPLOY OUTPUT ==="
  echo "previous_deployed_commit=${deployed:-unknown}"
  echo "target_commit=$target"
} >> "$report"

set +e
"$REPO_DIR/update.sh" >> "$report" 2>&1
rc=$?
set -e

if [[ $rc -ne 0 ]]; then
  fail_attempt "$report" "$target" "update.sh exit code $rc"
  exit "$rc"
fi

printf '%s\n' "$target" > "$DEPLOYED_FILE"
printf '%s\n' "$target" > "$ATTEMPTED_FILE"
printf 'success\n' > "$RESULT_FILE"
log "Deploy ${target:0:12} completato con successo."

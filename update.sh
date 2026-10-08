#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
SOURCE_DIR="${NSM_REPO_DIR:-$SCRIPT_DIR}"
PLATFORM_DIR="${PLATFORM_DIR:-/srv/network-platform}"
APP_RUNTIME_UID="${APP_RUNTIME_UID:-10001}"
APP_RUNTIME_GID="${APP_RUNTIME_GID:-10001}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$PLATFORM_DIR/data/backups/platform-db"
DEVICE_BACKUP_DIR="$PLATFORM_DIR/data/backups/device-files"
BACKUP_FILE="$BACKUP_DIR/network_platform_${STAMP}.sql.gz"

info(){ printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
ok(){ printf '\033[1;32m[ OK ]\033[0m %s\n' "$*"; }
die(){ printf '\033[1;31m[FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

if ! SOURCE_DIR="$(cd "$SOURCE_DIR" 2>/dev/null && pwd -P)"; then
  die "Repository sorgente non trovato. Esegui update.sh dal clone Git oppure imposta NSM_REPO_DIR=/percorso/del/repository."
fi
[[ -d "$PLATFORM_DIR" ]] || die "Installazione attiva non trovata in $PLATFORM_DIR."
PLATFORM_REAL="$(cd "$PLATFORM_DIR" && pwd -P)"

# update.sh is also copied into the runtime directory for traceability. Running
# that copy without an explicit repository source used to make SOURCE_DIR equal
# to PLATFORM_DIR; the script would then delete runtime app/config and try to
# copy them from the same directory it had just removed. Refuse that destructive
# mode before any backup, rm, Docker operation, or other mutation takes place.
if [[ "$SOURCE_DIR" == "$PLATFORM_REAL" ]]; then
  die "Rifiuto aggiornamento: la sorgente coincide con il runtime ($PLATFORM_REAL). Esegui update.sh dal clone Git oppure usa NSM_REPO_DIR=/percorso/del/repository."
fi

[[ -d "$SOURCE_DIR/.git" ]] || die "Repository sorgente non valido in $SOURCE_DIR: directory .git assente."
[[ -f "$SOURCE_DIR/docker-compose.yml" ]] || die "Repository sorgente non valido: docker-compose.yml mancante."
[[ -d "$SOURCE_DIR/app" ]] || die "Repository sorgente non valido: app/ mancante."
[[ -d "$SOURCE_DIR/config" ]] || die "Repository sorgente non valido: config/ mancante."
[[ -d "$SOURCE_DIR/genieacs" ]] || die "Repository sorgente non valido: genieacs/ mancante."
[[ -f "$SOURCE_DIR/manage.sh" ]] || die "Repository sorgente non valido: manage.sh mancante."
[[ -f "$SOURCE_DIR/update.sh" ]] || die "Repository sorgente non valido: update.sh mancante."
[[ -f "$PLATFORM_DIR/docker-compose.yml" ]] || die "Installazione attiva non trovata in $PLATFORM_DIR."
[[ -f "$PLATFORM_DIR/secrets/bootstrap.env" ]] || die "Secret runtime non trovati."
# Dedicated least-privilege database account for the internet-facing syslog receiver.
grep -q '^SYSLOG_DB_PASSWORD=' "$PLATFORM_DIR/secrets/bootstrap.env" || echo "SYSLOG_DB_PASSWORD=$(openssl rand -hex 24)" | sudo tee -a "$PLATFORM_DIR/secrets/bootstrap.env" >/dev/null

git_repo(){ git -c safe.directory="$SOURCE_DIR" -C "$SOURCE_DIR" "$@"; }

prepare_backup_storage(){
  # api/worker run as the non-root application uid/gid. Re-apply these
  # permissions on every upgrade so existing installations self-heal after
  # older releases created data/backups as root:root with mode 0750.
  sudo mkdir -p "$PLATFORM_DIR/data/backups" "$DEVICE_BACKUP_DIR"
  sudo chown root:"$APP_RUNTIME_GID" "$PLATFORM_DIR/data/backups"
  sudo chmod 0750 "$PLATFORM_DIR/data/backups"
  sudo chown -R "$APP_RUNTIME_UID:$APP_RUNTIME_GID" "$DEVICE_BACKUP_DIR"
  sudo chmod 0750 "$DEVICE_BACKUP_DIR"
}

docker info >/dev/null 2>&1 || die "Docker non disponibile."

if git_repo rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if ! git_repo diff --quiet || ! git_repo diff --cached --quiet; then
    die "Il repository contiene modifiche locali non committate. Risolvile prima dell'upgrade."
  fi
  info "Deploy commit $(git_repo rev-parse --short HEAD) da branch $(git_repo branch --show-current)."
fi

info "Verifico storage backup runtime..."
prepare_backup_storage

info "Creo backup PostgreSQL pre-upgrade..."
sudo mkdir -p "$BACKUP_DIR"
tmp_backup="$(mktemp)"
trap 'rm -f "$tmp_backup"' EXIT
(
  cd "$PLATFORM_DIR"
  docker compose exec -T postgres pg_dump -U network_platform -d network_platform
) | gzip -9 > "$tmp_backup"
[[ -s "$tmp_backup" ]] || die "Backup PostgreSQL vuoto."
sudo mv "$tmp_backup" "$BACKUP_FILE"
sudo chown root:docker "$BACKUP_FILE"
sudo chmod 0640 "$BACKUP_FILE"
ok "Backup DB: $BACKUP_FILE"

info "Aggiorno codice e configurazione applicativa senza toccare data/, secrets/ e .env..."
sudo rm -rf "$PLATFORM_DIR/app" "$PLATFORM_DIR/config" "$PLATFORM_DIR/genieacs"
sudo cp -a "$SOURCE_DIR/app" "$SOURCE_DIR/config" "$SOURCE_DIR/genieacs" "$PLATFORM_DIR/"
sudo install -m 0755 "$SOURCE_DIR/manage.sh" "$PLATFORM_DIR/manage.sh"
sudo install -m 0755 "$SOURCE_DIR/update.sh" "$PLATFORM_DIR/update.sh"
sudo install -m 0640 "$SOURCE_DIR/docker-compose.yml" "$PLATFORM_DIR/docker-compose.yml"
sudo chown -R root:docker "$PLATFORM_DIR/app" "$PLATFORM_DIR/config" "$PLATFORM_DIR/genieacs" "$PLATFORM_DIR/docker-compose.yml" "$PLATFORM_DIR/manage.sh" "$PLATFORM_DIR/update.sh"
sudo chmod -R g+rX "$PLATFORM_DIR/app" "$PLATFORM_DIR/config" "$PLATFORM_DIR/genieacs"
sudo chown -R 70:70 "$PLATFORM_DIR/data/postgres"
sudo chmod 0700 "$PLATFORM_DIR/data/postgres"
prepare_backup_storage

# If automatic deployment is already installed, refresh it from this same
# validated commit. The installer refresh mode also creates the host-local
# /etc/default/nsm-auto-update migration file when upgrading an installation
# that predates environment-file based configuration. It deliberately does not
# touch deployed_commit/last_result and does not start a nested updater run.
if [[ -f "$SOURCE_DIR/scripts/install-auto-update.sh" && -f /etc/systemd/system/nsm-auto-update.service ]]; then
  info "Aggiorno componenti auto-update..."
  sudo env \
    NSM_REPO_DIR="$SOURCE_DIR" \
    NSM_RUNTIME_DIR="$PLATFORM_DIR" \
    "$SOURCE_DIR/scripts/install-auto-update.sh" --refresh-components-only
fi

cd "$PLATFORM_DIR"
info "Build immagini e applico migrazioni..."
docker compose build api worker migrate
docker compose up -d --build

info "Verifico health..."
for _ in $(seq 1 60); do
  if health_json="$(curl -fsS http://127.0.0.1/health 2>/dev/null)"; then
    echo "$health_json"
    ok "Upgrade completato."
    docker compose ps
    exit 0
  fi
  sleep 2
done

docker compose ps || true
docker compose logs --tail=180 || true
echo
echo "Backup DB disponibile per recovery: $BACKUP_FILE" >&2
die "Health check non superato dopo l'upgrade."

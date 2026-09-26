#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
PLATFORM_DIR="${PLATFORM_DIR:-/srv/network-platform}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$PLATFORM_DIR/data/backups/platform-db"
BACKUP_FILE="$BACKUP_DIR/network_platform_${STAMP}.sql.gz"

info(){ printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
ok(){ printf '\033[1;32m[ OK ]\033[0m %s\n' "$*"; }
die(){ printf '\033[1;31m[FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

[[ -f "$SOURCE_DIR/docker-compose.yml" ]] || die "Repository sorgente non valido."
[[ -f "$PLATFORM_DIR/docker-compose.yml" ]] || die "Installazione attiva non trovata in $PLATFORM_DIR."
[[ -f "$PLATFORM_DIR/secrets/bootstrap.env" ]] || die "Secret runtime non trovati."
docker info >/dev/null 2>&1 || die "Docker non disponibile."

if git -C "$SOURCE_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if ! git -C "$SOURCE_DIR" diff --quiet || ! git -C "$SOURCE_DIR" diff --cached --quiet; then
    die "Il repository contiene modifiche locali non committate. Risolvile prima dell'upgrade."
  fi
  info "Deploy commit $(git -C "$SOURCE_DIR" rev-parse --short HEAD) da branch $(git -C "$SOURCE_DIR" branch --show-current)."
fi

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
sudo rm -rf "$PLATFORM_DIR/app" "$PLATFORM_DIR/config"
sudo cp -a "$SOURCE_DIR/app" "$SOURCE_DIR/config" "$PLATFORM_DIR/"
sudo install -m 0755 "$SOURCE_DIR/manage.sh" "$PLATFORM_DIR/manage.sh"
sudo install -m 0755 "$SOURCE_DIR/update.sh" "$PLATFORM_DIR/update.sh"
sudo install -m 0640 "$SOURCE_DIR/docker-compose.yml" "$PLATFORM_DIR/docker-compose.yml"
sudo chown -R root:docker "$PLATFORM_DIR/app" "$PLATFORM_DIR/config" "$PLATFORM_DIR/docker-compose.yml" "$PLATFORM_DIR/manage.sh" "$PLATFORM_DIR/update.sh"
sudo chmod -R g+rX "$PLATFORM_DIR/app" "$PLATFORM_DIR/config"
sudo chown -R 70:70 "$PLATFORM_DIR/data/postgres"
sudo chmod 0700 "$PLATFORM_DIR/data/postgres"

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

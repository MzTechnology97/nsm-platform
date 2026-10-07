#!/usr/bin/env bash
set -Eeuo pipefail

PLATFORM_DIR="${PLATFORM_DIR:-/srv/network-platform}"
APP_RUNTIME_UID="${APP_RUNTIME_UID:-10001}"
APP_RUNTIME_GID="${APP_RUNTIME_GID:-10001}"
DEVICE_BACKUP_DIR="$PLATFORM_DIR/data/backups/device-files"

info(){ printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
ok(){ printf '\033[1;32m[ OK ]\033[0m %s\n' "$*"; }
die(){ printf '\033[1;31m[FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

prepare_backup_storage(){
  # The api/worker image runs as uid/gid 10001 by default. The bind-mounted
  # backup root must therefore be traversable by that gid and device-files
  # must be writable by the runtime user, otherwise artifact/start fails 500.
  sudo mkdir -p "$PLATFORM_DIR/data/backups" "$DEVICE_BACKUP_DIR"
  sudo chown root:"$APP_RUNTIME_GID" "$PLATFORM_DIR/data/backups"
  sudo chmod 0750 "$PLATFORM_DIR/data/backups"
  sudo chown -R "$APP_RUNTIME_UID:$APP_RUNTIME_GID" "$DEVICE_BACKUP_DIR"
  sudo chmod 0750 "$DEVICE_BACKUP_DIR"
}

[[ -f docker-compose.yml ]] || die "Esegui lo script dalla root del repository."
[[ -d app && -d config ]] || die "Sorgenti app/config non trovati."
docker info >/dev/null 2>&1 || die "Docker non è utilizzabile dall'utente corrente."
docker network inspect network-platform-net >/dev/null 2>&1 || die "Rete network-platform-net non trovata."
[[ -f "$PLATFORM_DIR/secrets/bootstrap.env" ]] || die "Secret bootstrap non trovati in $PLATFORM_DIR/secrets/bootstrap.env."

info "Preparo directory e permessi..."
sudo mkdir -p "$PLATFORM_DIR/data/postgres" "$PLATFORM_DIR/data/redis" "$PLATFORM_DIR/data/backups" "$PLATFORM_DIR/data/reports" "$PLATFORM_DIR/data/evidence" "$PLATFORM_DIR/logs"
sudo chown root:docker "$PLATFORM_DIR" "$PLATFORM_DIR/secrets" "$PLATFORM_DIR/secrets/bootstrap.env"
sudo chmod 0750 "$PLATFORM_DIR" "$PLATFORM_DIR/secrets"
sudo chmod 0640 "$PLATFORM_DIR/secrets/bootstrap.env"
sudo chown -R 70:70 "$PLATFORM_DIR/data/postgres"
sudo chmod 0700 "$PLATFORM_DIR/data/postgres"
prepare_backup_storage

info "Installo il core preservando data/, secrets/ e configurazione runtime..."
sudo rm -rf "$PLATFORM_DIR/app" "$PLATFORM_DIR/config" "$PLATFORM_DIR/genieacs"
sudo cp -a app config genieacs "$PLATFORM_DIR/"
sudo install -m 0755 manage.sh "$PLATFORM_DIR/manage.sh"
sudo install -m 0755 update.sh "$PLATFORM_DIR/update.sh"
sudo install -m 0640 docker-compose.yml "$PLATFORM_DIR/docker-compose.yml"

if [[ ! -f "$PLATFORM_DIR/.env" ]]; then
  info "Creo .env runtime da .env.example..."
  sudo install -m 0640 .env.example "$PLATFORM_DIR/.env"
else
  info "Preservo .env runtime esistente."
fi

sudo chown -R root:docker "$PLATFORM_DIR/app" "$PLATFORM_DIR/config" "$PLATFORM_DIR/genieacs" "$PLATFORM_DIR/docker-compose.yml" "$PLATFORM_DIR/.env" "$PLATFORM_DIR/manage.sh" "$PLATFORM_DIR/update.sh"
sudo chmod -R g+rX "$PLATFORM_DIR/app" "$PLATFORM_DIR/config" "$PLATFORM_DIR/genieacs"

cd "$PLATFORM_DIR"
info "Build, migrazioni e avvio stack..."
docker compose up -d --build

info "Attendo health check..."
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1/health >/dev/null 2>&1; then
    ok "Core avviato e health check OK."
    echo
    docker compose ps
    echo
    echo "Per creare un amministratore:"
    echo "  cd $PLATFORM_DIR && ./manage.sh create-admin"
    exit 0
  fi
  sleep 2
done

docker compose ps || true
docker compose logs --tail=150 || true
die "Health check non superato."

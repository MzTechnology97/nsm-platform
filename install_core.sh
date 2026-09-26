#!/usr/bin/env bash
set -Eeuo pipefail
PLATFORM_DIR="${PLATFORM_DIR:-/srv/network-platform}"
info(){ printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
ok(){ printf '\033[1;32m[ OK ]\033[0m %s\n' "$*"; }
die(){ printf '\033[1;31m[FAIL]\033[0m %s\n' "$*" >&2; exit 1; }
[[ -f docker-compose.yml ]] || die "Esegui lo script dalla directory estratta del core."
docker info >/dev/null 2>&1 || die "Docker non è utilizzabile dall'utente corrente."
docker network inspect network-platform-net >/dev/null 2>&1 || die "Rete network-platform-net non trovata."
[[ -f "$PLATFORM_DIR/secrets/bootstrap.env" ]] || die "Secret bootstrap non trovati."
info "Preparo permessi dei secret per il gruppo docker..."
sudo chown root:docker "$PLATFORM_DIR/secrets" "$PLATFORM_DIR/secrets/bootstrap.env"
sudo chmod 750 "$PLATFORM_DIR/secrets"
sudo chmod 640 "$PLATFORM_DIR/secrets/bootstrap.env"
info "Copio il core in $PLATFORM_DIR preservando data/ e secrets/..."
sudo mkdir -p "$PLATFORM_DIR/data/postgres"
sudo cp -a docker-compose.yml .env manage.sh config app "$PLATFORM_DIR/"
sudo chown -R root:docker "$PLATFORM_DIR/app" "$PLATFORM_DIR/config" "$PLATFORM_DIR/docker-compose.yml" "$PLATFORM_DIR/.env" "$PLATFORM_DIR/manage.sh"
sudo chmod -R g+rX "$PLATFORM_DIR/app" "$PLATFORM_DIR/config"
sudo chmod 750 "$PLATFORM_DIR"
sudo chmod 755 "$PLATFORM_DIR/manage.sh"
sudo chmod 640 "$PLATFORM_DIR/docker-compose.yml" "$PLATFORM_DIR/.env"
cd "$PLATFORM_DIR"
info "Build e avvio dello stack..."
docker compose up -d --build
info "Attendo health check..."
for i in $(seq 1 60); do
  if curl -fsS http://127.0.0.1/health >/dev/null 2>&1; then
    ok "Core avviato e health check OK."
    echo
    docker compose ps
    echo
    echo "Ora crea l'amministratore con:"
    echo "  cd $PLATFORM_DIR && ./manage.sh create-admin"
    exit 0
  fi
  sleep 2
done
docker compose ps || true
docker compose logs --tail=120 || true
die "Health check non superato."

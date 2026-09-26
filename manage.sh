#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"

cmd="${1:-help}"
shift || true

case "$cmd" in
  up) docker compose up -d --build ;;
  down) docker compose down ;;
  restart) docker compose restart ;;
  status) docker compose ps ;;
  logs) docker compose logs -f --tail=200 "$@" ;;
  migrate) docker compose run --rm migrate ;;
  create-admin) docker compose run --rm api python -m app.cli create-admin "$@" ;;
  shell) docker compose exec api /bin/sh ;;
  db-shell) docker compose exec postgres psql -U network_platform -d network_platform ;;
  health) curl -fsS http://127.0.0.1/health && echo ;;
  backup-db)
    mkdir -p data/backups/platform-db
    out="data/backups/platform-db/network_platform_$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
    docker compose exec -T postgres pg_dump -U network_platform -d network_platform | gzip -9 > "$out"
    chmod 640 "$out"
    echo "$out"
    ;;
  *)
    cat <<'TXT'
Uso: ./manage.sh <comando>
  up             Build e avvia lo stack
  down           Ferma lo stack
  restart        Riavvia i servizi
  status         Mostra stato container
  logs [service] Segue i log
  migrate        Esegue Alembic upgrade head
  create-admin   Crea un amministratore
  shell          Shell nel container API
  db-shell       psql nel database
  health         Health check via Caddy
  backup-db      Crea pg_dump compresso
TXT
    ;;
esac

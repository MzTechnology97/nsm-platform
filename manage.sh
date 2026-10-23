#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"
# Commands act on the installation (secrets/, .env, data/), not on the Git clone:
# when launched from the clone, move to the installation directory.
PLATFORM_DIR="${PLATFORM_DIR:-/srv/network-platform}"
if [[ ! -f secrets/bootstrap.env && -d "$PLATFORM_DIR/secrets" ]]; then
  echo "manage.sh: uso l'installazione in $PLATFORM_DIR (non il repository $(pwd))." >&2
  cd "$PLATFORM_DIR"
fi

cmd="${1:-help}"
shift || true

# Add a Docker Compose profile to COMPOSE_PROFILES in .env, keeping the others.
add_compose_profile() {
  current=$(sed -n 's/^COMPOSE_PROFILES=//p' .env 2>/dev/null | tail -n 1)
  case ",$current," in
    *",$1,"*) ;;
    *) sed -i '/^COMPOSE_PROFILES=/d' .env; echo "COMPOSE_PROFILES=${current:+$current,}$1" >> .env ;;
  esac
}

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
  seed-demo) docker compose run --rm api python -m app.demo seed ;;
  clear-demo) docker compose run --rm api python -m app.demo clear ;;
  backup-db)
    mkdir -p data/backups/platform-db
    out="data/backups/platform-db/network_platform_$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
    docker compose exec -T postgres pg_dump -U network_platform -d network_platform | gzip -9 > "$out"
    chmod 640 "$out"
    echo "$out"
    ;;
  rotate-secrets)
    # Re-encrypt stored secrets with ENCRYPTION_MASTER_KEY (old key in ENCRYPTION_PREVIOUS_KEYS, see docs/OPERATIONS.md).
    docker compose run --rm api python -m app.cli rotate-secrets
    ;;
  syslog-firewall)
    # Allow the syslog ports only from the networks configured in NSM (DOCKER-USER chain). --apply runs the rules.
    script=$(mktemp)
    docker compose exec -T api python -m app.syslog_firewall > "$script"
    cat "$script"
    if [ "${1:-}" = "--apply" ]; then
      sh "$script" && echo "Regole firewall syslog applicate."
    else
      echo "# Rivedi le regole e applicale con: ./manage.sh syslog-firewall --apply"
    fi
    rm -f "$script"
    ;;
  legacy-backup-enable)
    # Upload-only FTP receiver for RouterOS 6 / 7.12 backups (legacy Agent 0.49.16+).
    # Arguments: IPv4 of NSM reachable by the routers, optional control port (default 2121).
    host="${1:-}"; port="${2:-}"
    case "$host" in *.*.*.*) ;; *) echo "Uso: ./manage.sh legacy-backup-enable <IPv4 di NSM raggiungibile dai router> [porta]" >&2; exit 1 ;; esac
    if [ -n "$port" ]; then
      case "$port" in ''|*[!0-9]*) echo "Porta non valida: $port" >&2; exit 1 ;; esac
      [ "$port" -ge 1 ] && [ "$port" -le 65535 ] || { echo "Porta non valida: $port" >&2; exit 1; }
      sed -i "/^LEGACY_FTP_PORT=/d" .env && echo "LEGACY_FTP_PORT=$port" >> .env
    fi
    grep -q '^LEGACY_FTP_ENABLED=' .env || echo "LEGACY_FTP_ENABLED=1" >> .env
    sed -i "/^LEGACY_FTP_PUBLIC_HOST=/d" .env && echo "LEGACY_FTP_PUBLIC_HOST=$host" >> .env
    add_compose_profile legacyftp
    docker compose up -d --build
    ftp_port=$(sed -n 's/^LEGACY_FTP_PORT=//p' .env | tail -n 1); ftp_port=${ftp_port:-2121}
    pasv=$(sed -n 's/^LEGACY_FTP_PASV_PORTS=//p' .env | tail -n 1); pasv=${pasv:-30100-30109}
    # Host firewall: open the ports when ufw or firewalld is active (NSM itself filters by the allowed subnets).
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
      ufw allow "$ftp_port/tcp" >/dev/null && ufw allow "$(echo "$pasv" | tr '-' ':')/tcp" >/dev/null && echo "ufw: porte $ftp_port e $pasv aperte."
    elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
      firewall-cmd --permanent --add-port="$ftp_port/tcp" --add-port="$pasv/tcp" >/dev/null && firewall-cmd --reload >/dev/null && echo "firewalld: porte $ftp_port e $pasv aperte."
    else
      echo "Nessun firewall attivo sul server: le porte $ftp_port e $pasv sono gia raggiungibili."
    fi
    # Allowed subnets from NSM (Amministrazione -> Sistema): enforce them in DOCKER-USER too when configured.
    script=$(mktemp)
    docker compose exec -T api python -m app.legacy_ftp_firewall > "$script" 2>/dev/null || true
    if [ -s "$script" ] && ! grep -q '^exit 1' "$script"; then
      sh "$script" && echo "Firewall: accesso al ricevitore limitato alle subnet configurate in NSM."
    else
      echo "Nessuna subnet configurata in NSM: impostale in Amministrazione -> Sistema, poi ./manage.sh legacy-ftp-firewall --apply"
    fi
    rm -f "$script"
    echo "Ricevitore FTP dei backup legacy attivo su $host:$ftp_port (passive $pasv)."
    ;;
  legacy-ftp-firewall)
    # Allow the legacy backup FTP ports only from the subnets configured in NSM (DOCKER-USER chain). --apply runs the rules.
    script=$(mktemp)
    docker compose exec -T api python -m app.legacy_ftp_firewall > "$script"
    cat "$script"
    if [ "${1:-}" = "--apply" ]; then
      sh "$script" && echo "Regole firewall del ricevitore FTP applicate."
    else
      echo "# Rivedi le regole e applicale con: ./manage.sh legacy-ftp-firewall --apply"
    fi
    rm -f "$script"
    ;;
  acs-enable)
    # Enable the integrated GenieACS stack: UI secret, compose profile, NBI URL for NSM.
    secrets=secrets/bootstrap.env
    grep -q '^GENIEACS_UI_JWT_SECRET=' "$secrets" || echo "GENIEACS_UI_JWT_SECRET=$(openssl rand -hex 32)" >> "$secrets"
    add_compose_profile acs
    grep -q '^GENIEACS_INTERNAL_NBI_URL=' .env || echo "GENIEACS_INTERNAL_NBI_URL=http://genieacs-nbi:7557" >> .env
    mkdir -p data/genieacs-mongo
    docker compose up -d --build
    echo "GenieACS attivo: ACS URL per le CPE http://<indirizzo-server>:7547"
    echo "NSM si collega da solo al GenieACS integrato (Amministrazione > Integrazioni > GenieACS)."
    echo "Pannello GenieACS: NSM > Amministrazione > ACS > «Apri pannello ACS» (porta 7080, solo amministratori NSM)."
    echo "In alternativa, tunnel SSH: ssh -L 3000:127.0.0.1:3000 <server>, poi http://127.0.0.1:3000"
    ;;
  restore-drill)
    # Restore a dump into a temporary database, check it, drop it; the live database is not touched.
    dir=data/backups/platform-db
    file="${1:-$(ls -1t "$dir"/*.sql.gz 2>/dev/null | head -n 1 || true)}"
    [ -f "$file" ] || { echo "Nessun dump trovato: esegui prima ./manage.sh backup-db" >&2; exit 1; }
    drill=nsm_restore_drill
    pg() { docker compose exec -T postgres "$@"; }
    q() { pg psql -At -U network_platform -d "$drill" -c "$1" 2>/dev/null | tr -d '\r' || true; }
    pg dropdb -U network_platform --if-exists "$drill"
    pg createdb -U network_platform "$drill"
    start=$(date +%s); result=failed; tables=""; revision=""; devices=""
    if gunzip -c "$file" | pg psql -q -v ON_ERROR_STOP=1 -U network_platform -d "$drill" >/dev/null; then
      tables=$(q "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
      revision=$(q "SELECT version_num FROM alembic_version")
      devices=$(q "SELECT count(*) FROM devices")
      [ -n "$revision" ] && [ "${tables:-0}" -gt 0 ] && result=success
    fi
    seconds=$(( $(date +%s) - start ))
    pg dropdb -U network_platform --if-exists "$drill"
    mkdir -p "$dir"
    printf '{"at":"%s","file":"%s","result":"%s","seconds":%s,"tables":"%s","revision":"%s","devices":"%s"}\n' \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(basename "$file")" "$result" "$seconds" "$tables" "$revision" "$devices" >> "$dir/restore-drills.jsonl"
    echo "Prova di ripristino $result: $(basename "$file"), ${seconds}s, tabelle=$tables, revisione=$revision, apparati=$devices"
    [ "$result" = success ]
    ;;
  restore-db)
    # Replace the live database with a dump (see docs/OPERATIONS.md). A safety dump is taken first.
    file="${1:-}"
    [ -f "$file" ] || { echo "Uso: ./manage.sh restore-db <dump.sql.gz>" >&2; exit 1; }
    echo "ATTENZIONE: il database network_platform verrà sostituito con $(basename "$file")."
    printf 'Digita RIPRISTINA per continuare: '
    read -r answer
    [ "$answer" = "RIPRISTINA" ] || { echo "Annullato."; exit 1; }
    "./$(basename "$0")" backup-db
    docker compose stop api worker
    docker compose exec -T postgres dropdb -U network_platform --force network_platform
    docker compose exec -T postgres createdb -U network_platform network_platform
    gunzip -c "$file" | docker compose exec -T postgres psql -q -v ON_ERROR_STOP=1 -U network_platform -d network_platform >/dev/null
    docker compose run --rm migrate
    docker compose start api worker
    echo "Ripristino completato da $(basename "$file")."
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
  restore-drill [dump]  Prova di ripristino su database temporaneo (default: ultimo dump)
  rotate-secrets        Ricifra i segreti con la nuova ENCRYPTION_MASTER_KEY
  syslog-firewall       Regole firewall host per le porte syslog dalle reti consentite (--apply per applicarle)
  acs-enable            Attiva GenieACS integrato (TR-069) nello stack Docker
  legacy-backup-enable  Attiva il ricevitore FTP per i backup dei RouterOS 6 / 7.12 (argomenti: IPv4 di NSM, [porta])
  legacy-ftp-firewall   Regole firewall per il ricevitore FTP dalle subnet consentite in NSM (--apply per applicarle)
  restore-db <dump>     Sostituisce il database con un dump (chiede conferma)
  seed-demo      Crea dataset demo reversibile con backup fittizi
  clear-demo     Rimuove esclusivamente il dataset demo
TXT
    ;;
esac

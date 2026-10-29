#!/usr/bin/env bash
# NSM external probe installer (SCAN-02) for a Debian/Ubuntu host outside the operator network.
#
#   curl -fsSL https://raw.githubusercontent.com/MzTechnology97/nsm-platform/main/probe/install.sh -o install.sh
#   sudo bash install.sh --allow <public IP of the NSM server>            # plain http://<host>:8788
#   sudo bash install.sh --allow <IP> --domain probe.example.net          # https:// through Caddy
#
# Commands: install (default), update, status, show-secret, uninstall. Run "bash install.sh --help".
# Docker Engine and the compose plugin are installed from the official Docker repository when missing.
# Everything lives in /opt/nsm-probe; the secret stays in /opt/nsm-probe/.env (mode 600) and is printed only here.
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/MzTechnology97/nsm-platform"
DIR="/opt/nsm-probe"
REF="main"
SOURCE=""
NAME=""
SECRET=""
ALLOW=""
PORT=""
DOMAIN=""
EMAIL=""
ASSUME_YES=0
COMMAND="install"
CADDY_IMAGE="caddy:2.8.4-alpine"
PYTHON_IMAGE="python:3.12-slim"

usage() {
  cat <<'EOF'
Usage: sudo bash install.sh [command] [options]

Commands:
  install       install or reconfigure the probe (default)
  update        download the probe again from --ref (or --source), rebuild and restart
  status        containers, health and configuration (without the secret)
  show-secret   print the shared secret to paste into NSM
  uninstall     stop and remove the probe and /opt/nsm-probe

Options:
  --allow CIDRS   public address(es) of the NSM server, comma separated (only they may call the probe)
  --name NAME     name shown in NSM next to the results (default: probe-<hostname>)
  --port PORT     plain HTTP port (default 8788; ignored with --domain)
  --domain FQDN   serve https://FQDN through Caddy with a Let's Encrypt certificate (ports 80 and 443)
  --email ADDR    ACME account e-mail for the certificate (optional)
  --secret VALUE  shared secret, at least 32 characters (default: keep the current one or generate one)
  --ref REF       git ref of the NSM repository to download the probe from (default: main)
  --source DIR    use a local checkout instead of downloading (the repository root or its probe/ folder)
  --dir DIR       installation folder (default /opt/nsm-probe)
  -y, --yes       do not ask for confirmations
EOF
}

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!!\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31mxx\033[0m %s\n' "$*" >&2; exit 1; }

parse_args() {
  if [ $# -gt 0 ] && [[ "$1" != -* ]]; then COMMAND="$1"; shift; fi
  while [ $# -gt 0 ]; do
    case "$1" in
      --allow) ALLOW="${2:-}"; shift 2 ;;
      --name) NAME="${2:-}"; shift 2 ;;
      --port) PORT="${2:-}"; shift 2 ;;
      --domain) DOMAIN="${2:-}"; shift 2 ;;
      --email) EMAIL="${2:-}"; shift 2 ;;
      --secret) SECRET="${2:-}"; shift 2 ;;
      --ref) REF="${2:-}"; shift 2 ;;
      --source) SOURCE="${2:-}"; shift 2 ;;
      --dir) DIR="${2:-}"; shift 2 ;;
      -y|--yes) ASSUME_YES=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *) die "unknown option: $1 (see --help)" ;;
    esac
  done
}

confirm() {
  [ "$ASSUME_YES" = 1 ] && return 0
  [ -t 0 ] || die "$1: rerun with --yes to confirm"
  read -r -p "$1 [y/N] " answer
  [[ "$answer" =~ ^[yYsS] ]]
}

need_root() {
  [ "$(id -u)" = 0 ] || [ "${NSM_PROBE_TEST:-}" = 1 ] || die "run as root (sudo bash install.sh ...)"  # NSM_PROBE_TEST: smoke test
}

# --- Saved configuration ----------------------------------------------------------------------

load_config() {
  # probe.conf holds the installer choices; .env holds what the container reads (the secret only there).
  if [ -f "$DIR/probe.conf" ]; then
    local key value
    while IFS='=' read -r key value; do
      case "$key" in
        NAME) [ -n "$NAME" ] || NAME="$value" ;;
        ALLOW) [ -n "$ALLOW" ] || ALLOW="$value" ;;
        PORT) [ -n "$PORT" ] || PORT="$value" ;;
        DOMAIN) [ -n "$DOMAIN" ] || DOMAIN="$value" ;;
        EMAIL) [ -n "$EMAIL" ] || EMAIL="$value" ;;
        REF) [ "$REF" != main ] || REF="$value" ;;
      esac
    done < "$DIR/probe.conf"
  fi
  if [ -z "$SECRET" ] && [ -f "$DIR/.env" ]; then
    SECRET="$(sed -n 's/^PROBE_SECRET=//p' "$DIR/.env" | head -1)"
  fi
}

validate() {
  NAME="${NAME:-probe-$(hostname -s 2>/dev/null || echo 1)}"
  PORT="${PORT:-8788}"
  [[ "$NAME" =~ ^[A-Za-z0-9._-]{1,60}$ ]] || die "--name: letters, digits, dot, dash, underscore (max 60)"
  [[ "$PORT" =~ ^[0-9]+$ ]] && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || die "--port: 1-65535"
  if [ -n "$DOMAIN" ]; then
    [[ "$DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$ ]] || die "--domain: not a host name"
  fi
  if [ -n "$EMAIL" ]; then [[ "$EMAIL" =~ ^[^@[:space:]]+@[^@[:space:]]+$ ]] || die "--email: not an address"; fi
  ALLOW="$(printf '%s' "$ALLOW" | tr -d '[:space:]')"
  local item
  IFS=',' read -r -a items <<< "$ALLOW"
  for item in "${items[@]:-}"; do
    [ -z "$item" ] && continue
    [[ "$item" =~ ^[0-9a-fA-F:.]+(/[0-9]{1,3})?$ ]] || die "--allow: '$item' is not an IP address or CIDR"
  done
  if [ -n "$SECRET" ] && ! [[ "$SECRET" =~ ^[A-Za-z0-9._~+/=-]{32,}$ ]]; then
    die "--secret: at least 32 characters among letters, digits and . _ ~ + / = -"
  fi
}

# --- Docker -----------------------------------------------------------------------------------

install_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then return 0; fi
  [ -r /etc/os-release ] || die "unknown system: install Docker Engine and the compose plugin, then rerun"
  # shellcheck disable=SC1091
  . /etc/os-release
  local distro="${ID:-}"
  case "$distro" in
    ubuntu|debian) ;;
    *) case " ${ID_LIKE:-} " in
         *" ubuntu "*) distro=ubuntu ;;
         *" debian "*) distro=debian ;;
         *) die "automatic Docker install supports Debian and Ubuntu: install Docker Engine and the compose plugin, then rerun" ;;
       esac ;;
  esac
  local codename="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
  [ -n "$codename" ] || die "cannot read the release codename from /etc/os-release"
  say "Installing Docker Engine from download.docker.com ($distro $codename)"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get install -y -q ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/$distro/gpg" -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$distro $codename stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker >/dev/null 2>&1 || true
  docker compose version >/dev/null 2>&1 || die "Docker installed but 'docker compose' does not work"
}

compose() { docker compose --project-directory "$DIR" -p nsm-probe "$@"; }

# --- Files ------------------------------------------------------------------------------------

fetch_sources() {
  local tmp
  tmp="$(mktemp -d)"
  if [ -n "$SOURCE" ]; then
    local root="$SOURCE"
    [ -f "$root/probe/nsm_probe.py" ] || root="$(cd "$SOURCE/.." && pwd)"
    [ -f "$root/probe/nsm_probe.py" ] && [ -f "$root/app/app/port_probes.py" ] || die "--source: probe/nsm_probe.py or app/app/port_probes.py not found"
    cp "$root/probe/nsm_probe.py" "$root/app/app/port_probes.py" "$tmp/"
    say "Probe files taken from $root"
  else
    command -v curl >/dev/null 2>&1 || { apt-get update -q && apt-get install -y -q curl; }
    curl -fsSL "$REPO_RAW/$REF/probe/nsm_probe.py" -o "$tmp/nsm_probe.py" || die "download failed: probe/nsm_probe.py at $REF"
    curl -fsSL "$REPO_RAW/$REF/app/app/port_probes.py" -o "$tmp/port_probes.py" || die "download failed: app/app/port_probes.py at $REF"
    say "Probe files downloaded from the NSM repository ($REF)"
  fi
  grep -q '^VERSION = ' "$tmp/nsm_probe.py" && grep -q 'def probe_tcp' "$tmp/port_probes.py" || die "the downloaded files are not the NSM probe"
  install -d -m 0755 "$DIR/build"
  install -m 0644 "$tmp/nsm_probe.py" "$tmp/port_probes.py" "$DIR/build/"
  rm -rf "$tmp"
}

write_files() {
  install -d -m 0755 "$DIR"
  cat > "$DIR/build/Dockerfile" <<EOF
# Generated by install.sh: the NSM probe with the port checks of NSM, as an unprivileged user.
FROM $PYTHON_IMAGE
RUN useradd --system --no-create-home probe
WORKDIR /probe
COPY port_probes.py nsm_probe.py ./
USER probe
EXPOSE 8788
CMD ["python", "-u", "nsm_probe.py"]
EOF

  local sources="$ALLOW"
  if [ -z "$DOMAIN" ] && [ -n "$sources" ]; then sources="$sources,127.0.0.1/32"; fi  # local healthcheck
  if [ -n "$DOMAIN" ]; then sources=""; fi  # behind Caddy every call comes from Caddy: the filter is in the Caddyfile
  umask 077
  cat > "$DIR/.env" <<EOF
# Generated by install.sh. PROBE_SECRET is the shared secret: the same value goes in NSM.
PROBE_SECRET=$SECRET
PROBE_NAME=$NAME
PROBE_ALLOWED_SOURCES=$sources
PROBE_PORT=8788
PROBE_MAX_REQUESTS=30
EOF
  chmod 600 "$DIR/.env"
  umask 022
  cat > "$DIR/probe.conf" <<EOF
NAME=$NAME
ALLOW=$ALLOW
PORT=$PORT
DOMAIN=$DOMAIN
EMAIL=$EMAIL
REF=$REF
EOF

  local publish caddy=""
  if [ -n "$DOMAIN" ]; then
    publish='    expose:
      - "8788"'
    caddy="
  caddy:
    image: $CADDY_IMAGE
    restart: unless-stopped
    depends_on: [nsm-probe]
    ports:
      - \"80:80\"
      - \"443:443\"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy-data:/data
      - caddy-config:/config
    logging: *logging

volumes:
  caddy-data:
  caddy-config:"
    write_caddyfile
  else
    publish="    ports:
      - \"$PORT:8788\""
    rm -f "$DIR/Caddyfile"
  fi
  cat > "$DIR/docker-compose.yml" <<EOF
# Generated by install.sh: rerun it to change the configuration (manual edits are overwritten).
x-logging: &logging
  driver: json-file
  options:
    max-size: "10m"
    max-file: "3"

services:
  nsm-probe:
    build: ./build
    image: nsm-probe:local
    restart: unless-stopped
    env_file: .env
$publish
    read_only: true
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8788/v1/health', timeout=3).status == 200 else 1)"]
      interval: 60s
      timeout: 5s
      retries: 3
      start_period: 10s
    logging: *logging
$caddy
EOF
}

write_caddyfile() {
  local global="" matcher=""
  [ -n "$EMAIL" ] && global="{
	email $EMAIL
}

"
  if [ -n "$ALLOW" ]; then
    matcher="	@nsm remote_ip ${ALLOW//,/ }
	handle @nsm {
		reverse_proxy nsm-probe:8788
	}
	respond 403"
  else
    matcher="	reverse_proxy nsm-probe:8788"
  fi
  cat > "$DIR/Caddyfile" <<EOF
${global}# Generated by install.sh: only the NSM addresses reach the probe.
$DOMAIN {
$matcher
}
EOF
}

# --- Commands ---------------------------------------------------------------------------------

public_ip() {
  # The source address of the default route (on a VPS the public one), "<this host>" when it cannot be read.
  ip -4 route get 192.0.2.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") {print $(i + 1); exit}}' | grep . || echo "<this host>"
}

probe_url() {
  if [ -n "$DOMAIN" ]; then echo "https://$DOMAIN"; else echo "http://$(public_ip):$PORT"; fi
}

wait_healthy() {
  local id state i
  for i in $(seq 1 30); do
    id="$(compose ps -q nsm-probe 2>/dev/null || true)"
    state="$( [ -n "$id" ] && docker inspect -f '{{.State.Health.Status}}' "$id" 2>/dev/null || echo starting)"
    [ "$state" = healthy ] && return 0
    sleep 2
  done
  warn "the probe is not healthy yet: see 'bash install.sh status' and 'docker compose -p nsm-probe logs'"
  return 1
}

cmd_install() {
  need_root
  load_config
  [ -n "$SECRET" ] || SECRET="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
  validate
  if [ -z "$ALLOW" ]; then
    warn "no --allow: anyone who knows the secret may call the probe. Pass the public IP the NSM server goes out from."
    confirm "Continue without a source restriction?" || exit 1
  fi
  install_docker
  fetch_sources
  write_files
  say "Building and starting the probe"
  compose up -d --build --remove-orphans
  wait_healthy || true
  summary
}

cmd_update() {
  need_root
  [ -f "$DIR/probe.conf" ] || die "not installed in $DIR"
  load_config
  validate
  fetch_sources
  write_files
  compose up -d --build --remove-orphans
  wait_healthy || true
  say "Updated: $(compose exec -T nsm-probe python -c 'import nsm_probe; print(nsm_probe.VERSION)' 2>/dev/null || echo '?')"
}

cmd_status() {
  [ -f "$DIR/probe.conf" ] || die "not installed in $DIR"
  load_config
  compose ps
  echo
  echo "Name:            $NAME"
  echo "Address for NSM: $(probe_url)"
  echo "Allowed sources: ${ALLOW:-anyone with the secret}"
  echo "Downloaded from: ${REF}"
  local health
  health="$(compose exec -T nsm-probe python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8788/v1/health', timeout=3).read().decode())" 2>/dev/null || echo 'not answering')"
  echo "Health:          $health"
}

cmd_show_secret() {
  need_root
  [ -f "$DIR/.env" ] || die "not installed in $DIR"
  sed -n 's/^PROBE_SECRET=//p' "$DIR/.env"
}

cmd_uninstall() {
  need_root
  [ -d "$DIR" ] || die "not installed in $DIR"
  confirm "Remove the probe, its image and $DIR?" || exit 1
  compose down --volumes --rmi local 2>/dev/null || true
  rm -rf "$DIR"
  say "Removed. Disable it in NSM too: Amministrazione → Integrazioni → Sonda esterna."
}

summary() {
  cat <<EOF

NSM external probe "$NAME" is running.

In NSM: Amministrazione → Integrazioni → Sonda esterna
  Indirizzo:  $(probe_url)
  Nome:       $NAME
  Segreto:    $SECRET
Then save and press "Prova la sonda".

The secret is in $DIR/.env (show it again with: sudo bash install.sh show-secret).
EOF
  if [ -z "$DOMAIN" ]; then
    echo "Plain HTTP: the requests are signed and refused after 5 minutes, but use --domain for HTTPS when a name points here."
  else
    echo "HTTPS: $DOMAIN must point to this host and ports 80/443 must be reachable for the certificate."
  fi
}

main() {
  parse_args "$@"
  case "$COMMAND" in
    install) cmd_install ;;
    update) cmd_update ;;
    status) cmd_status ;;
    show-secret) cmd_show_secret ;;
    uninstall) cmd_uninstall ;;
    help) usage ;;
    *) die "unknown command: $COMMAND (see --help)" ;;
  esac
}

main "$@"

#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT
RUNTIME="$TMP_ROOT/runtime"

mkdir -p "$RUNTIME/app" "$RUNTIME/config" "$RUNTIME/secrets"
cp "$REPO_ROOT/update.sh" "$RUNTIME/update.sh"
touch "$RUNTIME/docker-compose.yml" "$RUNTIME/secrets/bootstrap.env"
printf 'sentinel-app\n' > "$RUNTIME/app/KEEP"
printf 'sentinel-config\n' > "$RUNTIME/config/KEEP"

set +e
OUTPUT="$(PLATFORM_DIR="$RUNTIME" bash "$RUNTIME/update.sh" 2>&1)"
RC=$?
set -e

if [[ $RC -eq 0 ]]; then
  echo "Expected runtime-copy update.sh to refuse execution" >&2
  exit 1
fi

grep -Fq "Rifiuto aggiornamento: la sorgente coincide con il runtime" <<<"$OUTPUT"
[[ -f "$RUNTIME/app/KEEP" ]]
[[ -f "$RUNTIME/config/KEEP" ]]

echo "update.sh runtime source guard smoke test passed"

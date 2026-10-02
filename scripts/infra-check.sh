#!/usr/bin/env bash
# Smoke-check the running local stack. Prints one line per check and exits 1 on any failure.
set -uo pipefail
cd "$(dirname "$0")/.."

compose="docker compose -f infra/compose/docker-compose.yml --profile storage --profile ml"
fail=0
ok()   { printf '  ok   %s\n' "$1"; }
bad()  { printf '  FAIL %s\n' "$1"; fail=1; }
pg()   { $compose exec -T postgres psql -U "${POSTGRES_USER:-gharbasket}" -d "${1}" -Atc "${2}" 2>/dev/null; }

echo "PostgreSQL"
version=$(pg gharbasket "show server_version") && [[ "$version" == 18.* ]] && ok "server $version" || bad "server is not 18.x (got '${version:-none}')"
for db in gharbasket gharbasket_test; do
  for ext in postgis vector pg_trgm unaccent pgcrypto; do
    [[ "$(pg "$db" "select count(*) from pg_extension where extname='$ext'")" == "1" ]] \
      && ok "$db has $ext" || bad "$db is missing extension $ext"
  done
done
[[ "$(pg gharbasket "select uuidv7() is not null")" == "t" ]] && ok "uuidv7() available" || bad "uuidv7() missing"
[[ "$(pg gharbasket "select count(*) from pg_database where datname='mlflow'")" == "1" ]] && ok "mlflow database exists" || bad "mlflow database missing"

echo "Valkey"
[[ "$($compose exec -T valkey valkey-cli ping 2>/dev/null | tr -d '\r')" == "PONG" ]] && ok "PING -> PONG" || bad "Valkey not answering"

echo "Optional services (only if started)"
if curl -sf -m 3 "http://127.0.0.1:${S3_PORT:-8333}/" >/dev/null 2>&1 || curl -s -m 3 -o /dev/null -w '%{http_code}' "http://127.0.0.1:${S3_PORT:-8333}/" 2>/dev/null | grep -qE '^(200|403)$'; then
  ok "S3 endpoint answers on :${S3_PORT:-8333}"
else
  echo "  --   S3 storage not running (start with: just up-storage)"
fi
if curl -sf -m 3 "http://127.0.0.1:${MLFLOW_PORT:-5001}/health" >/dev/null 2>&1; then
  ok "MLflow healthy on :${MLFLOW_PORT:-5001}"
else
  echo "  --   MLflow not running (start with: just up-all)"
fi

exit $fail

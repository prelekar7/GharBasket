#!/usr/bin/env bash
# Run both frontends on the real local backend (no mocks): `just dev-real`.
#
#   API      http://localhost:8000   just dev, APP_ENV=dev (dev sign-in: any seed phone, code 123456)
#   Mobile   http://localhost:5173   Vite, VITE_API_MOCK empty, no Firebase, /v1 proxied to the API
#   Console  http://localhost:3000   Next.js, GB_API_MOCK=false, server-side calls to the API
#
# Checks that `just up` is running, migrates and seeds (`--refresh`), then starts the three with a
# coloured prefix on each line. Ctrl-C stops all three. Needs bash 3.2 (macOS): no associative arrays.
set -uo pipefail
cd "$(dirname "$0")/.."

API_PORT=8000
MOBILE_PORT=5173
CONSOLE_PORT=3000
compose="docker compose -f infra/compose/docker-compose.yml"

if [ -t 1 ]; then
  c_api=$'\033[36m' c_mob=$'\033[32m' c_con=$'\033[35m' c_dim=$'\033[2m' c_bold=$'\033[1m' c_off=$'\033[0m'
else
  c_api='' c_mob='' c_con='' c_dim='' c_bold='' c_off=''
fi
fail() { printf 'dev-real: %s\n' "$1" >&2; exit 1; }
ok()   { printf '  ok   %s\n' "$1"; }

# 1. The local stack ------------------------------------------------------------------------------
echo "Checking the local stack"
[ -f .env ] || fail "no .env; run: cp .env.example .env"
$compose exec -T postgres pg_isready -U "${POSTGRES_USER:-gharbasket}" >/dev/null 2>&1 \
  || fail "Postgres is not running; start it with: just up"
ok "Postgres"
[ "$($compose exec -T valkey valkey-cli ping 2>/dev/null | tr -d '\r')" = "PONG" ] \
  || fail "Valkey is not answering; start it with: just up"
ok "Valkey"
if curl -s -m 3 -o /dev/null "http://127.0.0.1:${S3_PORT:-8333}/"; then
  ok "S3 storage (uploads work)"
else
  echo "  --   S3 storage is not running: photo and document uploads will fail (start it with: just up-storage)"
fi
for entry in "API:$API_PORT" "mobile:$MOBILE_PORT" "console:$CONSOLE_PORT"; do
  port=${entry#*:}
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    fail "port $port (${entry%%:*}) is already in use; stop what is running there first"
  fi
done
ok "ports $API_PORT, $MOBILE_PORT and $CONSOLE_PORT are free"

# A Vite env file with Firebase keys does not matter: an empty variable in the process environment
# beats .env.local (checked on Vite 8), and the mobile app below gets every Firebase key empty.
if grep -qsE '^VITE_FIREBASE_[A-Z_]+=.+' frontend/mobile/.env.local frontend/mobile/.env.development.local 2>/dev/null; then
  echo "  --   frontend/mobile/.env.local has Firebase keys; they are overridden for this run (your file is not touched)"
fi

# 2. Database -------------------------------------------------------------------------------------
echo "Migrating and seeding"
just migrate || fail "just migrate failed"
just seed --refresh || fail "just seed failed"

# 3. Who can sign in ------------------------------------------------------------------------------
echo
echo "${c_bold}Sign in with a seed phone (OTP is always 123456)${c_off}"
uv run python - <<'PY' || fail "could not read the seed people"
from gharbasket.tools.seed import CAPTAINS, PEOPLE

CONSOLE = {"admin", "hub_manager", "franchise_owner", "ngo_coordinator", "catalog_editor"}
captains = {phone for phone, _, _ in CAPTAINS}
for p in PEOPLE:
    roles = ["customer"] + [r for r, _ in p.roles]
    if p.phone in captains:
        roles.append("captain")
    staff = [r for r in roles if r in CONSOLE]
    apps = (["console :3000"] if staff else []) + ["mobile :5173"]
    print(f"  {p.phone[3:]}  {p.name:<16} {', '.join(roles[1:]) or 'customer':<26} {' + '.join(apps)}")
PY
echo "${c_dim}  Mobile (http://localhost:5173) is for customers, captains, kitchen partners, riders and packers;"
echo "  the console (http://localhost:3000) is for admin, hub manager, franchise owner and NGO coordinator.${c_off}"
echo "  Use Chrome or Firefox: Safari rejects the Secure cookies on http://localhost."
echo

# 4. The three processes --------------------------------------------------------------------------
PIDS=()
STOPPING=0

stop_all() {
  [ "$STOPPING" = 1 ] && return
  STOPPING=1
  trap '' INT TERM
  set +m # no "Terminated" job notices
  echo
  echo "Stopping..."
  for pid in "${PIDS[@]}"; do kill -TERM -- "-$pid" 2>/dev/null; done
  # Give them a moment, then make sure nothing is left holding a port.
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    alive=0
    for pid in "${PIDS[@]}"; do kill -0 -- "-$pid" 2>/dev/null && alive=1; done
    [ "$alive" = 0 ] && break
    sleep 0.5
  done
  for pid in "${PIDS[@]}"; do kill -KILL -- "-$pid" 2>/dev/null; done
  wait 2>/dev/null
}
trap 'stop_all; exit 130' INT TERM
trap 'stop_all' EXIT

# prefix LABEL COLOUR: put a coloured "label |" in front of every line read from stdin.
prefix() {
  local label=$1 colour=$2 line
  while IFS= read -r line; do
    printf '%s%-7s|%s %s\n' "$colour" "$label" "$c_off" "$line"
  done
}

# Job control gives every background job its own process group, so one signal stops a whole tree
# (uvicorn's reloader, pnpm, Vite, Next.js).
set -m

# Each job is a subshell wrapping "command | prefix", so $! is the process group to signal.
(
  (
    export APP_ENV=dev LOG_JSON=false
    # Empty, not unset: a FIREBASE_PROJECT_ID in .env would otherwise still be read.
    export FIREBASE_PROJECT_ID=
    just dev
  ) 2>&1 | prefix api "$c_api"
) &
PIDS+=($!)

(
  (
    # Empty values beat any .env.local: the mobile app then signs in with dev_phone and calls /v1.
    export VITE_API_MOCK= VITE_FIREBASE_API_KEY= VITE_FIREBASE_AUTH_DOMAIN= VITE_FIREBASE_PROJECT_ID=
    export VITE_FIREBASE_APP_ID= VITE_FIREBASE_MESSAGING_SENDER_ID= VITE_FIREBASE_TEST_MODE=
    export VITE_API_PROXY_TARGET="http://localhost:$API_PORT"
    corepack pnpm --filter ./frontend/mobile dev --port "$MOBILE_PORT" --strictPort
  ) 2>&1 | prefix mobile "$c_mob"
) &
PIDS+=($!)

(
  (
    export GB_API_MOCK=false GB_API_URL="http://localhost:$API_PORT"
    corepack pnpm --filter ./frontend/console dev
  ) 2>&1 | prefix console "$c_con"
) &
PIDS+=($!)

echo "Started: API :$API_PORT, mobile :$MOBILE_PORT, console :$CONSOLE_PORT. Ctrl-C stops all three."
echo

# Run until any of the three ends (then stop the others) or Ctrl-C.
while true; do
  for pid in "${PIDS[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "dev-real: a process ended (see its last lines above); stopping the rest" >&2
      exit 1
    fi
  done
  sleep 1
done

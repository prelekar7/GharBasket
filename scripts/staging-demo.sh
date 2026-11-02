#!/usr/bin/env bash
# Load demo data into staging (handbook 12, "Staging demo data"): a Neon restore point, then the
# seed (adds only), then simulated weeks through the real modules (`just sim --write-db`), all
# data_source = synthetic, then the counts.
#
#   infisical run --env=staging -- just staging-demo        # 1. shows the host, the plan; writes nothing
#   GB_ALLOW_REMOTE_SEED=<host> infisical run --env=staging -- just staging-demo   # 2. loads it
#
# The weeks are the last --weeks full weeks before this IST week. Weeks the simulator already
# replayed are never simulated again: such a window is refused unless --add-weeks, which simulates
# only the weeks after the latest one. A run that would take an `unlocking` society to its unlock
# threshold is refused. At the end the simulated subscriptions are paused, so staging's weekly job
# leaves them alone. Run it from the commit staging runs (checked against /v1/health), after the
# deploy, with no merge or migration during the run: the simulator relays staging's whole outbox
# with this checkout's code. On the local Docker database (no Infisical) it runs the same steps
# without the confirmation, the commit check or the restore point.
set -euo pipefail
cd "$(dirname "$0")/.."

usage() {
  cat <<'EOF'
usage: scripts/staging-demo.sh [options]
  --weeks N        full weeks to simulate, ending last week (default 8, at most 26)
  --adoption X     share of the seed societies' flats that subscribe (default 0.015: 13 households)
  --planner P      cpsat (default) or rules
  --seed N         simulator seed (default 7)
  --add-weeks      add only the weeks after the latest simulated one
  --no-backup      skip the Neon restore point (not recommended)
  --skip-commit-check  write even if this checkout is not the commit staging runs (not recommended)
  --dry-run        show the host, counts and plan, then stop
  -h, --help       this help
A shared database also needs GB_ALLOW_REMOTE_SEED=<its host> (the first run prints it).
EOF
}

weeks=8
adoption=0.015
planner=cpsat
seed=7
add_weeks=""
backup=1
dry_run=0
commit_check=1
while [ $# -gt 0 ]; do
  case "$1" in
    --weeks | --adoption | --planner | --seed)
      if [ $# -lt 2 ]; then
        echo "$1 needs a value" >&2
        exit 2
      fi
      case "$1" in
        --weeks) weeks="$2" ;;
        --adoption) adoption="$2" ;;
        --planner) planner="$2" ;;
        --seed) seed="$2" ;;
      esac
      shift 2
      ;;
    --add-weeks) add_weeks=--add-weeks && shift ;;
    --no-backup) backup=0 && shift ;;
    --skip-commit-check) commit_check=0 && shift ;;
    --dry-run) dry_run=1 && shift ;;
    -h | --help) usage && exit 0 ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done
if ! [[ "$weeks" =~ ^[0-9]+$ ]] || [ "$weeks" -lt 1 ] || [ "$weeks" -gt 26 ]; then
  echo "--weeks must be a whole number from 1 to 26" >&2
  exit 2
fi
if ! [[ "$adoption" =~ ^0?\.[0-9]+$ ]] || [[ "$adoption" =~ ^0?\.0+$ ]]; then
  echo "--adoption must be a share between 0 and 1, e.g. 0.015" >&2
  exit 2
fi
if [ "$planner" != cpsat ] && [ "$planner" != rules ]; then
  echo "--planner must be cpsat or rules" >&2
  exit 2
fi
if ! [[ "$seed" =~ ^[0-9]+$ ]]; then
  echo "--seed must be a whole number" >&2
  exit 2
fi

helper() { uv run --quiet python -m gharbasket_sim.staging "$@"; }

host=$(helper host)
case "$host" in
  localhost | 127.0.0.1 | ::1) shared=0 ;;
  *) shared=1 ;;
esac
echo "database host: $host$([ "$shared" = 1 ] && echo ' (shared)' || echo ' (local Docker)')"
echo "before:"
helper counts

set +e
# shellcheck disable=SC2086 # $add_weeks is empty or one word
window=$(helper window --weeks "$weeks" $add_weeks)
status=$?
set -e
if [ "$status" = 4 ]; then
  echo "nothing to add"
  exit 0
elif [ "$status" != 0 ]; then
  exit "$status"
fi
read -r start sim_weeks <<<"$window"
echo "plan: $sim_weeks week(s) from Monday $start, seed $seed, planner $planner, adoption $adoption"
echo "      $(helper estimate --weeks "$sim_weeks" --adoption "$adoption")"
echo "unlocking societies the run adds households to:"
helper unlock --adoption "$adoption"
code=$(git rev-parse HEAD)
if ! git diff --quiet HEAD --; then
  code="$code+uncommitted changes"
fi
echo "code: $code"

if [ "$dry_run" = 1 ]; then
  echo "dry run: nothing written"
  exit 0
fi
if [ "$shared" = 1 ] && [ "${GB_ALLOW_REMOTE_SEED:-}" != "$host" ]; then
  echo "not confirmed: to write to $host, run again with GB_ALLOW_REMOTE_SEED=$host" >&2
  exit 2
fi
if [ "$shared" = 1 ] && [ "$commit_check" = 1 ]; then
  api="${STAGING_API_URL:-https://gharbasket-api.onrender.com}"
  deployed=$(curl --silent --max-time 90 "$api/v1/health" |
    python3 -c 'import json, sys; print(json.load(sys.stdin).get("commit") or "")' 2>/dev/null || true)
  if [ "$deployed" != "$code" ]; then
    echo "refused: $api runs ${deployed:-an unknown commit}, this checkout is $code." >&2
    echo "Check out the deployed commit (and wait for any deploy to finish) first." >&2
    exit 2
  fi
fi

stamp=$(date -u +%Y%m%dT%H%MZ)
if [ "$shared" = 0 ]; then
  echo "restore point: none (local database)"
elif [ "$backup" = 1 ]; then
  if [ -z "${NEON_API_KEY:-}" ]; then
    echo "NEON_API_KEY is not set: add it to Infisical (staging) or pass --no-backup" >&2
    exit 2
  fi
  uv run --quiet python scripts/neon_restore_point.py before-demo
else
  echo "restore point: skipped (--no-backup)"
fi

echo "seed (adds only on a shared database):"
uv run --quiet python -m gharbasket.tools.seed --allow-remote
helper unlock --adoption "$adoption" >/dev/null # again, now that the seed's households are there
echo "simulating $sim_weeks week(s) through the modules (subscriptions paused at the end):"
uv run --quiet python -m gharbasket_sim --write-db --allow-remote --pause-after --seed "$seed" \
  --weeks "$sim_weeks" --start "$start" --planner "$planner" --adoption "$adoption" \
  --out "data/sim_truth/staging-$stamp"
echo "after:"
helper counts
echo "done in $((SECONDS / 60)) min $((SECONDS % 60)) s"

#!/usr/bin/env bash
# Run CI locally and report it on GitHub as commit statuses (`local-ci/*`), for while GitHub Actions
# cannot start jobs. Mirrors .github/workflows/ci.yml: lint, secrets, python and frontend.
#
#   scripts/ci-local.sh 334            # a PR: checks out its head, runs, posts statuses on it
#   scripts/ci-local.sh main           # any branch, tag or commit
#   scripts/ci-local.sh 334 --no-post  # run without posting
#
# Needs the local stack (`just up`), uv, corepack (pnpm), gitleaks and an authenticated `gh`.
# Each run gets its own worktree and a fresh test database, and removes both at the end, so runs
# for different PRs can go side by side (use CI_VALKEY_DB, 0 to 15, to give each a different Valkey db).
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)

target=${1:?usage: scripts/ci-local.sh <pr-number|ref> [--no-post]}
post=1
[[ "${2:-}" == "--no-post" ]] && post=0

git fetch -q origin
if [[ "$target" =~ ^[0-9]+$ ]]; then
  sha=$(gh pr view "$target" --json headRefOid --jq .headRefOid) || exit 2
  git fetch -q origin "pull/$target/head" || exit 2
else
  sha=$(git rev-parse --verify "origin/$target^{commit}" 2>/dev/null || git rev-parse --verify "$target^{commit}") || exit 2
fi
short=${sha:0:8}
repo=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
work=$(mktemp -d "${TMPDIR:-/tmp}/gb-ci-$short.XXXX")
db="gb_ci_$short"
valkey_db=${CI_VALKEY_DB:-15}
log_dir="$ROOT/.ci-local"
mkdir -p "$log_dir"
log="$log_dir/$short.log"
: >"$log"

compose="docker compose -f $ROOT/infra/compose/docker-compose.yml"
psql_() { $compose exec -T postgres psql -q -U gharbasket "$@"; }

status() { # context state description
  [[ $post == 1 ]] || return 0
  gh api -X POST "repos/$repo/statuses/$sha" -f state="$2" -f context="local-ci/$1" \
    -f description="${3:0:140}" >/dev/null || echo "could not post local-ci/$1" >&2
}

cleanup() {
  git -C "$ROOT" worktree remove --force "$work" >/dev/null 2>&1
  psql_ -d postgres -c "drop database if exists $db" >/dev/null 2>&1
  psql_ -d postgres -c "drop database if exists ${db}_e2e with (force)" >/dev/null 2>&1
}
trap cleanup EXIT

JOBS=(lint secrets python frontend)
for job in "${JOBS[@]}"; do status "$job" pending "running on $(hostname -s)"; done

git worktree add -q --detach "$work" "$sha" || exit 2
cd "$work" || exit 2

# Failed step names per job, in FAILED_<job> (bash 3.2 on macOS has no associative arrays).
for job in "${JOBS[@]}"; do printf -v "FAILED_$job" "%s" ""; done
step() { # job name command...
  local job=$1 name=$2
  shift 2
  echo "=== $job: $name" | tee -a "$log"
  if "$@" >>"$log" 2>&1; then
    echo "    ok" | tee -a "$log"
  else
    echo "    FAILED" | tee -a "$log"
    local var="FAILED_$job"
    printf -v "$var" "%s" "${!var:+${!var}, }$name"
  fi
}
finish() { # job [description when green]
  local var="FAILED_$1"
  if [[ -n "${!var}" ]]; then
    status "$1" failure "failed: ${!var} (log: .ci-local/$short.log)"
  else
    status "$1" success "${2:-passed}"
  fi
}

# --- lint ------------------------------------------------------------------------------------
step lint "pnpm install" corepack pnpm install --frozen-lockfile
step lint "biome" corepack pnpm exec biome ci .
step lint "ruff check" uv run --isolated --frozen --only-group dev ruff check .
step lint "ruff format" uv run --isolated --frozen --only-group dev ruff format --check .
step lint "agent docs" ./scripts/sync-agent-docs.sh --check
finish lint

# --- secrets: the commits this ref adds to main --------------------------------------------
base=$(git merge-base "$sha" origin/main)
if [[ "$base" == "$sha" ]]; then
  step secrets "gitleaks" gitleaks git --no-banner --log-opts="-1 $sha" .
else
  step secrets "gitleaks" gitleaks git --no-banner --log-opts="$base..$sha" .
fi
finish secrets

# --- python ----------------------------------------------------------------------------------
if [[ -f backend/api/pyproject.toml ]]; then
  psql_ -d postgres -c "drop database if exists $db" -c "create database $db" >>"$log" 2>&1
  for ext in postgis vector pg_trgm unaccent pgcrypto; do
    psql_ -d "$db" -c "create extension if not exists $ext" >>"$log" 2>&1
  done
  export GB_REQUIRE_SERVICES=1
  export TEST_DATABASE_URL="postgresql+asyncpg://gharbasket:gharbasket@localhost:5432/$db"
  export TEST_VALKEY_URL="redis://localhost:6380/$valkey_db"
  step python "uv sync" uv sync --all-packages --frozen
  step python "pyright" uv run pyright
  step python "import-linter" uv run --directory backend/api lint-imports
  step python "api tests" uv run --directory backend/api pytest --cov -n 4 --dist worksteal -q -p no:cacheprovider
  coverage=$(grep -o 'Total coverage: [0-9.]*%' "$log" | tail -1)
  step python "ml tests" uv run --directory backend/ml pytest --cov -q -p no:cacheprovider
  step python "simulator tests" uv run --directory data/simulator pytest --cov -q -p no:cacheprovider
  step python "training tests" uv run --directory backend/training pytest --cov -q -p no:cacheprovider
  # Parallel test workers each use a database of their own; the base one is not migrated by them.
  step python "migrate" env DATABASE_URL="$TEST_DATABASE_URL" uv run --directory backend/api alembic upgrade head
  step python "alembic check" env DATABASE_URL="$TEST_DATABASE_URL" uv run --directory backend/api alembic check
  step python "openapi up to date" bash -c \
    "uv run python -m gharbasket.tools.export_openapi '$work/.openapi.json' && diff -u backend/api/openapi.json '$work/.openapi.json'"
  # The simulated day, when the branch has it, on a database and Valkey db of this run's own: two runs side
  # by side would otherwise share the harness defaults (gharbasket_e2e, Valkey db 14) and drop each other's.
  [[ -d e2e/backend ]] && step python "simulated day" env \
    E2E_DATABASE_URL="postgresql+asyncpg://gharbasket:gharbasket@localhost:5432/${db}_e2e" \
    E2E_VALKEY_URL="redis://localhost:6380/$((valkey_db + 80))" \
    E2E_S3_URL="${E2E_S3_URL:-http://localhost:8333}" \
    uv run --directory e2e/backend pytest -q -p no:cacheprovider
  finish python "passed; api ${coverage:-coverage n/a}"
else
  status python success "no backend/api"
fi

# --- frontend --------------------------------------------------------------------------------
if [[ -f frontend/mobile/package.json ]]; then
  step frontend "api-client up to date" bash -c \
    "corepack pnpm --filter @gharbasket/api-client run generate && git diff --exit-code -- frontend/packages/api-client"
  step frontend "api-client tests" corepack pnpm --filter @gharbasket/api-client test
  step frontend "builds" corepack pnpm --filter "./frontend/**" --workspace-concurrency=1 run build
  finish frontend
else
  status frontend success "no frontend"
fi

echo
echo "log: $log"
bad=0
for job in "${JOBS[@]}"; do
  var="FAILED_$job"
  if [[ -n "${!var}" ]]; then echo "FAILED $job: ${!var}"; bad=1; fi
done
((bad)) && exit 1
echo "all green ($short)"

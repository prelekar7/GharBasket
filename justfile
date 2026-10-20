# GharBasket command vocabulary. Run `just` to list recipes.
# Recipes marked "(arrives in …)" are placeholders until that Sprint 0 PR merges.

set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list --unsorted

# Install JS and Python dependencies and git hooks
install:
    pnpm install
    if [ -f pyproject.toml ]; then uv sync --all-packages; fi

# Lint everything (Biome for JS/JSON/CSS, Ruff for Python, module boundaries)
lint:
    pnpm exec biome check .
    uv run ruff check .
    uv run ruff format --check .
    uv run --directory backend/api lint-imports

# Format everything
fmt:
    pnpm exec biome format --write .
    uv run ruff format .

# Copy AGENTS.md into every AI-tool instruction file
sync-agent-docs:
    ./scripts/sync-agent-docs.sh

# Fail if any AI-tool instruction file differs from AGENTS.md (used in CI)
check-agent-docs:
    ./scripts/sync-agent-docs.sh --check

compose := "docker compose -f infra/compose/docker-compose.yml"

# Start the light stack: Postgres 18 (PostGIS, pgvector) and Valkey (~400 MB RAM)
up:
    {{compose}} up -d --wait

# Also start S3-compatible storage (SeaweedFS) and create the buckets
up-storage:
    {{compose}} --profile storage up -d --wait seaweedfs
    {{compose}} --profile storage up storage-init

# Start everything including MLflow (needs about 1.5 GB RAM in total)
up-all:
    {{compose}} --profile storage --profile ml up -d --wait seaweedfs
    {{compose}} --profile storage --profile ml up storage-init
    {{compose}} --profile storage --profile ml up -d --wait

# Build the Mumbai-region road network for OSRM once (downloads ~220 MB; see infra/osrm/README.md)
osrm-prepare:
    ./infra/osrm/prepare.sh

# Start the OSRM routing server on http://localhost:5050 (after `just osrm-prepare`)
up-routing:
    {{compose}} --profile routing up -d --wait osrm

# Stop all containers (data volumes are kept)
down:
    {{compose}} --profile storage --profile ml --profile routing down

# Stop everything and DELETE all local data volumes (database, queue, storage)
reset:
    {{compose}} --profile storage --profile ml --profile routing down -v

# Show container status
ps:
    {{compose}} --profile storage --profile ml --profile routing ps

# Follow logs, e.g. `just logs postgres`
logs *services:
    {{compose}} --profile storage --profile ml --profile routing logs -f --tail=100 {{services}}

# Open a psql shell in the local database
psql db="gharbasket":
    {{compose}} exec postgres psql -U gharbasket -d {{db}}

# Verify extensions and services of the running stack
infra-check:
    ./scripts/infra-check.sh

# Run the API with auto-reload on http://localhost:8000 (readable logs; docs at /v1/docs)
dev:
    APP_ENV=${APP_ENV:-dev} LOG_JSON=${LOG_JSON:-false} uv run uvicorn gharbasket.main:create_app --factory --reload --port 8000 --no-access-log

# Run the ML service (M10) on http://localhost:8100; point the API at it with ML_SERVICE_URL
dev-ml:
    APP_ENV=${APP_ENV:-dev} uv run uvicorn gharbasket_ml.main:create_app --factory --reload --port 8100

# Run the Python tests with the coverage gate (single file: `uv run --directory backend/api pytest tests/test_x.py`)
test:
    uv run --directory backend/api pytest --cov
    uv run --directory backend/ml pytest --cov
    uv run --directory data/simulator pytest --cov
    uv run --directory backend/training pytest --cov

# The backend end-to-end "simulated day" on its own fresh database (gharbasket_e2e, Valkey db 14),
# ML service down; `E2E_ML=1 just e2e-day` with `just dev-ml` running, `E2E_KEEP_DB=1` to keep the DB
e2e-day *args:
    uv run --directory e2e/backend pytest {{args}}

# Run a scheduled job on the local database, e.g. `just job outbox_sweep` (ADR-0021)
job name *args:
    uv run python -m gharbasket.tools.run_job {{name}} {{args}}

# Give a signed-up user a staff role, e.g. `just grant-role +919800000001 admin` (see the tool's help)
grant-role *args:
    uv run python -m gharbasket.tools.grant_role {{args}}

# Fill the local database with synthetic people, one per role (safe to run again; local only).
# Catalog rows are only added; `just seed --refresh` re-applies the seed files to rows nobody edited in the console
seed *args:
    uv run python -m gharbasket.tools.seed {{args}}

# Regenerate backend/api/openapi.json, the contract the frontend client is built from
openapi:
    uv run python -m gharbasket.tools.export_openapi

# Apply all database migrations (uses DATABASE_URL_DIRECT, else DATABASE_URL)
migrate:
    cd backend/api && uv run alembic upgrade head

# Undo the last migration(s), e.g. `just migrate-down` or `just migrate-down 2`
migrate-down steps="1":
    cd backend/api && uv run alembic downgrade -{{steps}}

# Create a migration from model changes, e.g. `just migration "orders add slot table"`
migration message:
    cd backend/api && uv run alembic revision --autogenerate -m "{{message}}"

# Smoke test a deployed API, e.g. `infisical run --env=staging -- just cloud https://gharbasket-api.onrender.com`
cloud url:
    scripts/smoke-staging.sh {{url}}

# Regenerate backend/api/openapi.json, then the JS client types from it (ADR-0022)
gen-api: openapi
    pnpm --filter @gharbasket/api-client run generate

# Simulate synthetic households and write their ground truth (after `just seed`), e.g. `just sim --seed 11`
sim *args:
    uv run python -m gharbasket_sim {{args}}

# Pin the ML image's dependencies: backend/ml/requirements.lock from uv.lock (deploy-ml.yml does the same; git-ignored)
ml-lock:
    uv export --quiet --package gharbasket-ml --frozen --no-dev --no-emit-project -o backend/ml/requirements.lock

# Smoke-test a deployed ML service, e.g. `just cloud-ml https://<space>.hf.space $ML_SERVICE_TOKEN`
cloud-ml url token="":
    uv run --directory backend/ml python -m gharbasket_ml.smoke {{url}} {{token}}

# Evaluate the planner and the pantry baseline over simulator seeds; report in docs/eval (eval protocol)
eval *args:
    uv run python -m gharbasket_training.eval {{args}}

# Point a model's latest.json in the registry at a version (also rolls back), e.g. `just promote basket_ranker 2026-10-05`
promote model version *args:
    uv run --directory backend/training python -m gharbasket_training.jobs.promote {{model}} {{version}} {{args}}

# Score Ghar Saathi on data/eval/assistant_utterances.jsonl (needs ANTHROPIC_API_KEY), e.g. `just eval-assistant --models claude-opus-5-5 claude-sonnet-5-5`
eval-assistant *args:
    uv run --directory backend/ml python -m gharbasket_ml.eval_assistant {{args}}

# Both frontends on the real local backend (API :8000, mobile :5173, console :3000); migrates and seeds first, Ctrl-C stops all
dev-real:
    ./scripts/dev-real.sh

# Sign in as all 13 seed people against a running dev API and check /v1/me against the seed, e.g. `just real-check 8020`
real-check *args:
    ./scripts/real-check.sh {{args}}

# Report the frontends' API calls that openapi.json lacks (docs/integration/contract-gaps.md); `node scripts/contract-gaps.mjs --check` gates
contract-gaps:
    node scripts/contract-gaps.mjs

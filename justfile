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

# Give a signed-up user a staff role, e.g. `just grant-role +919800000001 admin` (see the tool's help)
grant-role *args:
    uv run python -m gharbasket.tools.grant_role {{args}}

# Fill the local database with synthetic people, one per role (safe to run again; local only)
seed:
    uv run python -m gharbasket.tools.seed

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

# Seed synthetic households with the simulator (arrives in feat/M10-simulator-v0)
sim:
    @echo "Not available yet: arrives in feat/M10-simulator-v0"

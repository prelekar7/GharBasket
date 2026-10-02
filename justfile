# GharBasket command vocabulary. Run `just` to list recipes.
# Recipes marked "(arrives in …)" are placeholders until that Sprint 0 PR merges.

set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list --unsorted

# Install JS and Python dependencies and git hooks
install:
    pnpm install
    if [ -f pyproject.toml ]; then uv sync --all-packages; fi

# Lint everything (Biome for TS/JSON/CSS, Ruff for Python)
lint:
    pnpm exec biome check .
    if ls services data 2>/dev/null | grep -q .; then uvx ruff check .; fi

# Format everything
fmt:
    pnpm exec biome format --write .
    uvx ruff format .

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

# Stop all containers (data volumes are kept)
down:
    {{compose}} --profile storage --profile ml down

# Stop everything and DELETE all local data volumes (database, queue, storage)
reset:
    {{compose}} --profile storage --profile ml down -v

# Show container status
ps:
    {{compose}} --profile storage --profile ml ps

# Follow logs, e.g. `just logs postgres`
logs *services:
    {{compose}} --profile storage --profile ml logs -f --tail=100 {{services}}

# Open a psql shell in the local database
psql db="gharbasket":
    {{compose}} exec postgres psql -U gharbasket -d {{db}}

# Verify extensions and services of the running stack
infra-check:
    ./scripts/infra-check.sh

# Run the API with auto-reload on http://localhost:8000 (readable logs; docs at /v1/docs)
dev:
    LOG_JSON=false uv run uvicorn gharbasket.main:create_app --factory --reload --port 8000 --no-access-log

# Run the Python test suite
test:
    uv run pytest services/api

# Regenerate services/api/openapi.json, the contract the frontend client is built from
openapi:
    uv run python -m gharbasket.tools.export_openapi

# Apply all database migrations (uses DATABASE_URL_DIRECT, else DATABASE_URL)
migrate:
    cd services/api && uv run alembic upgrade head

# Undo the last migration(s), e.g. `just migrate-down` or `just migrate-down 2`
migrate-down steps="1":
    cd services/api && uv run alembic downgrade -{{steps}}

# Create a migration from model changes, e.g. `just migration "orders add slot table"`
migration message:
    cd services/api && uv run alembic revision --autogenerate -m "{{message}}"

# Regenerate the JS API client from openapi.json (arrives in feat/M1-auth-contract)
gen-api:
    @echo "Not available yet: arrives in feat/M1-auth-contract"

# Seed synthetic households with the simulator (arrives in feat/M10-simulator-v0)
sim:
    @echo "Not available yet: arrives in feat/M10-simulator-v0"

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

# Run API, workers and frontends in dev mode (arrives in feat/M0-api-platform)
dev:
    @echo "Not available yet: arrives in feat/M0-api-platform"

# Run all tests (arrives in feat/M0-api-platform)
test:
    @echo "Not available yet: arrives in feat/M0-api-platform"

# Apply database migrations (arrives in feat/M0-api-platform)
migrate:
    @echo "Not available yet: arrives in feat/M0-api-platform"

# Regenerate the TypeScript API client from OpenAPI (arrives in feat/M0-walking-skeleton)
gen-api:
    @echo "Not available yet: arrives in feat/M0-walking-skeleton"

# Seed synthetic households with the simulator (arrives in feat/M10-simulator-v0)
sim:
    @echo "Not available yet: arrives in feat/M10-simulator-v0"

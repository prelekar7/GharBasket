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

# Start local infrastructure: Postgres, Valkey, MinIO, MLflow (arrives in chore/M0-infra-compose)
up:
    @echo "Not available yet: arrives in chore/M0-infra-compose"

# Stop local infrastructure (arrives in chore/M0-infra-compose)
down:
    @echo "Not available yet: arrives in chore/M0-infra-compose"

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

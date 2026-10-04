# backend/ml: GharBasket ML and optimisation service (M10)

A separate FastAPI service that only the API calls (through `intel_gateway.api`). It starts with
the CP-SAT weekly meal planner from spike S-4 (`docs/spikes/S-4-meal-plan.md`). The contract is
M10 §3.1.

| Command | What |
|---|---|
| `just dev-ml` | Run on http://localhost:8100 (docs at `/docs`) |
| `uv run --directory backend/ml pytest --cov` | Tests with the coverage gate (90%) |

Settings: `ML_SERVICE_TOKEN`, the shared secret the API sends as `Authorization: Bearer`. It is
required outside local development. Hosting is decided before P3/P4 (ADR-0020); until then, the
API's rule-based planner answers.

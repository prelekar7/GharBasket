# services

Python services (Python 3.13, uv workspace). Owner: Prathamesh Relekar (platform), module owners per `docs/modules/`.

| Folder | What | Arrives in |
|---|---|---|
| `api/` | FastAPI modular monolith: platform + 11 modules, each owning one Postgres schema. Also runs the Taskiq workers | `feat/M0-api-platform` |
| `ml/` | ML and optimisation service: planners, forecasts, routing, assignment, LLM assistant; MLflow models | Sprint 0 spikes, then S7 |

Rules: modules talk only through `api.py` facades or outbox events (import-linter enforces this). See `docs/handbook/04-system-architecture.md`.

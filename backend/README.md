# backend

The Python services (Python 3.13, one uv workspace). Owner: Prathamesh Relekar.

| Folder | What |
|---|---|
| `api/` | FastAPI modular monolith: `platform/` (settings, errors, logging, database) plus one folder per module (M1–M13), each owning one Postgres schema. Also relays outbox events and serves the internal job endpoints that a GitHub Actions schedule calls (no broker or Taskiq workers until the pilot, ADR-0021) |
| `ml/` | ML and optimisation service (M10), a separate FastAPI app that only the API calls (through `intel_gateway`, with a timeout and a rule-based fallback). It starts with the CP-SAT weekly meal planner (`POST /plan/week`, from spike S-4); forecasts, routing and kitchen assignment follow. `just dev-ml` runs it on port 8100 |

Modules talk to each other only through their `api.py` facade or outbox events; import-linter enforces this in CI.
See `docs/handbook/04-system-architecture.md` and the module specs in `docs/modules/`.

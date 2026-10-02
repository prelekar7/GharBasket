# backend

The Python services (Python 3.13, one uv workspace). Owner: Prathamesh Relekar.

| Folder | What |
|---|---|
| `api/` | FastAPI modular monolith: `platform/` (settings, errors, logging, database) plus one folder per module (M1–M13), each owning one Postgres schema. Also runs the Taskiq workers |
| `ml/` | ML and optimisation service: meal planner, forecasts, routing, kitchen assignment, assistant. Arrives with the first ML spike |

Modules talk to each other only through their `api.py` facade or outbox events; import-linter enforces this in CI.
See `docs/handbook/04-system-architecture.md` and the module specs in `docs/modules/`.

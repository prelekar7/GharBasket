# GharBasket (घरBasket)

A **Kitchen Autopilot** for Indian families: AI plans the family's food week, women "Society Captains" in the same
housing society run the last 50 metres, and the monthly vegetable bill stays predictable.
B.Tech major project, SIES Graduate School of Technology (2026–27), guide Prof. Kranti Bade.

## Where things live

| Folder | What | Owner |
|---|---|---|
| `frontend/mobile` | Customer and field app: React + Vite → PWA and Android (Capacitor) | Yash Salunkhe |
| `frontend/console` | Admin, hub and franchise web console (Next.js) | Yash Salunkhe |
| `frontend/packages` | Shared UI kit, generated API client, shared constants | Yash Salunkhe |
| `backend/api` | FastAPI modular monolith: platform + one folder per module (M1–M13) | Prathamesh Relekar |
| `backend/ml` | ML and optimisation service (meal planner, forecasts, routing) | Prathamesh Relekar |
| `infra/` | Local Docker stack: Postgres 18 (PostGIS, pgvector), Valkey, S3 storage, MLflow | Prathamesh Relekar |
| `data/` | Simulator and seed data (synthetic households) | Prathamesh Relekar |
| `e2e/` | End-to-end tests | Rhythm Thakur, Manish Raje |
| `docs/` | Handbook, technical design, ADRs, module specs | everyone |
| `spikes/` | Time-boxed experiments, each with a short notes file | as assigned |

## Quick start

```bash
just install        # JS and Python dependencies, git hooks
just up             # Postgres + Valkey in Docker
cp .env.example .env
just migrate        # create the database schemas
just dev            # API on http://localhost:8000 (docs at /v1/docs)
```

Run `just` to see every command. Full setup: [`docs/SETUP.md`](docs/SETUP.md). Start reading at
[`docs/handbook/00-README.md`](docs/handbook/00-README.md); AI assistants follow [`AGENTS.md`](AGENTS.md).

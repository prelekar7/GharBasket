# gharbasket-api

The GharBasket backend: a FastAPI modular monolith (see `docs/handbook/04-system-architecture.md`).

| Command (from the repo root) | What it does |
|---|---|
| `just up` | Start local Postgres 18 (PostGIS, pgvector) and Valkey in Docker |
| `just migrate` | Apply all migrations (`just migrate-down` undoes the last one) |
| `just migration "orders add slots"` | Generate a migration from model changes; review it before committing |
| `just dev` | Run the API with auto-reload on http://localhost:8000 (docs at `/v1/docs`) |
| `just test` | Run the tests; database tests skip when `just up` is not running |
| `just openapi` | Regenerate `services/api/openapi.json` (the contract the frontend client is built from) |

Configuration comes from environment variables (via Infisical, or a local `.env` copied from `.env.example`);
see `src/gharbasket/platform/settings.py`. Every endpoint lives under `/v1`. Errors use RFC 9457
`application/problem+json` with a namespaced `code`.

## Database
- One Postgres schema per module (`identity`, `orders`, `billing`, …); tables subclass `gharbasket.platform.db.Base`.
- Requests get a session through `DbSession`; services call `await session.commit()` themselves.
- The app uses `DATABASE_URL` (on Neon: the pooled URL); migrations use `DATABASE_URL_DIRECT`.
- Migrations live in `migrations/versions/`, named `YYYYMMDD_HHMM_<module>_<description>.py`; keep one head.

## Health
- `GET /v1/health`: liveness, never touches the database (Render's health check; keeps Neon idle).
- `GET /v1/ready`: checks the database and Valkey; 503 `platform.not_ready` if one is down.

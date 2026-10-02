# gharbasket-api

The GharBasket backend: a FastAPI modular monolith (see `docs/handbook/04-system-architecture.md`).

| Command (from the repo root) | What it does |
|---|---|
| `just up` | Start local Postgres 18 (PostGIS, pgvector) and Valkey in Docker |
| `just migrate` | Apply all migrations (`just migrate-down` undoes the last one) |
| `just migration "orders add slots"` | Generate a migration from model changes; review it before committing |
| `just dev` | Run the API with auto-reload on http://localhost:8000 (docs at `/v1/docs`) |
| `just test` | Run the tests with the coverage gate (85%); database tests skip when `just up` is not running |
| `just openapi` | Regenerate `backend/api/openapi.json` (the contract the frontend client is built from) |

Configuration comes from environment variables (via Infisical, or a local `.env` copied from `.env.example`);
see `src/gharbasket/platform/settings.py`. Every endpoint lives under `/v1`. Errors use RFC 9457
`application/problem+json` with a namespaced `code`.

## Database
- One Postgres schema per module (`identity`, `orders`, `billing`, …); tables subclass `gharbasket.platform.db.Base`.
- Requests get a session through `DbSession`; services call `await session.commit()` themselves.
- The app uses `DATABASE_URL` (on Neon: the pooled URL); migrations use `DATABASE_URL_DIRECT`.
- Migrations live in `migrations/versions/`, named `YYYYMMDD_HHMM_<module>_<description>.py`; keep one head.
- Events: `await publish(session, "orders.locked", {...})` (`platform/outbox.py`) in the same transaction
  as the change; the relay delivers them later (at least once; consumers dedupe on the event id).
- Money and order POSTs take `idem: Idempotent` and call `await idem.save(status, body)` before
  committing (`platform/idempotency.py`); retries replay the stored answer.
- Admin and money actions call `await audit.record(...)` (`platform/audit.py`).
- Lists use cursor pagination: `Page[T]`, `Limit`, `Cursor`, `encode_cursor()` (`platform/pagination.py`).

## Sign-in
- `POST /v1/auth/exchange` with a Firebase ID token (phone OTP) creates the account on first use and
  returns a 15-minute access token and a single-use refresh token. Locally (`APP_ENV=dev`) you can
  sign in without OTP: `{"dev_phone": "+919800000001", "device": {"platform": "web"}}`.
- Protected endpoints take `principal: CurrentPrincipal` (`platform/auth.py`).

## Health
- `GET /v1/health`: liveness, never touches the database (Render's health check; keeps Neon idle).
- `GET /v1/ready`: checks the database and Valkey; 503 `platform.not_ready` if one is down. On staging
  and prod it needs `Authorization: Bearer $JOBS_TOKEN`, so nobody can poll it to keep Neon awake.

## Safety rails (one Neon database is shared by the whole team)
- Without `APP_ENV` the API assumes `prod`; staging and prod refuse to start without `JOBS_TOKEN`.
- Tests refuse any `TEST_DATABASE_URL` that is not on this machine.
- Migrations refuse a non-local database unless `GB_ALLOW_REMOTE_MIGRATIONS=1` (set only by the deploy
  workflow after merge), and downgrades on a shared database are always refused.
- Logs mask phone numbers, tokens and database passwords, including inside exception messages.

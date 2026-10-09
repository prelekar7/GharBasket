# gharbasket-api

The GharBasket backend: a FastAPI modular monolith (see `docs/handbook/04-system-architecture.md`).

| Command (from the repo root) | What it does |
|---|---|
| `just up` | Start local Postgres 18 (PostGIS, pgvector) and Valkey in Docker |
| `just migrate` | Apply all migrations (`just migrate-down` undoes the last one) |
| `just migration "orders add slots"` | Generate a migration from model changes; review it before committing |
| `just seed` | Fill the local database with synthetic people (one per role), societies, households and the catalog (see "Seed data" below) |
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
- Editable resources use optimistic locking: reads `set_etag(response, version)`, edits take
  `expected: IfMatch` (428 without it) and answer 412 with a module code when it is stale (`platform/etag.py`).
- Map points: `platform/geo.py` (`point()` to store, `geography()` in SQL).
- Answers in the user's language: take `lang: Lang` (`platform/locale.py`) and use
  `localised(names, lang)` for `{en, hi, mr}` names; it also sets `Content-Language` and `Vary`.

## Sign-in
- `POST /v1/auth/exchange` with a Firebase ID token (phone OTP) creates the account on first use and
  returns a 15-minute access token and a single-use refresh token. Locally (`APP_ENV=dev`) you can
  sign in without OTP: `{"dev_phone": "+919800000001", "device": {"platform": "web"}}`.
- Browsers send `X-GB-Client: web` and get httpOnly cookies instead of tokens (`platform/web_session.py`);
  cookie requests that change something must carry that header and come from `WEB_ORIGINS`.
- Sign-out is instant (revoked sessions are listed in Valkey); sign-in is rate limited.
- Rate limits: take `valkey: Valkey` (`platform/valkey.py`) and call `await rate_limit.enforce(valkey,
  name, limit=…, window_s=…, code="<module>.<what>_rate_limited")`; the request is allowed if Valkey is down.
- Protected endpoints take `principal: CurrentPrincipal` (`platform/auth.py`).
- Staff endpoints take `permit: Annotated[Permit, require("orders:read")]` and call `permit.ensure(hub_id)`
  for hub/society-scoped data; modules register who may do what in their `permissions.py`
  (`platform/permissions.py`). `X-Active-Role` (optional) narrows the check to one of the caller's roles.
- The first admin: sign in once, then `just grant-role +91XXXXXXXXXX admin` (add `--shared` and run via
  `infisical run --env=staging --` for staging). Later admins use `POST /v1/admin/user-roles`.

## Seed data
`just seed` (`tools/seed.py`) writes synthetic people, societies and households through the module
services, marked `data_source='synthetic'` (ADR-0008). It is local-only and safe to run again. Later
module PRs add their rows (catalog) to the same command. For the catalog, `just seed` only adds
rows that are missing and never overwrites a product or recipe edited in the console (M2 §3.3);
`just seed --refresh` re-applies the seed files to the rows nobody has edited. Sign in as anyone below with `dev_phone`:

| Phone | Name | Roles (scope) |
|---|---|---|
| +919800001001 | Priya Sharma | customer |
| +919800001002 | Sunita Patil | customer (Marathi) |
| +919800001003 | Ramesh Kulkarni | customer (Marathi, senior mode) |
| +919800001004 | Meena Jadhav | captain (Sunrise Towers) |
| +919800001005 | Lata Shinde | kitchen_partner (Kharghar hub) |
| +919800001006 | Arjun Pawar | rider (Kharghar hub) |
| +919800001007 | Kavita More | packer (Kharghar hub) |
| +919800001008 | Vikram Desai | hub_manager (Kharghar hub) |
| +919800001009 | Sanjay Mehta | franchise_owner (Kharghar hub) |
| +919800001010 | Asha Fernandes | ngo_coordinator |
| +919800001011 | Neha Iyer | admin |
| +919800001013 | Rekha Joshi | catalog_editor (recipes: names, fasting and diabetic flags) |

Everyone is also a customer. The hub, society and NGO ids are fixed in `tools/seed.py`. Until M6
creates hubs (and P5 creates NGOs), those scoped roles point at ids that don't exist yet.

Societies (made-up names in real sectors, approximate square boundaries, all served by the Kharghar hub):

| Slug | Area | Status | Towers |
|---|---|---|---|
| sunrise-towers-kharghar | Sector 20, Kharghar | live (threshold 20) | A, B, C wing |
| green-valley-heights-kharghar | Sector 35, Kharghar | unlocking (25) | A, B wing |
| shree-ganesh-chs-kharghar | Sector 12, Kharghar | unlocking (15) | Main building |
| panchavati-residency-kamothe | Sector 7, Kamothe | prospect (20) | A–D wing |
| krishna-kunj-new-panvel | Sector 17, New Panvel | live (20) | A, B wing |
| sai-darshan-apartments-panvel | Old Panvel | prospect, no boundary or hub yet | — |

Try `GET /v1/societies?q=kharghar&near=19.0430,73.0720` after signing in.

Households (onboarded through the same service as `POST /v1/households`; `GET /v1/households/me` as them):

| Phone | Household |
|---|---|
| +919800001001 | Priya: 4 members, veg, Sunrise Towers A-1203 (with pin), ₹6,000 Budget Lock |
| +919800001002 | Sunita: 2 members, egg, Green Valley Heights B-504 (no pin), no Budget Lock |
| +919800001003 | Ramesh: senior couple, veg, Krishna Kunj A-101, health consent with diabetes, low_oil, high_bp |
| +919800001004 | Meena (captain): Jain, 3 members, Sunrise Towers B-402 |

Every other seed person can try onboarding from scratch.

Catalog: 11 categories and 68 everyday products from `data/seeds/catalog/*.json`, with English,
Hindi and Marathi names and the spellings people type. Try `GET /v1/search?q=bhendi` or
`?q=भिंडी` with `Accept-Language: mr`. Prices arrive with the price-list PR.

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

# GharBasket — Technical Design Document (TDD)

| | |
|---|---|
| **Project** | GharBasket (घरBasket) — AI-driven predictive household food platform |
| **Institution** | SIES Graduate School of Technology, Nerul — Department of Computer Engineering |
| **Guide** | Prof. Kranti Bade |
| **Team** | Yash Salunkhe · Prathamesh Relekar · Rhythm Thakur · Manish Raje |
| **Version** | 1.1 (approved baseline 2 Oct 2026; updated 4 Oct 2026 for ADR-0018 to ADR-0024) |
| **Change process** | Any change to a decision in this document requires an ADR in `docs/adr/` |
| **Related documents** | `docs/handbook/` (chapters + all diagrams) · `docs/FRONTEND_DESIGN.md` · `docs/modules/` (module specs) · `docs/adr/` |

Libraries marked ✓ were verified against current documentation (Context7) on 2 Oct 2026. All other libraries are pinned to the latest stable version on setup day.

---

## Table of contents

1. [Context and decisions](#1-context-and-decisions)
2. [System architecture](#2-system-architecture)
3. [Technology stack](#3-technology-stack)
4. [Cross-cutting design rules](#4-cross-cutting-design-rules)
5. [Data architecture](#5-data-architecture)
6. [AI / ML platform](#6-ai--ml-platform)
7. [Core workflows](#7-core-workflows)
8. [Module overview](#8-module-overview)
9. [Frontend architecture](#9-frontend-architecture)
10. [Engineering workflow](#10-engineering-workflow)
11. [Testing and quality](#11-testing-and-quality)
12. [Security, privacy and compliance](#12-security-privacy-and-compliance)
13. [Observability and operations](#13-observability-and-operations)
14. [Phases, milestones and gates](#14-phases-milestones-and-gates)
15. [Budget](#15-budget)
16. [Risk register](#16-risk-register)
17. [Architecture style decision: service-based, not microservices](#17-architecture-style-decision-service-based-not-microservices)
18. [Final technology stack summary](#18-final-technology-stack-summary)
19. [Open items](#19-open-items)

Diagrams for every section (C4, UML, ERDs, sequences, state machines, workflows, Gantt) are in `docs/handbook/`; see `docs/handbook/appendix-diagram-index.md`.

---

## 1. Context and decisions

GharBasket is a 4-person B.Tech major project (accepted 13 Aug 2026), built collaboratively on GitHub, module by module.

**Product.** A *Kitchen Autopilot* for Indian families. AI plans each family's food week: meals first, then the vegetables, cut-vegetable kits and tiffins those meals need. Women in the same housing society — **Society Captains** and home-kitchen partners — prepare and deliver. Delivery runs on fixed, scheduled society routes with zero-waste, order-before-we-buy procurement.

### 1.1 Decision register

| # | Decision |
|---|---|
| D1 | Headline USPs: **Kitchen Autopilot** (meal-plan-first AI) and **Society Captain ("Ghar Saathi")**. Supporting: Budget Lock (fixed monthly sabzi bill), zero-waste loop, Kindness Wallet. |
| D2 | One React frontend codebase, written in JavaScript (D18). `frontend/mobile` = Vite + React SPA → PWA + Capacitor 8, a single app for **field roles** (customer, captain, kitchen partner, rider, packer). `frontend/console` = Next.js 16 (admin, hub, franchise, NGO) plus the public site, web only. Replaces Flutter from the original proposal (guide to be informed). |
| D3 | Backend: Python only. FastAPI modular monolith + a separate ML/optimisation service + background jobs (no broker until the pilot, D21; Taskiq workers at the pilot). |
| D4 | Payments: closed-loop prepaid wallet (default) and UPI Autopay (mandates built in the pilot phase; MS-A uses the wallet with Razorpay test-mode top-ups). No customer KYC (closed-system PPI). KYC applies to everyone the platform pays (partners, captains, riders, franchisees). |
| D5 | Assistant: text MVP using an LLM with tool calls; voice is a stretch goal. |
| D6 | Database: PostgreSQL 18-centric (PostGIS, pgvector, pg_trgm, JSONB, RLS), Valkey, S3-compatible object storage, DuckDB. No MongoDB/Firebase DB/Elasticsearch. |
| D7 | Research: one systems paper covering four algorithms — CP-SAT meal planner and pantry digital twin (deepest), fair kitchen assignment, forecast + newsvendor procurement. |
| D8 | Data: simulator first; real data added as it arrives. Every household and order carries `data_source ∈ {synthetic, pilot, live}`. |
| D9 | Team (code ownership as updated by D23): **Prathamesh Relekar** — all of `backend/` (M0–M13), infra, simulator, ML and research results, CI/CD. **Yash Salunkhe** — all of `frontend/` (mobile app for every role, console UI, design system) and design in Claude Design. **Rhythm Thakur and Manish Raje** — documentation, report, paper writing, UML, testing/QA, pilot operations, presentation. |
| D10 | Timeline: plan for April 2027 (12 two-week sprints) with one floating buffer sprint, moved once exam and deadline dates are known. |
| D11 | Hosting: free tiers until the pilot — **Render, Vercel, Neon, Cloudflare R2, Infisical** (D20; replaces the earlier AWS Mumbai plan). |
| D12 | Platforms: **Android (Capacitor) + PWA**. iPhone users use the PWA; no iOS build in v1. |
| D13 | Assistant model chosen after the Sprint 8 evaluation (Claude Opus 5.5 vs Sonnet 5.5 vs Haiku 4.5 on accuracy vs cost). |
| D14 | Technology stack (Section 18) confirmed, including the service-based architecture (not microservices). |
| D15 | Database host: **Neon** (Singapore), one shared database for dev and staging, no branch per pull request (D20); pilot database placement decided before P6 (Section 19). Files on **Cloudflare R2** through the S3 API (D20 replaces the earlier EC2 + Amazon S3 plan). |
| D16 | UI design: **Claude Design only** — a GharBasket Design System artifact plus Design canvases (Customer, Field roles, Console). |
| D17 | Design timing: design system and customer core screens in Phase 0, before code; other screens at the start of their phases (spec-first). |
| D18 | Frontend language: **JavaScript** (`.js`/`.jsx`) with JSDoc, `jsconfig.json` `checkJs` and the generated `schema.d.ts` as editor hints; no `tsc` gate (ADR-0018). |
| D19 | Repository layout: `frontend/` and `backend/`, one folder per backend module (ADR-0019). |
| D20 | Hosting on free tiers: Render (API, Key Value), Vercel (PWA, console), Neon, Cloudflare R2, Infisical, PostHog (ADR-0020; supersedes ADR-0011 and ADR-0015). |
| D21 | Jobs without a broker until the pilot: Postgres outbox relayed after commit + internal job endpoints called by a GitHub Actions schedule (ADR-0021). |
| D22 | Generated API client: openapi-typescript + openapi-fetch + openapi-react-query (ADR-0022). |
| D23 | Ownership: Prathamesh Relekar all backend, infra and ML; Yash Salunkhe all frontend (ADR-0023). |
| D24 | Sign-in: Firebase phone OTP, then our own rotating tokens; httpOnly cookies + CSRF checks for browsers (ADR-0024). |

---

## 2. System architecture

### 2.1 Context

```
  Households ─┐          ┌─ Razorpay (payments, Autopay, Route payouts)
  Captains   ─┤          ├─ Firebase (phone OTP, FCM push)
  Kitchens   ─┼─ Mobile ─┤─ WhatsApp Cloud API (notifications)
  Riders     ─┤   app    ├─ Claude API (assistant, triage)
  Packers    ─┘          ├─ OSRM (self-hosted road distances)
                  ▼      ├─ Map tiles (OpenFreeMap / MapTiler)
            GharBasket   └─ Cloudflare R2 (files, S3 API)
                  ▲
  Admin / Hub manager / Franchisee / NGO ── Console (web)
```

### 2.2 Containers

```
┌──────────────── Clients ────────────────┐
│ frontend/mobile (PWA + Capacitor Android)   │   frontend/console (Next.js 16, SSR)
└──────────────┬──────────────────────────┘          │
               │ HTTPS JSON (OpenAPI) + SSE           │
        ┌──────▼──────────────────────────────────────▼──────┐
        │ Vercel: PWA + console, same-origin /v1/* rewrites;  │
        │ the Capacitor app calls the API directly.           │
        │ TLS by Vercel and Render                            │
        └──────┬──────────────────────────────────────────────┘
   ┌───────────▼───────────┐  httpx2 (timeouts, fallback) ┌────────────────────┐
   │ backend/api          │─────────────────────────────►│ backend/ml        │
   │ FastAPI modular       │                              │ planners, forecast,│
   │ monolith (11 modules) │◄──── results in intel.* ─────│ routing, assignment│
   │ + outbox relay after  │                              │ LLM tools; long    │
   │   each commit         │                              │ batch jobs here or │
   └───┬─────────┬─────▲───┘                              │ offline            │
       │         │     │ POST /v1/internal/jobs/<name>    └───┬───────────┬────┘
       │         │   ┌─┴───────────────────────────┐          │           │
       │         │   │ GitHub Actions schedule     │          │           │
       │         │   │ (outbox sweep, cut-off,     │          │           │
       │         │   │  reminders, clean-up)       │          │           │
       │         │   └─────────────────────────────┘          │           │
  ┌────▼─────────▼───┐  ┌────────┐  ┌───────────┐         ┌─────▼────┐  ┌───▼──────┐
  │ PostgreSQL 18    │  │ Valkey │  │ R2 /      │         │ MLflow   │  │ OSRM     │
  │ (Neon / Docker)  │  │        │  │ SeaweedFS │         │ tracking │  │ (MMR     │
  │ 1 schema/module  │  │        │  │ (S3 API)  │         │ registry │  │ extract) │
  └──────────────────┘  └────────┘  └───────────┘         └──────────┘  └──────────┘
```

Staging runs on free tiers (ADR-0020): the API is a Render free web service (Singapore) with Render Free Key Value for Valkey; Postgres is on Neon (Singapore); files are on Cloudflare R2; the PWA and console are on Vercel. Hosting for the ML service, MLflow and OSRM is to be decided before P3/P4; locally everything runs under Docker Compose.

### 2.3 Architecture principles

1. **Modular monolith first.** One deployable API with hard internal boundaries. Modules talk only through `api.py` facades and events, so any module can be extracted later.
2. **ML never blocks a customer.** Heavy AI runs in batch and writes results into `intel.*` tables. Online ML calls use timeouts and rule-based fallbacks (for example, if the ML service is down, Autopilot falls back to last week's box adjusted by family-size rules).
3. **Solvers decide, statistics predict, LLMs talk.** The LLM never computes money or quantities.
4. **Every write is idempotent; every money movement is a ledger entry.**
5. **Contract-first.** OpenAPI is the boundary between teammates. API clients are generated, never hand-written.
6. **Boring technology, sharp product.** Well-documented libraries; the innovation budget goes to algorithms and UX.

---

## 3. Technology stack

### 3.1 Toolchain and monorepo

| Concern | Choice | Rationale / rejected alternatives |
|---|---|---|
| JS runtime | Node 24 LTS | Current LTS |
| JS package manager | pnpm 10 workspaces | Fast, strict; npm/yarn rejected |
| Task runner | Turborepo | Cached builds; Nx rejected as heavier |
| JS lint + format | Biome | One fast tool; ESLint + Prettier rejected (two tools; Next 16 dropped `next lint`) |
| Python | 3.13 | Mature wheels for OR-Tools, LightGBM, torch; 3.14 rejected (ecosystem lag) |
| Python env/deps | uv workspace (api, ml, simulator) | Fast, lockfile; Poetry rejected |
| Python lint/types | Ruff + pyright | |
| Git hooks | lefthook (Biome, Ruff, gitleaks on staged files) | pre-commit rejected (slower) |
| Commands | `just` (`just up`, `just dev`, `just test`, `just gen-api`, `just migrate`, `just sim`, `just eval`) | One command vocabulary for the team |
| Dependency updates | Renovate (weekly, grouped) | |

### 3.2 Mobile app (`frontend/mobile`)

| Concern | Library | Notes |
|---|---|---|
| Build | Vite + `@vitejs/plugin-react` ✓ | SPA output for Capacitor |
| UI runtime | React 19 + JavaScript (`.js`/`.jsx`) | JSDoc + `jsconfig.json` `checkJs` and the generated `schema.d.ts` as editor hints, not a gate (ADR-0018) |
| Routing | TanStack Router ✓ | File-based, type-safe search params, route guards; role shells lazy-loaded |
| Server state | TanStack Query + openapi-react-query generated hooks | ADR-0022 |
| Client state | Zustand | Active role, cart draft, UI preferences |
| Forms | react-hook-form + zod | |
| Styling | Tailwind CSS v4 + `frontend/packages/ui` tokens | |
| Primitives | React Aria Components ✓ | Accessibility and i18n (Indian date/number formats) |
| Motion | Motion ✓, NumberFlow ✓, Rive ✓ (three brand moments) | |
| i18n | i18next + react-i18next + ICU; `en-IN`, `hi-IN`, `mr-IN` | |
| Native shell | Capacitor 8 ✓ (Android) | No iOS build in v1 (D12) |
| Native plugins | push-notifications ✓, camera, geolocation, network, preferences, app, haptics, splash-screen, status-bar, keyboard; Capawesome barcode-scanner ✓ and background-geolocation ✓; `@capacitor-firebase/authentication`; secure storage for the refresh token | Each wrapped in `src/shared/native/*` with a web fallback so the PWA works |
| Offline | Dexie (IndexedDB) outbox + cached route manifest | Not service-worker Background Sync (unreliable in Capacitor WebViews); flush on network-online and app-resume with idempotency keys |
| PWA | vite-plugin-pwa ✓ (`injectManifest`) | Browser installs |
| Maps | MapLibre GL JS + OpenFreeMap/MapTiler tiles | Google Maps JS rejected (cost, key exposure) |
| Analytics | PostHog | No names or phone numbers in events; Sentry is not used |
| Tests | Vitest, Testing Library, Playwright (mobile viewport) | |

### 3.3 Console (`frontend/console`)

| Concern | Library |
|---|---|
| Framework | Next.js 16.2.x ✓ (App Router, `proxy.js` auth gate ✓, `cacheComponents` ✓, React 19 ✓), JavaScript (ADR-0018), hosted on Vercel |
| Data | Server Components with the generated openapi-fetch client (session cookie forwarded); client islands use TanStack Query (openapi-react-query) |
| Tables | TanStack Table + TanStack Virtual |
| URL state | nuqs |
| Charts | Apache ECharts (brand theme) |
| Maps | MapLibre + deck.gl + terra-draw (society polygons) |
| Forms / UI | react-hook-form + zod, React Aria Components, Tailwind v4 (shared preset) |
| Tests | Vitest, Playwright |

### 3.4 Shared packages

| Package | Contents |
|---|---|
| `frontend/packages/ui` | Design tokens (CSS variables), Tailwind preset, fonts (Fraunces + Mukta), icons, base and signature components; Storybook 10 |
| `frontend/packages/api-client` | openapi-typescript (`schema.d.ts`, types only) + openapi-fetch (runtime client) + openapi-react-query (TanStack Query hooks) — generated from `backend/api/openapi.json` by `just gen-api`, never hand-edited; CI fails on drift (ADR-0022) |
| `frontend/packages/domain` | Enums/constants not in OpenAPI (role keys, feature-flag keys, i18n key types) |
| `frontend/packages/config` | Shared `jsconfig` and Biome config |

### 3.5 Backend API (`backend/api`)

| Concern | Library |
|---|---|
| Web | FastAPI ✓, Pydantic v2, pydantic-settings, uvicorn[standard] |
| Database | SQLAlchemy 2.1 async + asyncpg, Alembic, pgvector-python (with embeddings) |
| Jobs | Postgres outbox relayed after each commit + internal job endpoints called by a GitHub Actions schedule (ADR-0021); Taskiq + Valkey return at the pilot |
| Auth | PyJWT[crypto]: verifies Firebase ID tokens against Google's public keys (not firebase-admin) and signs our own access tokens (ADR-0024); phonenumbers |
| Payments | Our own thin async Razorpay client on httpx2, not the official SDK (planned with the wallet PRs) |
| Outbound HTTP | httpx2 + tenacity + simple circuit breaker |
| Realtime | sse-starlette + Valkey pub/sub (redis client) |
| State machines | python-statemachine |
| Templates | Jinja2 (notifications per locale) |
| Search helpers | pg_trgm + unaccent, indic-transliteration |
| Geo | GeoAlchemy2 (PostGIS geography), h3 (res-9 cells), shapely |
| Observability | structlog, OpenTelemetry |
| Rate limiting | Own fixed-window counters in Valkey (requests allowed if Valkey is down; ADR-0024) |
| Boundaries | import-linter ✓ independence contracts |
| Tests | pytest + anyio, pytest-cov (85 % gate), httpx2 mock transports for outbound calls (not respx); schemathesis fuzzing of every endpoint from `openapi.json`; hypothesis, time-machine and polyfactory added with their features |

### 3.6 ML and optimisation service (`backend/ml`)

| Concern | Library |
|---|---|
| Serving | FastAPI; long jobs run in the service or offline until a paid worker exists (Taskiq queue `ml` at the pilot, ADR-0021) |
| Data | Polars, DuckDB (reads Postgres via its `postgres` extension; writes Parquet to S3-API object storage) |
| Classical ML | scikit-learn, LightGBM (LambdaRank, churn), SHAP |
| Bayesian | NumPyro (CPU); SciPy closed-form Gamma–Poisson online updates |
| Forecasting | StatsForecast ✓ (AutoETS, CrostonOptimized/TSB/ADIDA, conformal intervals ✓), MLForecast (LightGBM), HierarchicalForecast (MinT), Chronos-2 ✓ (CPU, covariates, quantiles) |
| Optimisation | OR-Tools ✓ — routing (CVRPTW + pickup-and-delivery ✓) and CP-SAT |
| Embeddings | sentence-transformers with BAAI bge-m3 (multilingual), batch on CPU → pgvector (validate in S7 spike) |
| LLM | anthropic Python SDK |
| Experiment tracking | MLflow 3 ✓ (registry alias `@champion` ✓), artifacts in S3-API object storage (host to be decided before P4) |
| Hyperparameters | Optuna |
| Data validation | pandera |
| Notebooks | marimo ✓ (stored as `.py`) |
| Tests | pytest, solver golden tests, model-quality thresholds |

### 3.7 Data and infrastructure

| Concern | Choice |
|---|---|
| OLTP database | PostgreSQL 18 + PostGIS + pgvector + pg_trgm + unaccent + pgcrypto (custom image `infra/postgres/Dockerfile`) |
| Hosted Postgres | Neon (Singapore): one shared database for the Infisical `dev` and `staging` environments, no branch per PR. Migrations reach it only from the deploy workflow after merge (`GB_ALLOW_REMOTE_MIGRATIONS=1`); remote downgrades are refused; tests and `just seed` refuse non-local databases; synthetic rows carry `data_source='synthetic'`. Daily work uses local Docker Postgres. |
| Cache | Valkey 8 locally; Render Free Key Value on staging (in-memory, may lose data on restart; holds only rate-limit counters and the sign-out deny-list; the API works without it) |
| Jobs | Postgres outbox + GitHub Actions schedule calling `POST /v1/internal/jobs/<name>` (ADR-0021) |
| Files and data lake | Cloudflare R2 (S3 API, region `auto`, `gb-staging-*` buckets); SeaweedFS locally. Code uses the S3 API only. Data lake and MLflow artifact storage are decided with the ML hosting before P3/P4 |
| Road network | OSRM with OpenStreetMap clipped to the Mumbai Metropolitan Region (osmium) |
| API hosting | Render free web service (Singapore); Render builds the Docker image from the repo; UptimeRobot calls `/v1/health` every 10 minutes to keep it awake (750 free hours a month cover 24×7) |
| ML service, MLflow, OSRM hosting | ML service + OSRM on a Hugging Face Space (free CPU, Docker); training, forecasts and snapshots in GitHub Actions (`backend/training`); R2 as lake and model registry; MLflow local only (ADR-0025). Locally they run in Docker Compose |
| TLS | By Render and Vercel (no reverse proxy of our own) |
| Secrets | Infisical (`dev`, `staging`, `prod`), synced to GitHub Actions secrets; `.env.example` committed; gitleaks in CI |
| Backups | Neon point-in-time restore; a scheduled `pg_dump` to R2 can be added via the jobs workflow |
| Domains | No custom domain yet (Render and Vercel default URLs) |
| Web hosting | Vercel for the PWA and console, with same-origin `/v1/*` rewrites to the API; a preview deployment per PR |
| Mobile distribution | Debug APK per PR (CI artifact); signed AAB → Play Console internal testing / Firebase App Distribution |
| Monitoring | UptimeRobot, Grafana Cloud (OpenTelemetry), PostHog (no names or phone numbers); Sentry is not used |

Free tiers only until the pilot (P6). Before P6 we decide on paid plans (Render Starter, a worker, Firebase Blaze for real OTPs) and on where the pilot database lives (Section 19). See ADR-0020.

---

## 4. Cross-cutting design rules

### 4.1 API conventions
- REST under `/v1`; OpenAPI tags = module names; plural resource nouns.
- Errors: RFC 9457 `application/problem+json` with namespaced `code` (e.g. `orders.cutoff_passed`, `billing.insufficient_balance`).
- Cursor pagination (`?cursor=&limit=` → `next_cursor`).
- `Idempotency-Key` header required on every POST that creates money movements or orders; stored in `platform.idempotency_keys` for 24 h.
- Optimistic locking: `version` column + `If-Match` on boxes, plans, prices.
- Headers: `Authorization: Bearer <access>` (apps) or `X-GB-Client: web` with httpOnly cookies (browsers, ADR-0024), `X-Active-Role: <role>:<scope_id>`, `Accept-Language`.
- Money in JSON as integer paise (`amount_paise`). Time as ISO-8601 with offset; business dates in Asia/Kolkata.
- Inbound webhooks (Razorpay, WhatsApp): signature verified → stored raw → processed idempotently by a job.

### 4.2 Identity, authentication and permissions
1. Firebase Phone Auth OTP in the app → Firebase ID token.
2. `POST /v1/auth/exchange` → the API verifies the Firebase ID token itself with PyJWT against Google's public keys (not firebase-admin). The first exchange for a number creates the account with the `customer` role (that is signup). It issues an access JWT (HS256 with a `kid`, 15 min) and an opaque refresh token, stored only as an HMAC-SHA256 with a server pepper, in a session of at most 30 days. Refresh tokens rotate on every use, with a 2-minute grace for lost responses; any other reuse revokes the session (`auth.refresh_reused`). Locally only (`APP_ENV=dev`), `dev_phone` signs in without OTP.
3. Apps (Capacitor): `Authorization: Bearer`, refresh token in secure storage. Browsers (PWA, console): send `X-GB-Client: web`; the API sets httpOnly cookies `__Host-gb_at` (Path=/, 15 min) and `gb_rt` (Path=/v1/auth), same-origin through Vercel's `/v1/*` rewrites. CSRF: a cookie-authenticated POST/PATCH/PUT/DELETE without `X-GB-Client: web`, or from an Origin outside `WEB_ORIGINS`, gets 403 `auth.csrf_rejected`. Sign-out is instant through a Valkey deny-list; a refresh always checks the session in Postgres. Rate limits are fixed windows in Valkey (ADR-0024).
4. Roles with scopes in `identity.user_roles(user_id, role, scope_type, scope_id, status)`: customer, captain (society), kitchen_partner (hub), rider (hub), packer (hub), hub_manager (hub), franchise_owner (hub), ngo_coordinator (ngo), admin (global). Status `applied → kyc_pending → verified → suspended`.
5. Permission matrix in code, enforced by a FastAPI dependency, e.g. `require("orders:read", scope=hub_id)`. Role switching in the app is UX only.
6. Defence in depth: Postgres RLS on hub-scoped tables; `SET LOCAL app.hub_ids` per transaction.

### 4.3 Events and background jobs
- **Transactional outbox:** domain change + `platform.outbox` row in one transaction; after each commit the API relays fresh rows itself as a background task, reading with `FOR UPDATE SKIP LOCKED`; a scheduled `outbox-sweep` job picks up anything left; consumers deduplicate on `event_id` (at-least-once). See ADR-0021.
- **Scheduled jobs (no broker until the pilot, ADR-0021):** each job is an internal endpoint `POST /v1/internal/jobs/<name>`, protected by `JOBS_TOKEN` and called by a GitHub Actions scheduled workflow. Jobs are idempotent and take a Postgres advisory lock. Business rules use the scheduled time, not the run time: the API itself refuses box edits after 21:00 IST, and the cut-off job does the follow-up work. Long ML jobs (plans, forecasts, training) run in the ML service or offline. Taskiq workers return at the pilot, when a paid worker exists.
- **Event catalog v1:** `identity.household_created`, `subscriptions.cycle_generated`, `subscriptions.box_approved`, `subscriptions.box_skipped`, `orders.locked`, `orders.shortage_substituted`, `orders.packed`, `orders.dispatched`, `orders.at_society`, `orders.delivered`, `orders.delivery_failed`, `billing.wallet_topped_up`, `billing.payment_failed`, `billing.refund_issued`, `hub_ops.procurement_plan_ready`, `hub_ops.waste_logged`, `logistics.route_published`, `logistics.pickup_completed`, `partners.verified`, `partners.assignment_created`, `community.donation_verified`, `intel.plans_ready`, `intel.forecast_ready`.
- **Job schedule (IST, per hub; T = delivery day):**

| When | Job | Module |
|---|---|---|
| T-3 18:00 | `generate_weekly_plans` | intel → subscriptions |
| T-3 18:30 | `send_box_ready_and_pre_debit_notices` | notify / billing |
| T-1 19:00 | `remind_unapproved` | notify |
| T-1 21:00 | `cutoff_lock` (advisory lock, idempotent) | orders / billing |
| T-1 21:10 | `forecast_uncommitted` → `build_procurement_plan` | intel → ops |
| T-1 21:30 | `assign_kitchens` | intel → partners |
| T 05:00 | `build_packing_tasks` | ops |
| T 07:00 | `solve_routes` (per slot) | intel → logistics |
| T 12:30 | `surplus_cascade` | ops |
| Daily 02:00 | `export_snapshots`, data-quality checks | intel |
| Sunday 03:00 | `retrain_models` (champion/challenger) | ml |
| Hourly / daily | `reconcile_payments` / `expire_kindness_points` | billing |

### 4.4 Realtime, notifications, files, search
- **Realtime:** SSE `GET /v1/orders/{id}/events` and `/v1/routes/{id}/live`. Rider pings every 15 s → Valkey GEO + pub/sub → SSE; persisted every 60 s to `logistics.gps_pings` (monthly partitions).
- **Notifications:** templates per event × locale × channel (push, WhatsApp, in-app inbox); preferences; quiet hours 22:00–07:00 except delivery; deduplicated per event.
- **Files:** presigned uploads to Cloudflare R2 through the S3 API (`POST /v1/files/presign`; SeaweedFS locally); KYC documents in a private bucket with short-lived signed reads; thumbnails generated by a job.
- **Search:** Postgres FTS (`simple`) + pg_trgm + `catalog.search_terms` synonyms (English/Hindi/Marathi/Hinglish via indic-transliteration, curated by Rhythm and Manish); pgvector HNSW for substitutes and similar recipes.

### 4.5 Internationalisation and accessibility
Locales `en-IN`, `hi-IN`, `mr-IN`; catalog names as JSONB `{en, hi, mr}`. WCAG 2.2 AA, senior mode, 48 px touch targets; target device: a ₹8,000 Android phone (Android 9+, 3 GB RAM).

---

## 5. Data architecture

### 5.1 Global rules
1. `uuidv7()` primary keys (PostgreSQL 18).
2. `created_at` / `updated_at` as `timestamptz`.
3. Soft delete only where legally needed.
4. Money as `BIGINT` paise; quantities `NUMERIC(10,3)` + unit enum (`kg`, `g`, `pc`, `bunch`, `ml`, `l`).
5. Tenant rows carry `hub_id`; household-derived rows carry `data_source`.
6. One schema per module; writes only by the owning module; foreign keys only to stable IDs (users, households, hubs, products).
7. Migrations: one per PR, named `YYYYMMDD_HHMM_<module>_<desc>`, single Alembic head enforced in CI, reversible.

### 5.2 Core tables

```
identity.users(id, phone_e164 UQ, name, locale, status, created_at)
identity.user_roles(id, user_id, role, scope_type, scope_id, status, verified_at)
identity.households(id, owner_user_id, hub_id, society_id, size, diet, cuisine_tags[], budget_paise,
                    autopilot_enabled, auto_approve, data_source)
identity.household_members(id, household_id, age_band, sex, health_tags[], fasting_rules jsonb)
identity.addresses(id, household_id, society_id, tower, flat, location geography(Point), h3_r9)
identity.consents(id, user_id, purpose, version, granted_at, revoked_at)

catalog.products(id, kind, names jsonb, unit, shelf_life_h, nutrition jsonb, tags[], embedding vector(1024), active)
catalog.recipes(id, names jsonb, cuisine, cook_minutes, rules jsonb, steps jsonb, embedding)
catalog.recipe_ingredients(recipe_id, product_id, qty_per_serving, optional)
catalog.substitutions(product_id, substitute_id, score, source)
catalog.price_lists(id, hub_id, valid_from); catalog.prices(price_list_id, product_id, price_paise, mrp_paise)
catalog.festival_calendar(date, region, code, rules jsonb)
catalog.search_terms(product_id, term, lang)

subscriptions.plans(id, code, kind, fee_paise, features jsonb)
subscriptions.subscriptions(id, household_id, plan_id, status, started_at, paused_until, payment_mode)
subscriptions.society_delivery_days(society_id, weekday, slot_id)
subscriptions.budget_locks(id, household_id, month, budget_paise, spent_paise)
subscriptions.weekly_cycles(id, household_id, week_start, plan_ref, status, generated_at, approved_at, version)
subscriptions.box_lines(cycle_id, product_id, qty, unit, reason_code, reason_text jsonb, locked)

orders.orders(id, household_id, hub_id, kind, cycle_id, delivery_date, slot_id, status,
              subtotal_paise, fees_paise, discount_paise, total_paise, version, data_source)
orders.order_lines(order_id, product_id, qty, unit_price_paise, substituted_from, shortage_qty)
orders.order_events(id, order_id, from_status, to_status, actor, payload jsonb, at)
orders.slots(id, hub_id, label, start_time, end_time, capacity)

billing.ledger_accounts(id, owner_type, owner_id, currency, kind)
billing.journal_entries(id, kind, ref_type, ref_id, idempotency_key UQ, created_at)
billing.postings(entry_id, account_id, amount)            -- sum per entry and currency = 0
billing.payments(id, user_id, provider, provider_ref, amount_paise, status, purpose)
billing.mandates(id, user_id, provider_ref, max_paise, status)
billing.payment_webhooks(id, provider, event_id UQ, payload jsonb, processed_at)

hub_ops.hubs(id, name, location geography, service_area geography(Polygon), franchise_owner_id)
hub_ops.vendors(id, hub_id, name, kind); hub_ops.vendor_scores(vendor_id, week, freshness, price, reliability, defects)
hub_ops.procurement_plans(id, hub_id, for_date, status)
hub_ops.procurement_lines(plan_id, product_id, committed_qty, forecast_q50, forecast_qtau, order_qty, vendor_id, unit_cost_paise)
hub_ops.inventory_lots(id, hub_id, product_id, qty, received_at, expires_at, cost_paise, grade)
hub_ops.packing_tasks(id, order_id, packer_id, status, bag_qr, started_at, done_at)
hub_ops.waste_logs(id, hub_id, product_id, qty, reason, cascade_to)

logistics.vehicles(id, hub_id, kind, capacity_kg, capacity_crates)
logistics.routes(id, hub_id, date, slot_id, vehicle_id, rider_id, status, solver_stats jsonb)
logistics.route_stops(route_id, seq, society_id, eta, kind, payload jsonb)
logistics.handovers(id, route_stop_id, captain_id, bags_count, otp_ok, photo_key, at)
logistics.deliveries(order_id, status, proof_kind, proof_key, at)
logistics.reverse_pickups(id, kind, ref_id, route_stop_id, status)
logistics.gps_pings(rider_id, at, location) PARTITION BY RANGE(at)

partners.partners(id, user_id, kind, hub_id, status, rating, fssai_reg_no, bank_ref_masked)
partners.kyc_documents(id, partner_id, doc_type, file_key, status, reviewed_by)
partners.kitchen_capacity(partner_id, date, meal, max_portions, menu_skills[])
partners.assignments(id, partner_id, ref_type, ref_id, date, portions, status)
partners.earnings(id, partner_id, ref_type, ref_id, amount_paise, week)
partners.trainings(partner_id, module_code, completed_at, score)

community.societies(id, hub_id, name, boundary geography(Polygon), towers jsonb, flats, unlock_threshold, status)
community.captains(society_id, partner_id, since)
community.referrals(id, referrer_user_id, referee_user_id, status, reward_entry_id)
community.donation_drives(id, code, starts_on, ends_on, multiplier)
community.donations(id, user_id, items jsonb, status, pickup_id, points_awarded, ngo_handoff_id)
community.ngo_handoffs(id, ngo_id, items jsonb, photo_key, at)

intel.feature_snapshots(household_id, as_of, features jsonb)
intel.predictions(id, kind, subject_id, for_date, payload jsonb, model_name, model_version, created_at)
intel.experiments(id, key, variant, subject_id, assigned_at)

notifications.templates, notifications.messages, notifications.preferences, notifications.devices
platform.outbox(id, event_type, payload jsonb, created_at, sent_at), platform.idempotency_keys, platform.audit_log
```

### 5.3 State machines

**Weekly box / order**
```
DRAFT → AWAITING_APPROVAL → APPROVED | SKIPPED
AWAITING_APPROVAL → APPROVED automatically when auto_approve is on and the pre-debit notice was sent ≥ 24 h earlier
APPROVED → LOCKED (cut-off; wallet debit or Autopay capture) → PICKING → PACKED → DISPATCHED → AT_SOCIETY → DELIVERED
LOCKED → PAYMENT_FAILED (2 h grace) → CANCELLED
PACKED with shortage → substitution (customer preference) or partial refund
AT_SOCIETY → UNDELIVERED → REDELIVERY (next slot) | REFUNDED
Edits before cut-off increment version; cancellation is free before LOCKED.
```
**Tiffin:** `SCHEDULED → LOCKED → ASSIGNED → PREPARING → READY → PICKED_UP → DELIVERED | FAILED (refund)`
**Partner:** `APPLIED → KYC_PENDING → TRAINING → VERIFIED → ACTIVE ⇄ PAUSED → SUSPENDED`
**Donation:** `SCHEDULED → PICKUP_ASSIGNED → PICKED_UP → AT_HUB → VERIFIED (points) | REJECTED → HANDED_TO_NGO`

### 5.4 Ledger postings (paise)

| Event | Debit | Credit |
|---|---|---|
| Wallet top-up ₹1,000 | razorpay_clearing 100000 | wallet:U (liability) 100000 |
| Top-up bonus ₹100 | promo_expense 10000 | wallet:U 10000 |
| Box locked ₹450 | wallet:U 45000 | unearned_revenue 45000 |
| Box delivered | unearned_revenue 45000 | sales_revenue 45000 |
| Captain commission ₹6 | fulfilment_expense 600 | partner_payable:P 600 |
| Weekly payout | partner_payable:P | payout_clearing |
| Shortage refund ₹30 | unearned_revenue 3000 | wallet:U 3000 |
| Kindness +300 points | kindness_expense (PTS) 300 | kindness:U (PTS) 300 |
| Redeem 200 points → ₹40 off | kindness:U 200 PTS; promo_expense 4000 INR | kindness_expense 200 PTS; unearned_revenue 4000 INR |

Invariants (property-tested with hypothesis): each entry sums to zero per currency; a wallet balance is never negative; replaying an `idempotency_key` never double-posts.

### 5.5 Indexing and performance
GiST on geography columns; HNSW on embeddings; GIN trigram on `catalog.search_terms.term`; partial index `orders(hub_id, delivery_date) WHERE status IN ('APPROVED','AWAITING_APPROVAL')`; BRIN on `gps_pings.at`. Cut-off locks about 2,000 orders in batched `UPDATE … RETURNING` within an advisory-locked job, target under 60 s.

### 5.6 Simulator and seed data
`data/simulator` (owner: Prathamesh Relekar; reviewed by Rhythm Thakur and Manish Raje) generates a deterministic world from a seed: hubs and real Panvel/Kharghar societies with drawn polygons; households from cohort distributions (size, diet, region, fasting practice, budget); latent per-item consumption rates (ground truth retained); weekly meal choices and accept/edit behaviour; festivals and price shocks; churn; partners with capacities; donations. It writes through module services with `data_source='synthetic'`; ground truth is saved to `data/sim_truth/*.parquet`.

---

## 6. AI / ML platform

### 6.1 Where models are trained
No GPU is required in v1; the models are statistical, tabular and solver-based.

| Model / engine | Type | Runs where | Frequency | Typical time |
|---|---|---|---|---|
| Pantry digital twin | Hierarchical Bayesian (NumPyro) + closed-form online updates | ML service (CPU; host to be decided before P4); laptops for development | Weekly fit; online update per order/feedback | 1–5 min |
| Next-basket ranker | LightGBM LambdaRank | ML service (batch) | Weekly | < 2 min |
| Demand forecast | StatsForecast + MLForecast + reconciliation | ML service (batch) | Nightly | < 5 min |
| Chronos-2 | Pretrained, zero-shot (no training) | ML service CPU inference | Nightly, new hubs | seconds–minutes |
| Meal planner, routing, kitchen assignment, kit selection | OR-Tools solvers (no training) | ml service | Per cycle / per slot | 1–30 s |
| Churn + uplift | LightGBM + T-learner | ML service (batch) | Monthly | < 1 min |
| Embeddings (bge-m3) | Pretrained (no training) | One-off batch on a laptop or free Colab/Kaggle GPU | On catalog change | minutes |
| Assistant | Claude API — prompting + tools, no fine-tuning | Anthropic API | — | — |
| Optional research fine-tune of Chronos | Fine-tune | Free Kaggle/Colab GPU | Once | ~1 h |

Experiments run in marimo notebooks on laptops and are logged to a shared MLflow server (host to be decided before P4; artifacts in S3-API object storage).

### 6.2 Training pipeline
1. 02:00 `export_snapshots`: DuckDB reads Postgres → Parquet in `s3://gb-lake/snapshots/date=…/`.
2. pandera validation of training frames.
3. Feature build (Polars/DuckDB), point-in-time as of cut-off (no leakage).
4. Train challenger; evaluate on a time-based holdout (last 2–4 weeks, never random splits).
5. Promote only if the challenger beats `@champion` on the primary metric and guardrails; register in MLflow and move the alias.
6. The ml service hot-reloads `models:/<name>@champion` (checks every 10 minutes).

Training uses synthetic + pilot + live data; metrics are reported separately for pilot/live. A daily job compares forecast vs actual (WAPE), plan acceptance and waste; drift beyond thresholds raises an alert and appears on the console "AI health" page.

### 6.3 Model specifications

**M-A Pantry digital twin (research).** Per household × item consumption rate λ with uncertainty → depletion date and box quantity. λ<sub>hi</sub> ~ Gamma(α<sub>c,i</sub>, β<sub>c,i</sub>) with cohort *c* = size × diet × region. Observations: quantity delivered adjusted by "wasted" / "ran out early" feedback over the inter-delivery interval. Conjugate updates after each delivery; weekly NumPyro refit of cohort priors. Cold start from the cohort prior. Metrics: quantity MAE, 80 % interval calibration, ran-out/wasted rate vs a family-size rule baseline. Fallback: rule table.

**M-B Next-basket ranker.** Candidates: TIFU-KNN personal and neighbour repeats ∪ meal-plan ingredients ∪ seasonal/festival items. Features: recency, frequency, last accept/reject, price delta vs 4-week average, season, festival, cohort popularity, substitution history. LightGBM LambdaRank; label = kept in final box. Metrics: Recall@10, NDCG@10, acceptance rate, edits per box; baselines: repeat last week, top frequency.

**M-C Meal planner — CP-SAT (research; core of Autopilot).** Variables `x[d,s,r]` (day, slot, recipe) and `t[d,s]` (tiffin on no-cook slots). Hard constraints: one choice per slot; fasting/Jain/diabetic rules from household and festival calendar; weekday cook-time limit; no repeat within *k* days; budget from Budget Lock. Objective: preference score, nutrition closeness per member, ingredient-reuse bonus, variety bonus, penalty on price-spiked items. Output → aggregated ingredients × M-A quantities → box lines with `reason_code`; busy days → kit suggestions. 5 s limit per household, batch parallelised. Metrics: constraint satisfaction, nutrition adherence, variety index, ingredient reuse, acceptance; baselines: random valid plan, greedy.

**M-D Budget Lock optimiser.** Same CP-SAT model with the budget as a hard constraint and substitution penalties from `catalog.substitutions`; explanation chips such as "Tomato +62% → lauki".

**M-E Demand forecast + newsvendor procurement (research).** Forecast only uncommitted demand per hub × item × day. StatsForecast (AutoETS, CrostonOptimized, TSB) for intermittent series; MLForecast LightGBM with festival, weekday, price and weather features; Chronos-2 zero-shot for hubs with under 8 weeks of data; HierarchicalForecast MinT reconciliation; conformal intervals. `order_qty = committed + Q_τ(uncommitted)`, τ = C<sub>u</sub>/(C<sub>u</sub>+C<sub>o</sub>), with C<sub>o</sub> reduced by surplus-cascade salvage value and capped by shelf life. Metrics: WAPE, MASE, pinball loss, waste kg per 100 kg sold, stock-out %; baselines: seasonal naive, buy-last-week.

**M-F Routing.** OR-Tools CVRPTW with pickup-and-delivery: capacities in kg and crates, society time windows, service time per stop, donation/crate pickups on the return leg (same-vehicle constraint). OSRM matrix; 30 s guided local search. Vans do two trips by solving one captain-window wave at a time (spike S-3). Metrics: ₹/order, km/order, on-time %; baselines: one trip per order, nearest neighbour.

**M-G Fair kitchen assignment (research).** CP-SAT assignment of tiffin portions to kitchens with capacity, menu skills, rating floor and distance limits; objective = distance + quality penalty + λ × earnings inequality (max deviation from fair share over a rolling four weeks, a linear proxy for the Gini coefficient). Metrics: Gini of weekly earnings, cost per portion, rating; λ sweep gives a Pareto curve.

**M-H Slot pricing / green slots.** Marginal insertion cost of an address into tentative routes → slot discount ranking.

**M-I Churn and uplift.** LightGBM churn model + T-learner uplift; retention offers only where uplift > 0.

**M-J Trust and safety.** Rules + Isolation Forest on refund/donation patterns; complaint photo triage via a vision LLM in batch.

### 6.4 Explainability
Each AI output stores a `reason_code` and parameters (e.g. `SEASONAL`, `PRICE_SWAP{from:tomato,pct:62}`, `PANTRY_LOW{days:2}`); the UI renders localised chips from these codes, not LLM-written text. SHAP top features are stored for ranker decisions.

### 6.5 Evaluation harness
`backend/ml/eval/`: rolling-origin forecast backtests; leave-last-basket-out for the ranker; simulator A/B for Autopilot; fixed-seed routing benchmarks; fairness λ sweep. `just eval` produces a marimo HTML report with paper-ready figures, versioned with the model version.

### 6.6 LLM usage
- **Assistant ("Ghar Saathi" chat, text MVP):** Anthropic Python SDK; tools call the GharBasket API as the user — `get_my_week`, `edit_box_line`, `skip_week`, `book_tiffin`, `track_order`, `explain_item`; strict tool schemas with `tool_choice: auto`; every write returns a proposed change that the user confirms in the UI; prompt caching of system prompt and tools; per-user daily token cap; refusal fallback enabled; logs stored with PII scrubbing. Evaluation set: 200 labelled Hinglish/Marathi/English utterances (Rhythm Thakur, Manish Raje) → intent accuracy, slot-filling F1.
- **Model choice (D13):** build against Claude Opus 5.5; evaluate Opus 5.5, Sonnet 5.5 and Haiku 4.5 in Sprint 8.

| Model | List price per 1M tokens (in / out) | Est. cost per turn | 50 demo users | 500 pilot users |
|---|---|---|---|---|
| Claude Opus 5.5 (`claude-opus-5-5`) | $4 / $20 | ~$0.015 | ~$15/week | ~$150/week |
| Claude Sonnet 5.5 (`claude-sonnet-5-5`) | $2 / $10 | ~$0.008 | ~$8/week | ~$75/week |
| Claude Haiku 4.5 (`claude-haiku-4-5`) | $1 / $5 | ~$0.004 | ~$4/week | ~$40/week |

Assumptions: ~6k input tokens per turn (mostly cached), ~400 output tokens, 5 chats × 4 turns per user per week. Real numbers are measured in Sprint 8.
- **Batch LLM jobs:** complaint photo triage; drafting Hindi/Marathi catalog and recipe text (human-reviewed before publishing).

---

## 7. Core workflows

Sequence and activity diagrams for each workflow are in `docs/handbook/07-workflows.md`.

**W1 Customer onboarding (under 2 minutes).** Language → phone OTP → `/auth/exchange` → society picker (or map pin → society request) → tower/flat → household (size, diet, cuisine, fasting/Jain, consented health tags, weekday cooking time, no-cook days, optional Budget Lock) → preview of the first Autopilot week → payment mode (wallet; Autopay from the pilot phase) → society-unlock progress bar.

**W2 Weekly Autopilot cycle.** Per the job schedule. Between T-3 and T-1 21:00 the customer views the Thali calendar and Parchi box, swaps/removes items, changes quantities, skips the week, adds add-ons and books tiffins. Every edit increments `version` and records feedback for M-A/M-B. Cut-off debits the wallet or captures Autopay; failure → `PAYMENT_FAILED` → 2 h grace → cancel.

**W3 Payments.** Top-up: Razorpay order → checkout in the WebView (test mode) → webhook → ledger → push. Autopay (built in the pilot phase; MS-A uses the wallet): mandate once → pre-debit notice at T-3 → capture at cut-off. Refunds: wallet credit instantly or to source on request. Hourly reconciliation.

**W4 Hub morning operations.** 21:15 procurement plan → hub manager review → purchase orders to vendors. 04:00 receiving: weigh, grade, lots, vendor score inputs. 05:00 packing tasks sorted by route stop; packer scans bag QR, picks lines, flags shortages (substitution or partial refund); bags grouped into society crates.

**W5 Routing and delivery.** 07:00 routes published; rider sees stops, crates and map. At each society, captain handover (crate QR scan, bag count, OTP or photo); captain scans each bag at the door or lobby pickup → `DELIVERED`. Customer away → captain holds until evening → otherwise `UNDELIVERED` → redelivery or refund. Return leg collects crates and donation pickups.

**W6 Tiffin day.** Weekly menus from kitchens approved by the hub; cut-off 21:00 the previous day → fair assignment → kitchens accept counts; READY by 11:30; two-wheeler routes deliver 12:30–13:30; rating prompt 14:30; leftovers listed in the evening "Bachat Box".

**W7 Partner onboarding and payouts.** "Become a partner" in the customer app → role → documents (masked ID, FSSAI registration for kitchens, bank via penny drop — simulated in the prototype) → training modules → hub review → VERIFIED → role appears in the switcher. Earnings accrue in the ledger; weekly payout statement (Razorpay Route/X test mode); downloadable income certificate.

**W8 Kindness donation.** Schedule pickup with items and photos → attached to the return leg of the next route serving the society → hub verification → points (drive multiplier) → batch handoff to an NGO with photo proof → impact counter.

**W9 Surplus cascade (12:30 daily).** Unsold or near-expiry lots → "Imperfect Sabzi" next-day listing → kitchens → NGO → compost; each step logged in `hub_ops.waste_logs` with `cascade_to`.

**W10 Society onboarding.** Hub draws the polygon → `prospect` → captain recruited → unlock threshold N from the route-cost model → referral campaign → N households join → `live` with free-delivery badge.

**W11 ML lifecycle.** Section 6.2.

**W12 Developer workflow.** Section 10.

---

## 8. Module overview

Full specifications: `docs/modules/`.

| Module | Owner | Key endpoints (v1) | Publishes | Consumes | Jobs |
|---|---|---|---|---|---|
| M0 platform | Prathamesh (backend) / Yash (screens) | `/health`, `/files/presign` | — | — | outbox relay, outbox-sweep |
| M1 identity | Prathamesh (backend) / Yash (screens) | `/auth/exchange`, `/auth/refresh`, `/me`, `/households`, `/households/{id}/members`, `/addresses`, `/consents`, `/me/roles` | household_created | partners.verified | — |
| M2 catalog | Prathamesh (backend) / Yash (screens) | `/products`, `/search`, `/products/{id}/substitutes`, `/recipes`, `/prices`, `/festivals` | — | — | embed_catalog |
| M3 subscriptions | Prathamesh (backend) / Yash (screens) | `/subscriptions`, `/cycles/current`, `/cycles/{id}/lines`, `/cycles/{id}/approve`, `/cycles/{id}/skip`, `/budget-lock` | cycle_generated, box_approved, box_skipped | intel.plans_ready | cycle generation, reminders |
| M4 orders | Prathamesh (backend) / Yash (screens) | `/orders`, `/orders/{id}`, `/orders/{id}/events` (SSE), `/slots`, `/addons` | orders.* | box_approved, packed, delivered | cutoff_lock |
| M5 billing | Prathamesh (backend) / Yash (screens) | `/wallet`, `/wallet/topups`, `/mandates`, `/payments/webhook/razorpay`, `/kindness/balance`, `/statements` | wallet_topped_up, payment_failed, refund_issued | orders.locked/delivered/failed | reconcile, expire points |
| M6 hub ops | Prathamesh (backend) / Yash (screens) | `/hubs/{id}/procurement-plans`, `/purchase-orders`, `/grn`, `/packing-tasks`, `/waste-logs`, `/vendors` | procurement_plan_ready, waste_logged | orders.locked, intel.forecast_ready | build_packing_tasks, surplus_cascade |
| M7 logistics | Prathamesh (backend) / Yash (screens) | `/routes`, `/routes/{id}/live` (SSE), `/stops/{id}/handover`, `/deliveries/{order}/proof`, `/pickups` | route_published, pickup_completed, orders.at_society/delivered | orders.packed | solve_routes trigger |
| M8 partners | Prathamesh (backend) / Yash (screens) | `/partner-applications`, `/kyc-documents`, `/trainings`, `/kitchen-capacity`, `/assignments`, `/earnings`, `/payouts` | partners.verified, assignment_created | tiffin orders locked | assign_kitchens trigger, weekly payouts |
| M9 community | Prathamesh (backend) / Yash (screens) | `/societies`, `/societies/{id}/unlock`, `/referrals`, `/donations`, `/drives`, `/ngo-handoffs`, `/impact` | donation_verified | logistics.pickup_completed | — |
| M10 intelligence | Prathamesh (backend) / Yash (screens) | internal `/plan/week`, `/basket/rank`, `/substitutes`, `/forecast/run`, `/route/solve`, `/assign/kitchens`, `/slots/price`; admin `/models`, `/health/ai` | plans_ready, forecast_ready | snapshots | train, forecast, export |
| M11 notifications | Prathamesh (backend) / Yash (screens) | `/notifications`, `/preferences`, `/devices` | — | most events | send |
| M12 console & analytics | Prathamesh (backend) / Yash (screens) | `/analytics/*` | — | — | materialised view refresh |
| M13 assistant | Prathamesh (backend) / Yash (screens) | `/assistant/messages` (SSE), `/assistant/confirm/{proposal}` | — | — | — |

---

## 9. Frontend architecture

Design system, brand, screens and libraries are specified in `docs/FRONTEND_DESIGN.md`.

### 9.1 Mobile app structure
```
frontend/mobile/src/
  app/            router.jsx, providers (Query, i18n, theme, auth), error boundary
  routes/
    _public/      welcome, language, login
    _app/         layout with role switcher; guard: authenticated + role active
      c/          customer: week, box, thali, orders, wallet, kindness, society, assistant, profile, become-partner
      captain/    today, handover-scan, flats, exceptions, earnings
      kitchen/    today-counts, menu, prep-status, earnings, training
      rider/      route-map, stop, proof, pickups, earnings
      packer/     queue, pick-pack, shortages
  roles/*/        feature components per role (lazy chunks)
  shared/         components, hooks, native/, offline/, money, i18n, analytics
```
- Role shell driven by `/me/roles`; active role persisted (Zustand + Preferences); each role a lazy route chunk.
- Offline-capable flows (rider, captain, packer) write to the Dexie outbox first (optimistic UI) and sync with `Idempotency-Key`; route manifest and flat lists cached at route start.
- Performance budget: initial JS ≤ 250 KB gzipped, LCP < 2.5 s on a low-end Android, virtualised lists, AVIF/WebP images.

### 9.2 Console structure
```
frontend/console/app/
  (auth)/login
  (public)/        landing, society referral pages /s/<society> (SEO)
  (admin)/         catalog, prices, plans, societies (map editor), hubs, partners (KYC review), drives, ngos, ai-health, settings
  (hub)/           today (ops board), procurement, receiving, packing, routes (live map), deliveries, waste, vendors
  (franchise)/     pnl, unit-economics, staff, customers
  (analytics)/     kpis, cohorts, impact, experiments
proxy.js           session check + role-based redirect
```

---

## 10. Engineering workflow

### 10.1 Repository layout
```
gharbasket/
  frontend/{mobile,console,packages/{ui,api-client,domain}}
  backend/{api,ml}   data/{simulator,seeds}   e2e/   infra/{postgres,compose,mlflow,osrm}
  docs/{TDD.md, FRONTEND_DESIGN.md, handbook/, adr/, modules/, research/, runbooks/}
  .github/{workflows, CODEOWNERS, pull_request_template.md, ISSUE_TEMPLATE/}
  AGENTS.md (+ synced copies)  justfile  turbo.json  pnpm-workspace.yaml  pyproject.toml
```

### 10.2 Git and reviews
- Trunk-based; protected `main`; squash merge; linear history.
- Branches: `feat/M4-cutoff-lock`, `fix/…`, `docs/…`, `test/…`; Conventional Commits with module scope.
- PRs of about 400 changed lines of hand-written code, not counting tests and generated files (`openapi.json`, `uv.lock`, lockfiles, the generated api-client); larger work is split into PRs that each leave `main` working. Template: module, what/why, screenshots, migration note, test evidence, checklist.
- CODEOWNER approval required. Cross-review: each owner reviews the other's contract changes (module specs §3 and the generated api-client; ADR-0023). Rhythm and Manish review documentation and tests and run the QA checklist on feature PRs.
- Sprint tags `v0.<sprint>.0` with generated changelog.

### 10.3 Module boundary enforcement
- import-linter independence contract across `gharbasket.modules.*`; only `<module>.api` may be imported across modules.
- One Postgres schema per module.
- CODEOWNERS (ADR-0023): Prathamesh → all of `backend/` (every module, `backend/ml`), `infra/`, `data/`, CI and root configuration; Yash → all of `frontend/` (mobile, console UI, `packages/ui`, `packages/domain`); Rhythm and Manish → `docs/`, `e2e/`.

### 10.4 CI/CD (GitHub Actions)

Only `ci.yml` and `claude.yml` exist today; the other workflows are planned and arrive with their PRs.

| Workflow | Trigger | Steps |
|---|---|---|
| `ci.yml` | PR, push to main | Python: Ruff, pyright, import-linter, pytest with the 85 % coverage gate against the project's Postgres image in Docker, `alembic check` (migrations match the models), `openapi.json` up to date, schemathesis fuzzing of every endpoint. JS: Biome, regenerated api-client → no diff, api-client tests, build mobile + console + UI kit; "JS editor hints" (`checkJs`, informational, never blocks). Agent-docs sync check. gitleaks. Added as features land: `oasdiff breaking` vs main. (Single head and the upgrade/downgrade round trip run as pytest tests.) |
| `claude.yml` | `@claude` in an issue, PR comment or review | On-demand Claude Code help; no automatic review |
| `deploy-staging.yml` (planned, PR `chore/M0-deploy`) | Push to main, after CI passes | `alembic upgrade head` on Neon (`GB_ALLOW_REMOTE_MIGRATIONS=1`), then the Render deploy hook (Render's pre-deploy step is paid-only). Vercel deploys the PWA and console from Git. |
| Jobs schedule workflow (planned) | GitHub Actions `schedule` | Calls `POST /v1/internal/jobs/<name>` with `JOBS_TOKEN` (ADR-0021) |
| `e2e.yml` (planned) | PR label `e2e`, nightly | Docker Compose stack + simulator seed → Playwright (mobile web + console) |
| `android.yml` (planned) | PR (mobile paths), tags | Debug APK artifact; on tag, signed AAB → internal testing |
| Production deploy (planned) | — | Decided before P6 with the paid plans (ADR-0020) |

### 10.5 Environments

| Environment | Database | API / ML | Console and PWA | Mobile |
|---|---|---|---|---|
| Local | Docker Postgres (tests and `just seed` refuse any non-local database) | `just dev`; ML, MLflow, OSRM in Docker Compose | `next dev` / Vite | Vite / Android emulator |
| Preview (PR) | Shared Neon database (no branch per PR) | Shared staging API | Vercel preview | Debug APK |
| Staging | Neon (Singapore), one database for the Infisical `dev` and `staging` environments | Render free web service; ML host to be decided before P3/P4 | Vercel | Internal track |
| Production (pilot) | Decided before P6 (Section 19) | Paid plans decided before P6 | Vercel | Internal / closed testing |

### 10.6 Definitions
- **Ready:** module spec exists, endpoints drafted, acceptance criteria written, design available.
- **Done:** code + tests (unit and at least one integration), OpenAPI updated and client regenerated, docs/ADR updated, works on simulator seed, screenshot/demo in the PR, QA checklist passed.

### 10.7 AI-assistant consistency
`AGENTS.md` is canonical (architecture rules, module boundaries, naming, commands, "never hand-edit generated clients", "money in paise", "every POST idempotent") and points to `docs/handbook/00-README.md`. `scripts/sync-agent-docs.sh` copies it to CLAUDE.md, GEMINI.md, CODEBUDDY.md, QODER.md, `.cursorrules` and `.windsurfrules` (preserving the code-review-graph section); CI verifies they match.

---

## 11. Testing and quality

| Layer | Tooling | Owner |
|---|---|---|
| Unit (Python/JS) | pytest, Vitest | Feature author |
| Integration (real PostGIS) | Local Docker Postgres (project image from `infra/postgres`; CI runs the same image) | Feature author |
| Property tests (ledger, state machines) | hypothesis | Prathamesh Relekar |
| Contract / API fuzzing | schemathesis on OpenAPI | CI |
| Solver golden tests | pytest (fixed seeds) | Prathamesh Relekar |
| Model quality gates | Metric thresholds on fixture datasets | Prathamesh Relekar |
| End-to-end | Playwright: onboarding, approve box, cut-off, pack, deliver, donate | Rhythm Thakur, Manish Raje |
| Load | k6: cut-off with 2,000 orders; 200 concurrent box edits | Rhythm Thakur, Manish Raje |
| Device / manual | Low-end Android checklist; accessibility audit (axe, TalkBack) | Rhythm Thakur, Manish Raje |
| Security | OWASP ASVS L1 checklist, gitleaks, pip-audit, pnpm audit, Trivy | All |

Formal test plan and test-case documents (IEEE 829 style) for the report are owned by Rhythm Thakur and Manish Raje.

---

## 12. Security, privacy and compliance

- **AuthN/Z:** Section 4.2; refresh rotation with reuse detection; device list with revoke.
- **Data protection:** KYC numbers encrypted at application level (pgcrypto, key from Infisical) and displayed masked; no Aadhaar storage; health tags only with consent purpose `health_personalisation`.
- **DPDP Act 2023 / Rules 2025:** versioned consent records; `GET /me/data-export`; `DELETE /me` (hard delete PII, keep anonymised aggregates); privacy notice in three languages; no direct collection of children's data (age bands only).
- **Payments:** no card data stored (Razorpay hosted); webhook signatures verified; server-side amount checks.
- **Wallet (closed-system PPI):** spend only on GharBasket; no withdrawal or peer-to-peer transfer; ₹10,000 balance cap; refunds to source; Kindness points are loyalty points with expiry. Customer KYC not required; KYC for everyone paid by the platform.
- **Recurring debits:** RBI e-mandate pre-debit notice at least 24 h before debit (built into the weekly cycle when UPI Autopay arrives in the pilot phase).
- **SMS:** OTP through Firebase Phone Auth / WhatsApp in the prototype (avoids TRAI DLT registration).
- **FSSAI:** kitchen registration numbers captured and shown on "Know your cook"; batch traceability (packing task ↔ lot ↔ vendor).
- **Application:** Capacitor navigation restricted to our origins; CSP on the console; no secrets in client bundles; fixed-window rate limits on OTP exchange and refresh; CSRF checks on cookie-authenticated writes (ADR-0024).
- **Audit log** for admin actions (price changes, KYC decisions, refunds).
- **Academic data clause:** synthetic or consented pilot data only.

---

## 13. Observability and operations

- **SLOs (pilot):** API p95 < 300 ms (reads) / < 800 ms (writes); cut-off job < 60 s; route solve < 30 s per slot; weekly plan batch for 5,000 households < 30 min on 4 cores; uptime 99 %.
- **Dashboards (Grafana):** API latency/errors, job durations and failures, outbox backlog, DB connections, ML job status.
- **Alerts:** cut-off failed/late, payment webhook backlog, route solve failure, error spikes.
- **Runbooks** (`docs/runbooks/`): cut-off failed, Razorpay down, ML service down (fallback switch), restore from backup.

---

## 14. Phases, milestones and gates

| Phase | Sprints | Dates (tentative) | Exit gate / milestone | Yash Salunkhe | Prathamesh Relekar | Rhythm Thakur & Manish Raje |
|---|---|---|---|---|---|---|
| P0 Design | — | now → 18 Oct 2026 | TDD, handbook and ADRs approved by team and guide; ERD v1; module specs M1–M5; Claude Design System + customer core screens; simulator spec | Design review for buildability; review of M1–M5 specs (screens) | M1–M10 specs; simulator spec | UX interviews (8–10 households); review designs; SRS and UML refresh; guide meeting |
| P1 Foundation | S0 | 19 Oct → 1 Nov | Repo, CI, Compose, auth skeleton, tokens, migration baseline, simulator v0; four spikes done | Mobile/console shells, UI tokens, sign-in screens | API platform, auth, ML skeleton, simulator, infra | AGENTS.md rules, test plan, Playwright setup |
| P2 Commerce core | S1–S3 | 2 Nov → 13 Dec | **MS-A:** onboard, rule-based weekly box, edit, wallet payment, cut-off locks orders | Customer screens; console catalog/prices/societies screens | M1–M5 backend; outbox; OSRM prep | E2E for onboarding/approve/cut-off; ledger test cases |
| P3 Operations | S4–S6 | 14 Dec → 24 Jan | **MS-B:** simulated day end to end (procurement → packing → routes → captain → delivered); tiffin day; partner KYC | Packer/rider/captain/kitchen screens; console ops screens | M6, M7, M8, M11 backend | Ops E2E, device testing, pilot operations plan |
| P4 Intelligence | S7–S8 | 25 Jan → 21 Feb | **MS-C:** explained meal plan + Budget Lock; forecast-driven procurement; fair assignment; assistant text | Autopilot UX (Thali, Parchi, reasons), assistant UI | M10 models + eval harness, M13 | Label 200 assistant utterances; translations; paper sections |
| P5 Community & impact | S9 | 22 Feb → 7 Mar | **MS-D:** society unlock, referrals, Kindness pickups, NGO handoff, impact dashboard, green slots | M9 screens, wallet/Kindness UI | M9 backend, slot pricing, analytics | NGO tie-up, drive content |
| P6 Pilot & evaluation | S10–S11 | 8 Mar → 4 Apr | Real households (1–2 societies) for ≥ 3 weeks; evaluation reports; security/performance pass | Fixes, polish | Evaluation, retrain on pilot data | Pilot operations, surveys, k6/axe, bug bash |
| P7 Documentation & defence | S12 + buffer | 5 Apr → 18 Apr | Final report, IEEE paper, demo video, viva deck | Demo build | Paper results | Report, paper writing, slides |

Each milestone ends with a demo to Prof. Kranti Bade; each phase begins by finalising its module specs. Stretch items (voice, OTA updates, Autopay edge cases) are cut first if a milestone slips.

**Phase 1 spikes:** Razorpay checkout inside Capacitor; Firebase phone OTP inside Capacitor; OR-Tools + OSRM on a Mumbai Metropolitan Region sample; CP-SAT meal plan on 20 recipes.

---

## 15. Budget

| Item | Cost |
|---|---|
| Render (API, Key Value), Vercel, Neon, Cloudflare R2, Infisical, UptimeRobot | ₹0 (free tiers until the pilot; paid plans decided before P6, ADR-0020) |
| Grafana Cloud, PostHog, Firebase (test numbers; Blaze plan for real OTPs at the pilot) | ₹0 (free tiers) |
| Domain (optional; none yet) | ~₹800/year if bought |
| Claude API (assistant, demo scale) | ~₹350–1,300/week depending on model; capped |
| Razorpay / WhatsApp | Test mode / test number: ₹0 |
| Play Console (one-time) | ~₹2,100 |
| Packaging and pilot materials | ₹2,000–4,000 (per proposal) |

Within the proposal's ₹3,800–18,000 envelope with sensible model and pilot-size choices.

---

## 16. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Two people carry all code | High | High | Strict MVP; cut stretch first; Rhythm and Manish own testing, content and operations |
| WebView performance on low-end phones | Medium | Medium | Performance budget; virtualisation; real low-end device testing from S1 |
| Capacitor plugin gaps (Razorpay in WebView, Firebase OTP) | Medium | High | Phase 1 spikes before committing |
| Insufficient real data for the paper | High | Medium | Simulator with ground truth; pilot data reported separately |
| Migration conflicts / module coupling | Medium | High | Single-head CI, import-linter, CODEOWNERS |
| Scope creep | High | High | Changes via ADR and guide approval |
| OSRM / maps setup | Medium | Medium | MMR-clipped extract; prebuilt files in object storage |
| LLM cost overrun | Low | Medium | Per-user caps, cheaper model option, caching |
| Exams collide with milestones | High | Medium | Floating buffer sprint; light sprints during exams |
| Neon latency from Mumbai (no India region) | Medium | Medium | Batch queries, pooling; decide production DB placement before P6 |
| Compliance claims challenged | Medium | Medium | Closed-loop wallet, simulated KYC, guide sign-off, cite RBI/DPDP texts |

---

## 17. Architecture style decision: service-based, not microservices

GharBasket uses a **service-based architecture**: one modular monolith plus a small number of separately deployed services where separation has a technical reason.

| Deployable | Why separate |
|---|---|
| `api` (FastAPI modular monolith, 11 modules) | All transactional business logic; one database transaction can span order, ledger and inventory |
| `worker` (same code, Taskiq) — from the pilot | Background jobs must not slow API requests. Until a paid worker exists, jobs run without a broker: outbox relay after commit and internal job endpoints called by a GitHub Actions schedule (ADR-0021) |
| `ml` (FastAPI) | Different dependencies (torch, OR-Tools, LightGBM, NumPyro) and resource profile; can fail or retrain without affecting orders |
| `ml-worker` (same code as ml) — from the pilot | Long batch jobs (weekly plans, training); until then they run in the ML service or offline |
| `console`, `mobile` | Frontends |
| Postgres, Valkey, MLflow, OSRM | Off-the-shelf infrastructure |

**Why not full microservices:** four students and about six months; money and order flows need ACID transactions across modules (wallet debit + order lock + stock reservation) that microservices would turn into sagas; each extra service multiplies CI, deployment, monitoring and local setup; "monolith first, extract when proven" is established practice.

**Why it remains microservice-ready:** each module owns its schema, exposes only `api.py`, and communicates through outbox events — the same contracts a microservice would use; import-linter enforces boundaries in CI. Scale path: modular monolith → extract notify/logistics/intel → event bus (Redis Streams → Kafka) → per-city database shards.

---

## 18. Final technology stack summary

| Layer | Choice | Rationale |
|---|---|---|
| Mobile app | React 19 + Vite + TanStack Router → PWA + Capacitor 8 (Android) | One JavaScript codebase with the console (ADR-0018); Flutter dropped (second language, no shared UI); React Native/Expo rejected (no UI sharing with web) |
| Web console + public site | Next.js 16 | Server rendering for dashboards and SEO landing/referral pages; route groups; `proxy.js` auth gate |
| Backend API | FastAPI (Python 3.13) modular monolith | Python ML ecosystem; async + Pydantic + automatic OpenAPI → generated client and types; NestJS rejected (second backend language); Django rejected (sync-first, heavier) |
| ML / optimisation | Separate FastAPI service: OR-Tools, StatsForecast/MLForecast, LightGBM, NumPyro, Chronos-2, MLflow | Isolated heavy dependencies; CPU-only training |
| Jobs | Postgres outbox + GitHub Actions schedule calling internal job endpoints; Taskiq + Valkey at the pilot | Free, no broker to run (ADR-0021); Celery rejected |
| Database | PostgreSQL 18 (+PostGIS, pgvector, pg_trgm), Valkey, S3-API object storage (Cloudflare R2; SeaweedFS locally), DuckDB | Relational integrity for money and orders; JSONB for flexibility |
| API contract | OpenAPI → openapi-typescript + openapi-fetch + openapi-react-query (ADR-0022) | Generated contract between teammates; editor hints in JavaScript |
| LLM | Claude via Anthropic SDK (model after S8 evaluation) | Tool calling, multilingual |
| Auth / push | Firebase Phone Auth + FCM; own JWT sessions (PyJWT, rotating refresh tokens, httpOnly cookies for browsers) | ADR-0024 |
| Payments | Razorpay (Checkout top-ups, Route; UPI Autopay in the pilot phase) — test mode, own thin client | |
| Maps / routing | MapLibre + OpenFreeMap/MapTiler; self-hosted OSRM | No Google Maps billing |
| Hosting | Render (API, Key Value) + Vercel (PWA, console) + Neon + Cloudflare R2; Infisical for secrets | Free tiers, no bill possible (ADR-0020); ML, OSRM and MLflow hosts decided before P3/P4 |
| CI/CD | GitHub Actions (CI; migrations then the Render deploy hook; job schedule); Docker Compose locally | |
| Observability | structlog, OpenTelemetry → Grafana Cloud, UptimeRobot, PostHog | |
| Architecture | Service-based: modular monolith + ML service + scheduled jobs (workers at the pilot) | Not microservices (Section 17) |

---

## 19. Open items

| Item | Decide by |
|---|---|
| Exam dates and final submission deadline (moves the buffer sprint) | As soon as the department publishes them |
| United Brothers Global — involvement and IP terms | Before P2 |
| Pilot database placement (Q3): Neon Singapore (~60 ms round trip — to be verified) or a paid Mumbai Postgres; and paid plans (Render Starter, a worker, Firebase Blaze for real OTPs) | Before P6 |
| Hosting for the ML service, OSRM and MLflow | Before P3/P4 |
| Inform Prof. Kranti Bade of the Flutter → Capacitor change and update the report | P0 |

# Chapter 04 — System Architecture

> **In short:**
> - GharBasket uses a **service-based architecture**: one **FastAPI modular monolith** (11 business modules), one **ML/optimisation service**, background **jobs** (no broker until the pilot, D21), and two frontends. It is **not** microservices.
> - Modules are strictly separated: each owns one Postgres schema, exposes only `api.py`, and talks to others through that facade or through outbox events. CI enforces this with import-linter.
> - Six principles: modular monolith first; ML never blocks a customer; solvers decide, statistics predict, LLMs talk; every write is idempotent and every money movement is a ledger entry; contract-first APIs; boring tech, sharp product.
> - The full technology stack is in §7 and is final (D14).

---

## 1. Design principles

1. **Modular monolith first.** One deployable API with hard internal boundaries, so four people never fight over microservice plumbing. Any module can be extracted later because modules already talk only through `api.py` facades and events.
2. **ML never blocks a customer.** Heavy AI runs in batch and writes results into `intel.*` tables. Online ML calls have timeouts and rule-based fallbacks. If the ML service is down, Autopilot falls back to last week's box adjusted by family-size rules.
3. **Solvers decide, statistics predict, LLMs talk.** The LLM never computes money or quantities.
4. **Every write is idempotent; every money movement is a ledger entry.**
5. **Contract-first.** The OpenAPI specification is the boundary between teammates; frontends use generated clients only.
6. **Boring tech, sharp product.** Choose well-documented libraries; spend the innovation budget on algorithms and UX.

## 2. C4 level 1 — system context

*Figure 4.1 — C4 context diagram*

```mermaid
flowchart TB
  HH(("Households"))
  FP(("Field partners<br/>Captain, Kitchen, Rider, Packer"))
  OP(("Operators<br/>Admin, Hub manager,<br/>Franchise owner, NGO"))
  GB["GharBasket platform<br/>mobile app, console, API, ML service"]
  RZ["Razorpay<br/>payments, UPI Autopay, payouts"]
  FB["Firebase<br/>phone OTP, push"]
  WA["WhatsApp Cloud API<br/>notifications"]
  CL["Claude API<br/>assistant, triage"]
  OS["OSRM<br/>road distances (self-hosted)"]
  MT["Map tiles<br/>OpenFreeMap / MapTiler"]
  R2["Cloudflare R2 (S3 API)<br/>files, data lake"]
  HH -->|"plan, order, pay, donate"| GB
  FP -->|"handover, cook, deliver, pack"| GB
  OP -->|"manage catalogue, hubs, partners"| GB
  GB --> RZ
  GB --> FB
  GB --> WA
  GB --> CL
  GB --> OS
  GB --> MT
  GB --> R2
```

## 3. C4 level 2 — containers

*Figure 4.2 — C4 container diagram*

```mermaid
flowchart TB
  subgraph Clients
    MOB["frontend/mobile<br/>React 19 + Vite SPA<br/>PWA + Capacitor 8 Android"]
    CON["frontend/console<br/>Next.js 16<br/>console + public site"]
  end
  VER["Vercel<br/>hosts PWA + console,<br/>same-origin /v1/* rewrite"]
  GHA["GitHub Actions schedule<br/>calls internal job endpoints"]
  subgraph Backend["Backend (Python 3.13)"]
    API["backend/api<br/>FastAPI modular monolith<br/>13 modules, on Render<br/>outbox relay + jobs inside"]
    ML["backend/ml<br/>FastAPI: planners, forecasts,<br/>routing, assignment, LLM,<br/>batch plans, training"]
  end
  subgraph Data["Data and infrastructure"]
    PG[("PostgreSQL 18<br/>Neon (Singapore) / Docker<br/>one schema per module")]
    VK[("Valkey<br/>Render Key Value / Docker<br/>rate limits, sign-out deny-list")]
    MLF["MLflow<br/>tracking + registry"]
    OBJ[("Cloudflare R2 / SeaweedFS (local)<br/>S3 API: files + Parquet lake")]
    OSRM["OSRM<br/>Mumbai region extract"]
  end
  MOB -->|"browser: HTTPS JSON + SSE"| VER
  CON -->|"HTTPS JSON"| VER
  VER -->|"/v1/*"| API
  MOB -->|"Android app: HTTPS + Bearer"| API
  GHA -->|"POST /v1/internal/jobs/..."| API
  API -->|"httpx2 with timeout and fallback"| ML
  API --> PG
  API --> VK
  ML --> PG
  ML --> MLF
  ML --> OBJ
  ML --> OSRM
  API --> OBJ
```

Where it runs (D20): the API and Valkey on Render's free tier (Singapore), Postgres on Neon (Singapore), files on Cloudflare R2, and both frontends on Vercel, which rewrites `/v1/*` to the API so browsers stay same-origin. The platforms provide TLS. Hosting for the ML service, MLflow and OSRM is not decided yet (needed from P3/P4); locally everything runs in Docker Compose. Details: [Chapter 12](12-deployment-and-operations.md).

## 4. C4 level 3 — components

*Figure 4.3 — Component diagram: API modules, ML service and background jobs*

```mermaid
flowchart LR
  subgraph API["backend/api (modular monolith)"]
    PLAT["platform<br/>db, auth, outbox, config"]
    IDN["identity"]
    CAT["catalog"]
    SUB["subscriptions"]
    ORD["orders"]
    BIL["billing"]
    OPS["hub_ops"]
    LOG["logistics"]
    PAR["partners"]
    COM["community"]
    NOT["notifications"]
    IG["intel_gateway"]
    ANL["analytics"]
    AST2["assistant"]
  end
  subgraph MLS["backend/ml"]
    PLN["meal planner CP-SAT"]
    PTW["pantry twin"]
    RNK["basket ranker"]
    FCS["forecast + newsvendor"]
    RTE["routing OR-Tools"]
    ASG["fair kitchen assignment"]
    AST["assistant"]
  end
  subgraph WK["background jobs (no broker, D21)"]
    RELAY["outbox relay<br/>after each commit + sweep"]
    JOBS["scheduled jobs<br/>internal endpoints"]
    MLJ["ml batch + training<br/>ML service or offline"]
  end
  SUB -->|"api.py"| CAT
  ORD -->|"api.py"| SUB
  ORD -->|"api.py"| BIL
  OPS -->|"api.py"| ORD
  LOG -->|"api.py"| OPS
  PAR -->|"api.py"| IDN
  COM -->|"api.py"| BIL
  IG -->|HTTP| MLS
  PLAT -->|outbox rows| RELAY
  RELAY -->|events| NOT
  JOBS --> ORD
  MLJ --> MLS
```

Each module folder contains `api.py` (the only file other modules may import), `router.py` (HTTP endpoints), `service.py` (business logic), `models.py` (SQLAlchemy tables in its own schema), `schemas.py` (Pydantic) and `events.py`. Tests live in `backend/api/tests/<module>/`, outside the package, so test fixtures may combine modules and tests are not shipped in the image.

## 5. Package view — the monorepo

*Figure 4.4 — Package diagram of the monorepo*

```mermaid
flowchart TB
  subgraph REPO["gharbasket/ (monorepo)"]
    subgraph APPS["frontend/"]
      AM["mobile"]
      AC["console"]
    end
    subgraph PKGS["frontend/packages/"]
      PUI["ui<br/>tokens, components"]
      PAC["api-client<br/>generated: openapi-fetch<br/>+ openapi-react-query"]
      PDM["domain<br/>shared enums"]
      PCF["config<br/>jsconfig, Biome"]
    end
    subgraph SVC["backend/"]
      SAPI["api<br/>FastAPI modules"]
      SML["ml<br/>models, solvers, eval"]
    end
    DATA["data/<br/>simulator, seeds"]
    E2E["e2e/<br/>Playwright"]
    INFRA["infra/<br/>postgres, compose, mlflow, osrm"]
    DOCS["docs/<br/>handbook, TDD, adr, modules"]
  end
  AM --> PUI
  AM --> PAC
  AM --> PDM
  AC --> PUI
  AC --> PAC
  AC --> PDM
  SAPI -->|"exports openapi.json"| PAC
  DATA -->|"writes through services"| SAPI
  E2E --> AM
  E2E --> AC
```

## 6. Are we using microservices? No — decision record

**We use a service-based architecture**: one modular monolith plus a small number of separately deployed services, each split for a real technical reason.

| Deployable | Why it is separate |
|---|---|
| `api` (FastAPI modular monolith, 13 modules) | All transactional business logic. One database transaction can safely span order + ledger + inventory |
| `worker` (same code as api, Taskiq) — from the pilot | Background jobs must not slow API requests. Until a paid worker exists, jobs run without a broker: the API relays outbox rows after each commit and a GitHub Actions schedule calls internal job endpoints (D21) |
| `ml` (FastAPI) | Different dependencies (torch, OR-Tools, LightGBM, NumPyro), different CPU/RAM profile; can crash or retrain without touching orders |
| `ml-worker` (same code as ml) — from the pilot | Long batch jobs (weekly plans, training); until then they run in the ML service or offline (D21) |
| `console` (Next.js), `mobile` (static bundle) | Frontends |
| Postgres, Valkey, MLflow, OSRM | Off-the-shelf infrastructure |

**Why not full microservices:**
- **Team and time.** Four students, about six months.
- **Money needs one transaction.** Wallet debit + order lock + stock reservation must be ACID. Across microservices they would become sagas with distributed failure handling.
- **Cost of every extra service.** Each one multiplies CI pipelines, deployments, monitoring and local setup.
- **Common practice.** Industry practice (for example Shopify's modular monolith) is "monolith first, extract when proven".

**Why it is still microservice-ready:**
- Each module owns its Postgres schema, exposes only `api.py`, and communicates via outbox events — the same contracts a microservice would use.
- import-linter enforces those boundaries in CI.
- Extracting `notify` or `logistics` later means moving a folder and switching an in-process call to HTTP or a queue.

*Figure 4.5 — Scale path*

```mermaid
flowchart LR
  S1["Today<br/>modular monolith<br/>+ ML service + jobs"] --> S2["Extract notify,<br/>logistics, intel<br/>as services"]
  S2 --> S3["Event bus<br/>Redis Streams, then Kafka"]
  S3 --> S4["Per-city database shards<br/>read replicas"]
```

## 7. Final technology stack (D14)

| Layer | Final choice | Why this, not the alternative |
|---|---|---|
| Mobile app | React 19 + Vite + TanStack Router → PWA + Capacitor 8 (Android) | One JavaScript codebase with the console (D18). Flutter dropped (second language, no shared UI). React Native/Expo rejected (no UI sharing with web) |
| Web console + public site | Next.js 16 | Server rendering for dashboards and SEO landing/referral pages; route groups; `proxy.js` sign-in gate (cookies are set and refreshed by the API, D24). A Vite SPA cannot do SEO pages well |
| Backend API | FastAPI (Python 3.13) modular monolith | The ML/optimisation ecosystem is Python; async + Pydantic + automatic OpenAPI → generated API client (D22). NestJS rejected (second backend language); Django rejected (sync-first, heavier) |
| ML / optimisation | Separate FastAPI service: OR-Tools, StatsForecast/MLForecast, LightGBM, NumPyro, Chronos-2, MLflow | Isolated heavy dependencies; CPU-only training; hosting to be decided before P3 (Docker Compose locally) |
| Background jobs | Postgres outbox (`FOR UPDATE SKIP LOCKED`) relayed by the API after each commit; scheduled jobs as `POST /v1/internal/jobs/<name>` called by a GitHub Actions schedule (D21). Taskiq + Valkey from the pilot | Free and durable; APScheduler rejected (misses jobs while the free API sleeps); Celery rejected (sync-first) |
| Database | PostgreSQL 18 (+ PostGIS, pgvector, pg_trgm), Valkey, S3-compatible storage, DuckDB | Relational integrity for money and orders; geo, vector and fuzzy search in one database |
| API contract | OpenAPI → openapi-typescript (`schema.d.ts` types) + openapi-fetch + openapi-react-query (TanStack Query hooks) (D22) | Generated contract between Yash and Prathamesh; CI fails on drift |
| LLM | Claude via the Anthropic SDK (model chosen after the Sprint 8 evaluation) | Tool calling, multilingual |
| Backend libraries | Pydantic 2, SQLAlchemy 2.1 async + asyncpg, Alembic, structlog, PyJWT, httpx2, phonenumbers, redis client (Valkey), GeoAlchemy2 + h3 | Tests: pytest + anyio, pytest-cov (85% gate), import-linter |
| Auth / push | Firebase Phone Auth + FCM; our own JWT sessions (D24) | Firebase ID token verified with PyJWT, not firebase-admin |
| Payments | Razorpay (Checkout, UPI Autopay, Route) — test mode; our own thin async client on httpx2 | UPI Autopay moves to the pilot phase |
| Maps / routing | MapLibre + OpenFreeMap/MapTiler; OSRM self-hosted | No Google Maps bills |
| Hosting | Free tiers (D20): Render (API, Key Value), Vercel (PWA, console, PR previews), Neon Postgres (Singapore, one shared dev/staging database), Cloudflare R2 (files), Infisical (secrets). Hosting for the ML service, OSRM and MLflow to be decided before P3 | No bill possible during development; no server to maintain |
| CI/CD | GitHub Actions (CI; after merge `alembic upgrade head` on Neon, then the Render deploy hook); Render builds the Docker image; Docker Compose locally | |
| Observability | structlog JSON logs, OpenTelemetry → Grafana Cloud, PostHog | Sentry is not used |
| Architecture | Service-based: modular monolith + ML service + background jobs | Not microservices (§6) |

Detailed library lists per layer are in `docs/TDD.md` (Part 2) and [Chapter 09](09-frontend-and-design.md) for the frontend.

## Decisions & open questions

- D2 Frontend codebase — [ADR-0002](../adr/0002-react-capacitor-mobile-nextjs-console.md). D3 Python-only backend — [ADR-0003](../adr/0003-python-fastapi-backend-and-ml-service.md). D6 Postgres-centric database — [ADR-0006](../adr/0006-postgresql-centric-data-platform.md).
- D14 Final stack and service-based architecture — [ADR-0014](../adr/0014-final-stack-and-service-based-architecture.md). D18 JavaScript frontend — [ADR-0018](../adr/0018-javascript-not-typescript-frontend.md). D20 Free-tier hosting on Render, Vercel, Neon, R2 and Infisical — [ADR-0020](../adr/0020-free-tier-hosting-render-vercel-neon-r2.md) (supersedes ADR-0011 and ADR-0015). D21 Jobs without a broker — [ADR-0021](../adr/0021-jobs-without-a-broker.md). D22 Generated API client — [ADR-0022](../adr/0022-api-client-openapi-fetch.md).
- Open: before the pilot (Phase 6), decide on paid plans (Render Starter, a worker, Firebase Blaze) and whether the pilot database stays on Neon Singapore (about 60 ms per round-trip, to be verified) or moves to a paid Postgres in Mumbai (Q3).
- Open: hosting for the ML service, OSRM and MLflow, to be decided before P3.

Related: [03 Product and features](03-product-and-features.md) · [05 Data architecture](05-data-architecture.md) · [10 API and integration](10-api-and-integration.md) · [12 Deployment and operations](12-deployment-and-operations.md)

# Chapter 10 — API and Integration

> **In short:**
> - A REST API under `/v1`, described by **OpenAPI**. The frontend client is **generated** (openapi-typescript + openapi-fetch + openapi-react-query, D22) and never hand-written. This contract is the boundary between teammates.
> - Every POST that creates money or orders requires an **`Idempotency-Key`**. Money is always **integer paise**. Errors follow **RFC 9457** problem+json.
> - Auth: Firebase phone OTP → our own short-lived JWT + rotating refresh token (D24). Apps send a Bearer token; browsers get httpOnly cookies. **Roles carry scopes** (society, hub), and the server checks permissions on every request.
> - Modules talk through a **transactional outbox**: a domain change and its event are written in one database transaction, then the API relays it after the commit to idempotent handlers (no broker until the pilot, D21).
> - External integrations: Razorpay, Firebase, WhatsApp Cloud API, OSRM, map tiles, Claude API, Cloudflare R2 (S3 API).

---

## 10.1 API conventions

| Topic | Rule |
|---|---|
| Style | REST under `/v1`. OpenAPI tags = module names. Plural nouns (`/v1/households/{id}/members`). |
| Errors | RFC 9457 `application/problem+json` with `type`, `title`, `status`, `detail`, `code`, `errors[]`. Codes are namespaced: `orders.cutoff_passed`, `billing.insufficient_balance`. |
| Pagination | Cursor-based: `?cursor=…&limit=…` → `next_cursor`. |
| Idempotency | `Idempotency-Key` header **required** on every POST that creates money or orders. Stored in `platform.idempotency_keys` (key, user, request hash, response, 24 h TTL). |
| Concurrency | `version` column + `If-Match` header for editing boxes, plans, prices and households (optimistic locking). `GET` returns `ETag: "<version>"`; a `PATCH` without `If-Match` is 428 `request.precondition_required`, a stale one is 412 `<module>.version_conflict` and the client reloads before retrying. |
| Headers | `Authorization: Bearer <access>`, `X-Active-Role: captain:society_123`, `Accept-Language: hi-IN`. |
| Money | Integer paise (`amount_paise`), never floats. The UI formats Indian style (₹1,23,456). |
| Time | ISO-8601 with offset. Business dates as `YYYY-MM-DD` in Asia/Kolkata. |
| Webhooks in | Signature verified → raw payload stored (`billing.payment_webhooks`) → processed idempotently by a job. |
| Realtime | Server-Sent Events: `GET /v1/orders/{id}/events`, `GET /v1/routes/{id}/live`. |
| Files | `POST /v1/files/presign` (purpose, type, size) → direct upload to the private bucket over the S3 API (Cloudflare R2; SeaweedFS locally). The API stores only the key and metadata; reads use 5-minute signed links (M0 §3.1). |

**Contract-first workflow.** FastAPI generates `openapi.json`. `just gen-api` regenerates `frontend/packages/api-client` (openapi-typescript `schema.d.ts` types + openapi-fetch client + openapi-react-query TanStack Query hooks, D22); it is never edited by hand. CI runs `oasdiff` to block breaking changes unless the PR is labelled `breaking`, and fails if the generated client is stale.

## 10.2 Authentication, roles and permissions

```mermaid
sequenceDiagram
    autonumber
    actor U as User (app / browser)
    participant FB as Firebase Auth
    participant API as api (identity module)
    participant VK as Valkey
    participant DB as PostgreSQL
    U->>FB: phone number
    FB-->>U: OTP via SMS
    U->>FB: enter OTP
    FB-->>U: Firebase ID token
    U->>API: POST /v1/auth/exchange (ID token)
    API->>VK: rate-limit check (fixed window)
    API->>API: verify ID token with PyJWT (Google public keys, cached)
    API->>DB: find or create identity.users (first exchange = signup, customer role), load user_roles
    API->>DB: create session, store refresh token as HMAC-SHA256
    API-->>U: access JWT (15 min) + refresh token + roles[] (JSON for apps, httpOnly cookies for browsers)
    U->>U: role chooser if more than one active role
    U->>API: GET /v1/cycles/current (Bearer or cookie, X-Active-Role)
    API->>VK: session on the sign-out deny-list?
    API->>API: require("subscriptions:read", scope) permission check
    API-->>U: 200 current week
    Note over U,API: access token expired
    U->>API: POST /v1/auth/refresh (refresh token)
    API->>DB: check session, rotate token (2-minute grace), other reuse revokes the session
    API-->>U: new access + new refresh token
```
*Figure 10.1 — Login, token exchange, role selection and refresh*

- **Firebase ID token check:** the API verifies it itself with PyJWT against Google's published keys (audience, issuer, expiry). It does not use `firebase-admin`.
- **Our tokens:** an access JWT (HS256 with a `kid`), valid 15 minutes; an opaque refresh token stored only as HMAC-SHA256 with a server pepper, in a session of at most 30 days. Refresh tokens rotate on every use. A used token that comes back within 2 minutes while its child is unused (lost response, two tabs) gets a replacement; any other reuse revokes the session (`auth.refresh_reused`).
- **Apps (Capacitor)** send `Authorization: Bearer` and keep the refresh token in secure storage.
- **Browsers (PWA, console)** send `X-GB-Client: web` and never see a token. The API sets httpOnly cookies `__Host-gb_at` (Path=/, 15 minutes) and `gb_rt` (Path=/v1/auth). Vercel rewrites `/v1/*` to the API, so the cookies are same-origin.
- **CSRF:** a cookie-authenticated POST, PATCH, PUT or DELETE without `X-GB-Client: web`, or from an Origin outside `WEB_ORIGINS`, gets 403 `auth.csrf_rejected`.
- **Instant sign-out:** logout, logout-all, device sign-out and refresh reuse put the session on a Valkey deny-list for the access token's remaining life. A refresh always checks the session in Postgres.
- **Rate limits:** fixed windows in Valkey. Exchange: 30 per 10 minutes per address and 10 per 10 minutes per phone (keyed by a hash, never the number). Refresh: 300 per minute per address. If Valkey is down, requests are allowed.
- **Dev login:** `dev_phone` signs in without OTP, only when `APP_ENV=dev`.
- **Roles with scopes:** `identity.user_roles(user_id, role, scope_type, scope_id, status)`.

| Role | Scope | Status flow |
|---|---|---|
| customer | — | active |
| captain | society | applied → kyc_pending → verified → suspended |
| kitchen_partner, rider, packer | hub | applied → kyc_pending → verified → suspended |
| hub_manager, franchise_owner | hub | assigned by admin |
| ngo_coordinator | ngo | assigned by admin |
| admin | global | assigned |

- **Permissions:** a code-level matrix `permission → roles`, enforced by a FastAPI dependency (`require("orders:read", scope=hub_id)`). The role switch in the app is only UX.
- **Defence in depth:** Postgres Row-Level Security on hub-scoped tables. The API sets `SET LOCAL app.hub_ids = '…'` per transaction.

## 10.3 Events: transactional outbox

```mermaid
flowchart LR
    subgraph TX["One database transaction"]
        SVC["Module service<br/>e.g. orders.lock_order()"] --> W1["Write domain rows<br/>orders, order_events"]
        SVC --> W2["Insert platform.outbox row<br/>event_type, payload"]
    end
    W2 --> RL["Outbox relay (API, after each commit,<br/>plus a scheduled sweep)<br/>SELECT ... FOR UPDATE SKIP LOCKED"]
    RL --> TQ["Handlers in the API<br/>(Taskiq from the pilot)"]
    TQ --> H1["billing handler<br/>debit wallet"]
    TQ --> H2["notify handler<br/>push / WhatsApp"]
    TQ --> H3["ops handler<br/>procurement input"]
    H1 --> DD["Dedupe on event_id<br/>(idempotent consumers)"]
    H2 --> DD
    H3 --> DD
    RL --> MK["Mark outbox row sent_at"]
```
*Figure 10.2 — Outbox event flow (at-least-once, idempotent consumers)*

**Event catalog (v1)**

| Module | Events |
|---|---|
| identity | `identity.household_created` |
| subscriptions | `subscriptions.cycle_generated`, `subscriptions.box_approved`, `subscriptions.box_skipped` |
| orders | `orders.locked`, `orders.shortage_substituted`, `orders.packed`, `orders.dispatched`, `orders.at_society`, `orders.delivered`, `orders.delivery_failed` |
| billing | `billing.wallet_topped_up`, `billing.payment_failed`, `billing.refund_issued` |
| ops | `hub_ops.procurement_plan_ready`, `hub_ops.waste_logged` |
| logistics | `logistics.route_published`, `logistics.pickup_completed` |
| partners | `partners.verified`, `partners.assignment_created` |
| community | `community.donation_verified` |
| intel | `intel.plans_ready`, `intel.forecast_ready` |

## 10.4 Scheduled jobs (IST, per hub; T = delivery day)

Each job is an internal endpoint, `POST /v1/internal/jobs/<name>`, protected by the `JOBS_TOKEN` secret and called by a GitHub Actions scheduled workflow (D21). Jobs are idempotent, take a Postgres advisory lock and use the scheduled time, not the run time. Long ML jobs (`retrain_models`, forecasts) run in the ML service or offline.

| When | Job | Module |
|---|---|---|
| T-3 18:00 | `generate_weekly_plans` (meal plan → box drafts) | intel → subscriptions |
| T-3 18:30 | `send_box_ready_and_pre_debit_notices` | notify / billing |
| T-1 19:00 | `remind_unapproved` | notify |
| T-1 20:55 | `apply_auto_approve` | subscriptions |
| **T-1 21:00** | **`cutoff_lock`** (Postgres advisory lock; idempotent; cron-job.org) | orders / billing |
| T-1 23:00 | `payment_grace_expiry` (cron-job.org) | orders |
| T-1 21:10 | `forecast_uncommitted` → `build_procurement_plan` | intel → ops |
| T-1 21:30 | `assign_kitchens` (tiffin for day T) | intel → partners |
| T 05:00 | `build_packing_tasks` | ops |
| T 07:00 | `solve_routes` (per slot) | intel → logistics |
| T 12:30 | `surplus_cascade` | ops |
| Daily 02:00 | `export_snapshots` (Postgres → Parquet in object storage, S3 API) + data-quality checks | intel |
| Sun 03:00 | `retrain_models` (champion/challenger) | ml |
| Hourly / daily | `reconcile_payments` (hourly); `expire_kindness_points` (daily) | billing |

## 10.5 Key endpoints per module (v1)

| Module | Endpoints |
|---|---|
| M1 identity | `POST /auth/exchange`, `/auth/refresh`, `GET/PATCH /me`, `/households`, `/households/{id}/members`, `/addresses`, `/consents`, `/me/roles`, `POST/GET /me/data-export`, `DELETE /me` |
| M2 catalog | `/products`, `/search?q=`, `/products/{id}/substitutes`, `/recipes`, `/prices?hub=`, `/festivals` |
| M3 subscriptions | `/subscriptions`, `/cycles/current`, `PATCH /cycles/{id}/lines`, `PUT /cycles/{id}/meals`, `POST /cycles/{id}/approve`, `/skip`, `/budget-lock` |
| M4 orders | `/orders`, `/orders/{id}`, `/orders/{id}/events` (SSE), `/orders/{id}/issues`, `/slots`, `/addons` |
| M5 billing | `/wallet`, `/wallet/topups`, `/mandates`, `/payments/webhook/razorpay`, `/kindness/balance`, `/statements?period=` |
| M6 ops | `/hubs/{id}/procurement-plans`, `/purchase-orders`, `/grn`, `/packing-tasks`, `/waste-logs`, `/vendors` |
| M7 logistics | `/routes?date&slot`, `/routes/{id}/live` (SSE), `/stops/{id}/handover`, `/deliveries/{order}/proof`, `/pickups` |
| M8 partners | `/partner-applications`, `/kyc-documents`, `/trainings`, `/kitchen-capacity`, `/assignments`, `/earnings`, `/payouts` |
| M9 community | `/societies`, `/societies/{id}/unlock`, `/referrals`, `/donations`, `/drives`, `/ngo-handoffs`, `/impact` |
| M10 intel (ml, internal) | `/plan/week`, `/basket/rank`, `/substitutes`, `/forecast/run`, `/route/solve`, `/assign/kitchens`, `/slots/price`; admin `/models`, `/health/ai` |
| M11 notify | `/notifications` (inbox), `/preferences`, `/devices` |
| M12 analytics | `/analytics/*` (KPIs, unit economics, impact) |
| M13 assistant | `/assistant/messages` (SSE stream), `/assistant/confirm/{proposal}` |

## 10.6 External integrations

```mermaid
flowchart LR
    subgraph GB["GharBasket platform (Render, Vercel, Neon)"]
        API["api<br/>outbox handlers + jobs"]
        ML["ml service<br/>(host to be decided)"]
    end
    MOB["Mobile app<br/>(Capacitor)"] --> FBA["Firebase Auth<br/>phone OTP"]
    MOB --> API
    API --> FBA
    API --> FCM["Firebase Cloud Messaging<br/>push"]
    API --> WA["WhatsApp Cloud API<br/>templates (test number)"]
    API --> RZ["Razorpay<br/>Checkout, UPI Autopay, Route"]
    RZ -->|"signed webhooks"| API
    ML --> OSRM["OSRM (self-hosted)<br/>distance / time matrix"]
    MOB --> TILES["Map tiles<br/>OpenFreeMap / MapTiler"]
    API --> S3["Cloudflare R2 (S3 API)<br/>media, KYC (private), lake"]
    MOB -->|"presigned upload"| S3
    ML --> CL["Claude API<br/>assistant, triage"]
    API --> OTEL["OpenTelemetry<br/>Grafana Cloud"]
    MOB --> PH["PostHog<br/>events, flags, A/B"]
```
*Figure 10.3 — Integration map*

| Integration | Purpose | Prototype mode | Notes |
|---|---|---|---|
| Firebase Auth | Phone OTP | Free tier (test numbers) | Avoids TRAI DLT SMS registration in the prototype; ID tokens verified with PyJWT; real OTPs need the Blaze plan at the pilot |
| FCM | Push notifications | Free | Device tokens in `notify` |
| WhatsApp Cloud API | Order/box notifications | Test number | Template messages only |
| Razorpay | Top-ups, UPI Autopay, partner payouts (Route) | Test mode | Our own thin async client on httpx2 (not the official SDK); webhook signatures verified; hourly reconciliation; UPI Autopay from the pilot phase |
| OSRM | Road distance/time matrix | Self-hosted (MMR extract) | No Google Maps bills |
| Map tiles | Map display | OpenFreeMap / MapTiler free | MapLibre GL |
| Claude API | Assistant, complaint triage | Pay-as-you-go, capped | Model chosen after S8 eval |
| Cloudflare R2 (S3 API) | Media, KYC docs, data lake, MLflow artifacts | Free tier (`gb-staging-*` buckets); SeaweedFS locally | Private bucket + signed reads for KYC; code uses the S3 API only |

## Decisions & open questions

- **D3** — FastAPI modular monolith; OpenAPI as the contract ([ADR-0003](../adr/0003-python-fastapi-backend-and-ml-service.md) in [`docs/adr/`](../adr/)).
- **D4** — Closed-loop wallet + UPI Autopay; KYC only for people we pay ([ADR-0004](../adr/0004-closed-loop-wallet-and-upi-autopay.md)).
- **D20** — Free-tier hosting: Render, Vercel, Neon, Cloudflare R2, Infisical ([ADR-0020](../adr/0020-free-tier-hosting-render-vercel-neon-r2.md), supersedes ADR-0011 and ADR-0015).
- **D21** — Jobs without a broker: Postgres outbox and internal job endpoints ([ADR-0021](../adr/0021-jobs-without-a-broker.md)).
- **D22** — Generated API client: openapi-typescript + openapi-fetch + openapi-react-query ([ADR-0022](../adr/0022-api-client-openapi-fetch.md)).
- **D24** — Sign-in: Firebase phone OTP, then our own tokens; cookies and CSRF checks for browsers ([ADR-0024](../adr/0024-sign-in-firebase-otp-own-tokens.md)).
- **Open:** confirm Razorpay Checkout behaviour inside the Capacitor WebView and Firebase phone auth on Capacitor (Sprint 0 spikes).

Related: [Chapter 06 — Domain lifecycles](06-domain-lifecycles.md) · [Chapter 07 — Workflows](07-workflows.md) · [Chapter 13 — Security and compliance](13-security-and-compliance.md)

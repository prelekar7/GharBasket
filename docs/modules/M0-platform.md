# M0 — Platform

**Status:** Draft v1; §3.1 (file uploads) frozen on 2026-10-08
**Owner:** Prathamesh Relekar (backend: API platform, auth plumbing, infra, ML skeleton, simulator), Yash Salunkhe (screens: frontend shells, UI tokens) · **QA:** Rhythm Thakur, Manish Raje
**Phase / Sprint:** P1 / S0
**Postgres schema:** `platform` · **Python package:** `gharbasket.platform`

## 1. Purpose
Shared foundations every module depends on: application factory, configuration, database sessions, authentication middleware, permission dependency, error handling (RFC 9457), idempotency, transactional outbox and relay, file presigning, health checks, observability, and the monorepo/CI tooling. M0 contains no business logic.

## 2. User stories
| # | Story | Acceptance criteria |
|---|---|---|
| US-1 | As a developer, I want `just up && just dev` to start the whole stack locally so that I can work without setup friction. | Fresh clone → services healthy within 5 minutes; README documents prerequisites. |
| US-2 | As a developer, I want module boundaries enforced automatically so that we do not couple modules by accident. | A cross-module import outside `<module>.api` fails `lint-imports` in CI. |
| US-3 | As a developer, I want a single migration head so that parallel PRs do not create conflicting migrations. | CI fails when `alembic heads` returns more than one head. |
| US-4 | As a client developer, I want a generated, typed API client so that I never hand-write HTTP calls. | `just gen-api` regenerates `frontend/packages/api-client`; CI fails if the committed client is stale. |
| US-5 | As a backend developer, I want an outbox so that events are never lost when a transaction commits. | Event row written in the same transaction; relay dispatches with `FOR UPDATE SKIP LOCKED`; duplicate delivery is harmless. |
| US-6 | As an API client, I want consistent error responses so that the UI can show helpful messages. | All errors are `application/problem+json` with a namespaced `code`. |
| US-7 | As an API client, I want safe retries of POST requests so that network glitches do not double-charge. | Same `Idempotency-Key` + same body returns the stored response; different body returns 422. |
| US-8 | As an operator, I want health and readiness endpoints so that deployments can be verified. | `/health` (liveness) and `/ready` (DB + Valkey reachable) respond within 200 ms. |

## 3. API endpoints
| Method | Path | Permission / role | Idempotency | Notes |
|---|---|---|---|---|
| GET | `/v1/health` | Public | — | Liveness |
| GET | `/v1/ready` | Public locally; `JOBS_TOKEN` on staging and prod | — | DB and Valkey checks |
| GET | `/v1/internal/jobs` | `JOBS_TOKEN` (open locally) | — | The registered jobs |
| POST | `/v1/internal/jobs/{name}` | `JOBS_TOKEN` (open locally) | — | Runs a job now; body `{ "scheduled_for": "<ISO>" }` (default now); 200 with the run, 404 `platform.job_unknown`, 500 `platform.job_failed` with `run_id`; a second trigger while one runs answers `status: "skipped"` |
| POST | `/v1/files/presign` | Authenticated | Not required | A presigned PUT URL for one private upload (S3 API; Cloudflare R2 on staging, SeaweedFS locally) and its key; purposes, limits and retention in §3.1 |

### 3.1 File upload contract (frozen on 2026-10-08)
Order-issue photos (M4 §3.2), donation photos (M9) and KYC documents (M8) upload straight to object storage; the API never streams file bytes and stores only keys. Changing this needs a PR reviewed by both Prathamesh and Yash.

```
POST /v1/files/presign             signed in; no Idempotency-Key (it changes nothing a retry could repeat)
  request : { "purpose": "order_issue_photo" | "donation_photo" | "kyc_document"
                         | "delivery_photo" | "handoff_photo",
              "content_type": "image/jpeg", "size_bytes": 834221 }
  200     : { "key": "order_issue_photo/<user_id>/<uuidv7>.jpg",
              "url": "<presigned PUT URL>", "method": "PUT",
              "headers": { "Content-Type": "image/jpeg", "Content-Length": "834221" },   send exactly these
              "expires_at": "2026-10-08T10:10:00Z" }                                    10 minutes
  The app then PUTs the bytes to "url" and sends only "key" to the module that uses it.
```

Purposes, limits and retention below were confirmed by the owner on 2026-10-08.

| Purpose | Types | Max size | Used by | Kept until (`delete_after`) |
|---|---|---|---|---|
| `order_issue_photo` | `image/jpeg`, `image/png`, `image/webp` | 5 MB | M4 issues | 90 days after the issue is resolved |
| `donation_photo` | `image/jpeg`, `image/png`, `image/webp` | 5 MB | M9 donations | 90 days after the pickup |
| `kyc_document` | `image/jpeg`, `image/png`, `application/pdf` | 10 MB | M8 KYC | 30 days after the application is rejected or the partner leaves; never an Aadhaar card (handbook 13) |
| `delivery_photo` | `image/jpeg`, `image/webp` | 5 MB | M7 handover and door proof (M7 §3.4) | 90 days after the delivery |
| `handoff_photo` | `image/jpeg`, `image/png`, `image/webp` | 5 MB | M9 NGO hand-offs (M9 §3.5) | 3 years (impact proof); never beneficiaries' faces |
| `data_export` (server only, not presignable) | `application/json` | — | M1 export | 7 days |
| `income_certificate` (server only, not presignable) | `application/pdf` | — | M8 income certificate (M8 §3.7) | 1 day |

**Rules**
- **Keys** are `<purpose>/<user_id>/<uuidv7>.<ext>`, generated by the server. Every purpose goes to the **private bucket** (`S3_BUCKET_PRIVATE`, `gb-private`); nothing is publicly readable.
- **Size and type** are signed into the URL (`Content-Type`, `Content-Length`), and the module that receives the key checks the stored object with `platform.files.head` before accepting it.
- **Claiming.** Each presign writes a `platform.uploads` row. The owning module calls `platform.files.claim(session, key, user_id=…, purpose=…)` in its own transaction (only the uploader's own key, of the expected purpose) and later `set_delete_after(key, at)`. `sweep_uploads` deletes objects never claimed within 24 h, and claimed ones past `delete_after`. Deleting an account (M1 purge) deletes everything under `*/<user_id>/`.
- **Server-written objects** (`data_export`): the module records the key with `platform.files.record(session, key, user_id=…, purpose=…, content_type=…, size_bytes=…, by=…, delete_after=…)`, claimed at once, then writes the bytes with `storage.put(key, body, content_type, content_disposition=None)`; a retried write records and overwrites the same key. A `content_disposition` such as `attachment; filename="x.pdf"` is stored with the object, so a signed link names the file that is downloaded (the `income_certificate`).
- **Reading back.** Only through 5-minute signed GET URLs (`platform.files.signed_get(key)`), minted by the owning module after its own permission check: the owner and hub staff for issue photos, admins for KYC (each KYC read is audit-logged). No module hands out a key to anyone else.
- **Environments.** Same S3 API everywhere: SeaweedFS locally (`just up` with the `storage` profile, `S3_ENDPOINT_URL=http://localhost:8333`), Cloudflare R2 on staging. Presigned URLs use `S3_PUBLIC_ENDPOINT_URL` (default `S3_ENDPOINT_URL`; `http://10.0.2.2:8333` for the Android emulator). R2's CORS allows `PUT` from `WEB_ORIGINS` and the Capacitor origins.
- **Rate limit:** 30 presigns per 10 minutes per user.

**Error codes**

| Status | Code | When |
|---|---|---|
| 422 | `platform.upload_purpose_invalid` | Unknown purpose, or a server-written one (`data_export`, `income_certificate`) |
| 422 | `platform.upload_type_not_allowed` | A content type the purpose does not take; the problem carries `"allowed"` |
| 422 | `platform.upload_too_large` | `size_bytes` over the purpose's limit (or not positive); the problem carries `"max_bytes"` |
| 429 | `platform.upload_rate_limited` | Over the limit; `Retry-After` |
| 503 | `platform.storage_unavailable` | Object storage is not configured or not answering |

**For the mobile mocks** (`mocks/kindness.js` → contract)

| Mock today | Contract |
|---|---|
| `{ content_type }` | plus `purpose` and `size_bytes` |
| `{ key: "donations/<uuid>.jpg", url, method }` | `key` is `donation_photo/<user_id>/<uuidv7>.jpg`; plus `headers` (send them on the PUT) and `expires_at` |

## 4. Tables owned
| Table | Key columns | Notes |
|---|---|---|
| `platform.outbox` | id, event_type, payload jsonb, created_at, sent_at, attempts | Relay reads unsent rows in order |
| `platform.idempotency_keys` | key, user_id, request_hash, response jsonb, status_code, created_at | 24 h TTL cleanup job |
| `platform.job_runs` | id, name, scheduled_for, started_at, finished_at, status (ok, failed, skipped), summary jsonb, error, request_id | One row per job run (never cleaned in v1) |
| `platform.audit_log` | id, actor_user_id, action, entity_type, entity_id, before jsonb, after jsonb, at | Admin actions |
| `platform.uploads` | key (pk), user_id, purpose, content_type, size_bytes, created_at, claimed_by (module), claimed_at, delete_after | One row per presign; the owning module claims the key when it saves it |
| `platform.feature_flags` (optional; PostHog is primary) | key, enabled, rules jsonb | Server-side kill switches (e.g. ML fallback) |

## 5. Events
**Published:** none of its own (provides the mechanism). **Consumed:** none.

## 6. Jobs
| Job | Schedule / trigger | Notes |
|---|---|---|
| `outbox_relay` | After each request that published (background task in the API) | Relays fresh outbox rows (`FOR UPDATE SKIP LOCKED`) to their consumers; a failing consumer is retried with backoff (30 s doubling, max 1 h) |
| `outbox_sweep` | Hourly from GitHub Actions; every few minutes from the external cron | Picks up rows the after-commit relay missed (restart, error); reports `sent`, `failed`, `pending` |
| `sweep_uploads` | Daily 04:13 IST (GitHub Actions) | Deletes objects never claimed within 24 h, and claimed ones past `delete_after` |
| `cleanup_idempotency_keys` | Daily 04:03 IST (GitHub Actions) | Deletes keys older than 24 h |
| Backups | — | Neon point-in-time restore; a scheduled `pg_dump` to R2 can be added via the jobs workflow |

Scheduled jobs are internal endpoints `POST /v1/internal/jobs/<name>`, protected by `JOBS_TOKEN`; each is idempotent, takes a Postgres advisory lock for its name and uses the request's `scheduled_for` rather than the clock. Triggers: the `jobs-staging.yml` workflow (daily and weekly jobs, and "Run workflow" by hand), an external HTTPS cron (cron-job.org) for minute-level timing such as the 21:00 cut-off, because a private repository has 2,000 free Actions minutes a month, and `just job <name>` locally. Modules register jobs with `@platform.jobs.job("name", "…")` and consumers with `platform.outbox.subscribe(event_type, handler)`, both only in their `jobs.py`; `gharbasket.modules.registry.load_modules()` imports every one, and the app factory and every tool that runs jobs or relays (`just job`, `just seed`, `just sim --write-db`) call it first. The relay refuses to run in a process that has not (it raises and leaves every event pending), because an event whose consumers were never imported would otherwise be marked sent without them. Taskiq workers replace the trigger at the pilot (ADR-0021).

## 7. Permissions
Provides the `require(permission, scope)` dependency and the permission registry that modules contribute to. `audit:read` → admin.

## 8. Screens
**Mobile:** app shell, providers, error boundary, offline banner, role switcher host (content by M1). **Console:** layout shell, sidebar, hub switcher, ⌘K palette, login page shell.

## 9. Tests required
- Unit: error handler, idempotency middleware (same key/same body, same key/different body).
- Integration: outbox write + after-commit relay + `outbox-sweep` job against local Docker Postgres; RLS session variable is set per transaction.
- CI checks: import-linter, single Alembic head, upgrade/downgrade round trip, generated client up to date, agent-docs sync, gitleaks.

## 10. Dependencies
None (foundation). All modules depend on M0.

## 11. Open questions
- How often the `outbox-sweep` schedule runs (GitHub Actions schedules run at most every 5 minutes and may start late).
- Whether server-side feature flags are needed beyond PostHog.

# M0 — Platform

**Status:** Draft v1; §3.1 (file uploads) frozen on 2026-10-08. The server-side PDF helper (`platform/pdf.py`) and the server-only `income_certificate` purpose are built with M8 §3.7.
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
| GET | `/v1/internal/jobs/runs` | `JOBS_TOKEN` (open locally) | — | Recent runs, newest first: `?name=&status=ok\|failed\|skipped&since=<ISO>&limit=` (default 50, at most 200); each a `JobRunOut` (id, name, status, scheduled_for, started_at, finished_at, summary, error). Added by ADR-0033 |
| POST | `/v1/internal/jobs/{name}` | `JOBS_TOKEN` (open locally) | — | Runs a job now; body `{ "scheduled_for": "<ISO>" }` (default now); 200 with the run, 404 `platform.job_unknown`, 500 `platform.job_failed` with `run_id` (and `problems` for `ops_check`); a second trigger while one runs answers `status: "skipped"` |
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
- **Size and type** are signed into the URL (`Content-Type`, `Content-Length`), and the module that receives the key checks the stored object with `storage.clean(key)` before accepting it.
- **Photo metadata** (added 7 Nov 2026). `clean` first rewrites a JPEG, PNG or WebP without its location (GPS) and other EXIF/XMP metadata, comments and trailing data such as motion-photo videos (`platform/image_metadata.py`). It changes only the container, not the pixels, and keeps a JPEG's Orientation tag. Bytes that are not the signed image type are refused (`None`, so the module answers its own `*_invalid` 422). The object then reports the signed size through its `uploaded-size` metadata, so a retried request still matches; cleaning again changes nothing. PDFs (`kyc_document`) are kept as uploaded. The apps should strip metadata before uploading as well; the server is the guarantee.
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
| `ops_check` | VM: 06:30, 12:30, 21:15, 23:45 IST (ADR-0033) | Runs every registered probe (below); `ok` with `{probes, problems: []}`, or `failed` with the problems joined in `error` and listed in its summary; its 500 answer carries them as `problems` (no other job's error text is sent back), so the scheduler sends them to the `ops` alert |
| Backups | — | Neon point-in-time restore; a scheduled `pg_dump` to R2 can be added via the jobs workflow |

Scheduled jobs are internal endpoints `POST /v1/internal/jobs/<name>`, protected by `JOBS_TOKEN`; each is idempotent, takes a Postgres advisory lock for its name and uses the request's `scheduled_for` rather than the clock. Triggers: the `jobs-staging.yml` workflow (daily and weekly jobs, and "Run workflow" by hand), a scheduler on the staging Oracle VM (`scripts/jobs_scheduler.py`, ADR-0032) for minute-level timing such as the 21:00 cut-off, because GitHub starts schedules late and at most every 5 minutes, and `just job <name>` locally. Modules register jobs with `@platform.jobs.job("name", "…")` and consumers with `platform.outbox.subscribe(event_type, handler)`, both only in their `jobs.py`; `gharbasket.modules.registry.load_modules()` imports every one, and the app factory and every tool that runs jobs or relays (`just job`, `just seed`, `just sim --write-db`) call it first. The relay refuses to run in a process that has not (it raises and leaves every event pending), because an event whose consumers were never imported would otherwise be marked sent without them. Taskiq workers replace the trigger at the pilot (ADR-0021).

### 6.1 Ops probes and alerts (frozen on 2026-11-10, ADR-0033)
- **Probes.** A module registers `@platform.ops.probe("<module>.<name>")` on an `async (session, now) -> list[str]` in its `jobs.py`; each string is one problem in a line ("billing.webhook_backlog: 3 webhooks unprocessed for > 15 min"). A probe reads only its own schema. A probe that raises counts as a problem (`<name>: probe failed <ExceptionType>`); the others still run.
- **Platform probes:**
  - `platform.failed_jobs`: a `failed` run in the last 24 h with no later `ok` run of the same job (it names the job and the first 120 characters of `error`);
  - `platform.jobs_with_failed_hubs`: an `ok` run in the last 24 h whose summary has a non-empty `failed_hubs` (the per-hub jobs `assign_kitchens`, `assignment_acceptance_timeout`, `refund_unassigned_tiffins` and `surplus_cascade` run each hub in its own savepoint and keep the others' work, so the run stays `ok`), with no later `ok` run of the same job that is clean (no `failed_hubs`); it names the job, the hub ids of the job's latest such run and its start time. A later `failed` or `skipped` run does not clear it;
  - `platform.jobs_with_failed_households`: the same for a non-empty `failed_households` (`generate_weekly_plans` commits each household's draft as it is made and lists the households whose draft failed; the key is omitted when none failed). It heals on its own key: a later `ok` run without `failed_households` clears it even if that run lists `failed_hubs`, and the reverse. It names the job, up to 10 household ids and the start time;
  - `platform.outbox_stuck`: an unsent event older than 30 min with 3 or more attempts;
  - `platform.database_size`: `pg_database_size(current_database())` above 80 % of `OPS_DB_LIMIT_MB` (default 512, Neon's free tier).
- **Module probes** (M5, M7): see those specs' job tables.
- **The scheduler's alerts** (`scripts/jobs_scheduler.py`):
  - every hour it pings `HC_PING_URL` (the `scheduler` check, unchanged);
  - a call that ends `failed` (any non-retried error answer, e.g. 500 `platform.job_failed`) or `gave_up` POSTs `"<job> <minute> IST <status> <the answer's first 1,000 characters, or why it gave up>"` to `HC_OPS_URL/fail`;
  - a schedule entry may set `heartbeat: <ENV NAME>`: when that job answers 2xx, the scheduler pings the URL in that variable. `cutoff_lock` has `heartbeat: HC_CUTOFF_URL` (a check with cron `0 21 * * *` IST, so a missed or late cut-off alerts) and `ops_check` has `heartbeat: HC_OPS_URL` (a clean watchdog brings the `ops` check back up);
  - unset URLs are skipped.
- **Actions.** `jobs-staging.yml` and `training-jobs.yml` POST the workflow and job name to `HC_OPS_URL/fail` when a job fails (skipped while the secret is unset).
- **Manual runs.** Runbooks always pass `scheduled_for` (the API's default is now, which after IST midnight is the next day for date-based jobs).

### 6.2 Tracing (drafted 2026-11-12, frozen on approval)
OpenTelemetry traces from the API and the ML service to Grafana Cloud (free tier), as chosen in handbook 04 and 12 §12.5. It is for finding slow and failing requests and jobs, and it gives ADR-0033 the error-rate alert it waits for. Traces only: logs stay structlog JSON on stdout (Render's log view), and no OpenTelemetry metrics or logs are exported in this step.

- **Off unless configured.** The exporter is on only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (OTLP over HTTP). Its auth header `OTEL_EXPORTER_OTLP_HEADERS` is a secret: Infisical, then Render. Tests and local runs export nothing, and a missing or unreachable collector never changes an answer or fails a job: spans go through a bounded batch queue, a full queue drops spans, and an export error is logged once a minute at most. `OTEL_SERVICE_NAME` is `gharbasket-api` or `gharbasket-ml`, and `deployment.environment` is `APP_ENV`. The resource also carries the deployed commit.
- **Sampling.** Parent-based, with a ratio from `OTEL_TRACES_SAMPLER_ARG`. The default is 1.0, which suits staging's traffic; the pilot sets it from the measured volume.
- **What becomes a span.**
  - Each HTTP request: the span name is the method and the route template (`GET /v1/orders/{order_id}`), with the status code and the problem `code` of an RFC 9457 answer.
  - Database calls: the SQL text with placeholders only.
  - Outgoing HTTP: the ML service, Razorpay, FCM, Firebase, WhatsApp and the S3 store, as method, host and status.
  - Each job run: `job <name>`, with `scheduled_for`, the status and the run id.
  - Each outbox event handled by a consumer: `consume <event_type>`, with the consumer's name and the attempt number.
  - API → ML calls carry the trace context (W3C `traceparent`), so one trace covers both services.
- **Never in a span or its events**, however the instrumentation defaults change:
  - request and response bodies;
  - any header value (`Authorization`, `Cookie`, `Set-Cookie`, `X-Razorpay-Signature`, `Idempotency-Key`, the operator and ML tokens);
  - query strings;
  - the raw URL path (it holds ids): only the route template;
  - SQL bind parameters (the KYC key is one, M8 §3.1);
  - phone numbers, names, addresses, user, household or partner ids, tokens, and KYC numbers.

  An exception is recorded by its type and the problem `code` only, not its message, which can carry values. A test asserts these rules on the spans of a request that carries every one of them.
- **Logs ↔ traces.** Every log line written inside a span carries `trace_id` and `span_id`, and the request span carries `gb.request_id`, so a Render log line leads to its trace and back.
- **The error-rate alert** (ADR-0033's follow-up) is set up in Grafana Cloud by the owner. It fires on the share of request spans with a 5xx status over 15 minutes, and it notifies the same Telegram group and email. Its rule is written down in handbook 12 §12.5 when it is made.
- **The ML service** gets the same setup (FastAPI and its outgoing calls), with the same rules.

**As built (tracing, 2026-11-12).**
- **Packages.** `opentelemetry-api`, `-sdk` and `-exporter-otlp-proto-http` only, in both services. No contrib instrumentation: the httpx one supports `httpx2` (`HTTPX2ClientInstrumentor`, 0.66b2), but it and the ASGI/FastAPI one record the full URL, the raw path or the query string, which the never-list forbids, and the rules should not depend on any instrumentation's defaults. So the spans are built by hand in `platform/tracing.py` (API) and `gharbasket_ml/tracing.py`.
- **FastAPI's own telemetry is off.** FastAPI 0.142 ships native OpenTelemetry that, once `OTEL_EXPORTER_OTLP_ENDPOINT` is set, adds OTLP exporters for traces, metrics and logs (with exception messages) to the global providers on its own. Both apps pass `telemetry=FASTAPI_TELEMETRY_OFF`, and a test checks that the environment variable alone configures nothing.
- **Requests.** `RequestContextMiddleware` opens the server span; its name becomes `<METHOD> <route template>` once routing has run (`scope["route"].path`), else just the method (a 404). Attributes: `http.request.method`, `http.route`, `http.response.status_code`, `gb.request_id`, `gb.problem_code` (set in `errors.problem_response`), `error.type` (the exception class). An unhandled exception reaches Starlette's outermost `ServerErrorMiddleware`, outside the span, so the span records `error.type` and `gb.problem_code: server.error` itself; that handler's `unhandled_error` log line is written outside the span and has no trace id.
- **Database.** SQLAlchemy cursor events on every engine `db.create_engine` makes: one span per statement, named by its first keyword, with `db.query.text`. Quoted strings and bare numbers in the text are replaced by `?` as well, so a literal written into SQL never reaches a span; parameters are never read.
- **Outgoing HTTP.** Every client the API makes comes from `tracing.http_client()` (the shared app client and the modules' short-lived ones), whose transport opens a span `<METHOD> <host>` with the method, host (`server.address`) and status. `traceparent` is added only for the host of `ML_SERVICE_URL`. The S3 store is boto3, traced by its `before-call`/`after-call` events with the same attributes. In the ML service: OSRM, the R2 registry (sync client) and Anthropic (the SDK is given a traced `httpx2` client); none gets the trace context.
- **Jobs and consumers.** `job <name>` with `gb.job.scheduled_for`, `gb.job.status`, `gb.job.run_id` (and `error.type` on a failure); `consume <event_type>` with `gb.consumer` (`module.function`) and `gb.attempt`.
- **Logs.** The structlog processor `add_trace_ids` (API) and `TraceFormatter` (ML, its WARNING lines on stderr, unless the host has configured the root logger) add the ids.
- **Export.** `BatchSpanProcessor` with a 2048-span queue (a full queue drops); the OTLP exporter's endpoint is `OTEL_EXPORTER_OTLP_ENDPOINT` + `/v1/traces`, its own error lines are silenced, and a failed export is logged as `trace_export_failed` at most once a minute. The resource: `service.name` (`OTEL_SERVICE_NAME`), `service.version` and `vcs.ref.head.revision` (the commit, `RENDER_GIT_COMMIT`), `deployment.environment` (`APP_ENV`; the ML service runs with `APP_ENV=prod` on Render, so its spans say `prod`).

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

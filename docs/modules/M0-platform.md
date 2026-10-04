# M0 — Platform

**Status:** Draft v1 — refine at phase start (spec-first)
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
| POST | `/v1/files/presign` | Authenticated | Not required | Returns a presigned PUT URL (S3 API; Cloudflare R2 on staging, SeaweedFS locally) + object key; bucket chosen by purpose (media, kyc) |

## 4. Tables owned
| Table | Key columns | Notes |
|---|---|---|
| `platform.outbox` | id, event_type, payload jsonb, created_at, sent_at, attempts | Relay reads unsent rows in order |
| `platform.idempotency_keys` | key, user_id, request_hash, response jsonb, status_code, created_at | 24 h TTL cleanup job |
| `platform.job_runs` | id, name, scheduled_for, started_at, finished_at, status (ok, failed, skipped), summary jsonb, error, request_id | One row per job run (never cleaned in v1) |
| `platform.audit_log` | id, actor_user_id, action, entity_type, entity_id, before jsonb, after jsonb, at | Admin actions |
| `platform.feature_flags` (optional; PostHog is primary) | key, enabled, rules jsonb | Server-side kill switches (e.g. ML fallback) |

## 5. Events
**Published:** none of its own (provides the mechanism). **Consumed:** none.

## 6. Jobs
| Job | Schedule / trigger | Notes |
|---|---|---|
| `outbox_relay` | After each request that published (background task in the API) | Relays fresh outbox rows (`FOR UPDATE SKIP LOCKED`) to their consumers; a failing consumer is retried with backoff (30 s doubling, max 1 h) |
| `outbox_sweep` | Hourly from GitHub Actions; every few minutes from the external cron | Picks up rows the after-commit relay missed (restart, error); reports `sent`, `failed`, `pending` |
| `cleanup_idempotency_keys` | Daily 04:03 IST (GitHub Actions) | Deletes keys older than 24 h |
| Backups | — | Neon point-in-time restore; a scheduled `pg_dump` to R2 can be added via the jobs workflow |

Scheduled jobs are internal endpoints `POST /v1/internal/jobs/<name>`, protected by `JOBS_TOKEN`; each is idempotent, takes a Postgres advisory lock for its name and uses the request's `scheduled_for` rather than the clock. Triggers: the `jobs-staging.yml` workflow (daily and weekly jobs, and "Run workflow" by hand), an external HTTPS cron (cron-job.org) for minute-level timing such as the 21:00 cut-off, because a private repository has 2,000 free Actions minutes a month, and `just job <name>` locally. Modules register jobs with `@platform.jobs.job("name", "…")` in their `jobs.py`, imported by their router, and consumers with `platform.outbox.subscribe(event_type, handler)`. Taskiq workers replace the trigger at the pilot (ADR-0021).

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

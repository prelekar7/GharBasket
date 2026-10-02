# M0 — Platform

**Status:** Draft v1 — refine at phase start (spec-first)
**Owner:** Yash Salunkhe (frontend shells, auth plumbing, UI tokens) and Prathamesh Relekar (API platform, infra, ML skeleton, simulator) · **QA:** Rhythm Thakur, Manish Raje
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
| US-4 | As a client developer, I want a generated, typed API client so that I never hand-write HTTP calls. | `just gen-api` regenerates `packages/api-client`; CI fails if the committed client is stale. |
| US-5 | As a backend developer, I want an outbox so that events are never lost when a transaction commits. | Event row written in the same transaction; relay dispatches with `FOR UPDATE SKIP LOCKED`; duplicate delivery is harmless. |
| US-6 | As an API client, I want consistent error responses so that the UI can show helpful messages. | All errors are `application/problem+json` with a namespaced `code`. |
| US-7 | As an API client, I want safe retries of POST requests so that network glitches do not double-charge. | Same `Idempotency-Key` + same body returns the stored response; different body returns 422. |
| US-8 | As an operator, I want health and readiness endpoints so that deployments can be verified. | `/health` (liveness) and `/ready` (DB + Valkey reachable) respond within 200 ms. |

## 3. API endpoints
| Method | Path | Permission / role | Idempotency | Notes |
|---|---|---|---|---|
| GET | `/v1/health` | Public | — | Liveness |
| GET | `/v1/ready` | Public | — | DB and Valkey checks |
| POST | `/v1/files/presign` | Authenticated | Not required | Returns presigned S3 PUT URL + object key; bucket chosen by purpose (media, kyc) |

## 4. Tables owned
| Table | Key columns | Notes |
|---|---|---|
| `platform.outbox` | id, event_type, payload jsonb, created_at, sent_at, attempts | Relay reads unsent rows in order |
| `platform.idempotency_keys` | key, user_id, request_hash, response jsonb, status_code, created_at | 24 h TTL cleanup job |
| `platform.audit_log` | id, actor_user_id, action, entity_type, entity_id, before jsonb, after jsonb, at | Admin actions |
| `platform.feature_flags` (optional; PostHog is primary) | key, enabled, rules jsonb | Server-side kill switches (e.g. ML fallback) |

## 5. Events
**Published:** none of its own (provides the mechanism). **Consumed:** none.

## 6. Jobs
| Job | Schedule / trigger | Notes |
|---|---|---|
| `outbox_relay` | Continuous (poll every 1 s) | Dispatches to Taskiq handlers |
| `cleanup_idempotency_keys` | Daily 03:30 | Deletes keys older than 24 h |
| `db_backup` | Daily 01:30 | `pg_dump` → S3, 30-day retention |

## 7. Permissions
Provides the `require(permission, scope)` dependency and the permission registry that modules contribute to. `audit:read` → admin.

## 8. Screens
**Mobile:** app shell, providers, error boundary, offline banner, role switcher host (content by M1). **Console:** layout shell, sidebar, hub switcher, ⌘K palette, login page shell.

## 9. Tests required
- Unit: error handler, idempotency middleware (same key/same body, same key/different body).
- Integration: outbox write + relay dispatch with testcontainers; RLS session variable is set per transaction.
- CI checks: import-linter, single Alembic head, upgrade/downgrade round trip, generated client up to date, agent-docs sync, gitleaks.

## 10. Dependencies
None (foundation). All modules depend on M0.

## 11. Open questions
- Relay via polling vs Postgres `LISTEN/NOTIFY` wake-up (start with polling).
- Whether server-side feature flags are needed beyond PostHog.

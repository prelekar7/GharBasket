# ADR-0014 — Final technology stack and service-based architecture (not microservices)

- **Status:** Accepted; amended by [ADR-0018](0018-javascript-not-typescript-frontend.md) (JavaScript), [ADR-0020](0020-free-tier-hosting-render-vercel-neon-r2.md) (hosting), [ADR-0021](0021-jobs-without-a-broker.md) (jobs) and [ADR-0022](0022-api-client-openapi-fetch.md) (API client)
- **Date:** 2026-10-02
- **Decision ID:** D14

## Context
Earlier documents proposed 18 microservices with Kafka and Kubernetes. The team asked whether GharBasket should use microservices. The core money and order flows need **atomic transactions across modules** (wallet debit + order lock + stock reservation). The team is four students with about six months.

## Decision
**Architecture: service-based** — one modular monolith plus a few services separated for real technical reasons:

| Deployable | Reason for separation |
|---|---|
| `api` (FastAPI, 11 modules) | All transactional business logic in one database transaction scope |
| `worker` (Taskiq, same code) | Background jobs must not slow requests |
| `ml` + `ml-worker` | Heavy dependencies (torch, OR-Tools, LightGBM), long CPU jobs, independent failure |
| `console` (Next.js), `mobile` (static bundle) | Frontends |
| Postgres, Valkey, MLflow, OSRM, Caddy | Off-the-shelf infrastructure |

It remains **microservice-ready**: each module owns its own schema, exposes only `api.py`, communicates via transactional-outbox events, and import-linter enforces boundaries in CI. Scale path for the report: extract services → event bus (Redis Streams → Kafka) → per-city database shards.

**Final stack:** React 19 + Vite + TanStack Router → PWA + Capacitor 8 (mobile); Next.js 16 (console and public site); FastAPI on Python 3.13 (api); separate FastAPI ML service with OR-Tools, StatsForecast/MLForecast, LightGBM, NumPyro, Chronos-2 and MLflow; Taskiq + Valkey; PostgreSQL 18 + PostGIS + pgvector + pg_trgm; S3; DuckDB; OpenAPI → Hey API clients; Claude API; Firebase Auth/FCM; Razorpay; MapLibre + OSRM; GitHub Actions; Sentry, OpenTelemetry → Grafana Cloud, PostHog.

## Alternatives considered
- **Full microservices (orders, billing, logistics, notify…)** — rejected: distributed transactions become sagas, and CI, deployment, monitoring and local setup multiply per service with no benefit at our scale.
- **Single process including ML** — rejected (see ADR-0003).

## Consequences
- (+) Simple to run locally (`just up`), easy to reason about, still defensible as scalable.
- (−) Boundary discipline relies on tooling (import-linter, CODEOWNERS) rather than network separation.
- Any later change to the stack requires a new ADR.

## Related
[ADR-0002](0002-react-capacitor-mobile-nextjs-console.md) · [ADR-0003](0003-python-fastapi-backend-and-ml-service.md) · [ADR-0006](0006-postgresql-centric-data-platform.md) · Handbook [Chapter 04](../handbook/04-system-architecture.md)

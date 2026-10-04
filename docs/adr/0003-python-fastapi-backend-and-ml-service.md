# ADR-0003 — Python-only backend: FastAPI modular monolith, ML service and Taskiq workers

- **Status:** Accepted; Taskiq workers deferred to the pilot by [ADR-0021](0021-jobs-without-a-broker.md)
- **Date:** 2026-10-02
- **Decision ID:** D3

## Context
Earlier documents mixed FastAPI and NestJS and described 18 microservices with RabbitMQ/Kafka. The project's differentiator is AI and optimisation (OR-Tools, LightGBM, StatsForecast, NumPyro, Chronos-2), whose ecosystem is Python. Two backend languages would split the team and duplicate models, validation and tooling.

## Decision
- **services/api:** FastAPI (Python 3.13), Pydantic v2, SQLAlchemy 2.0 async + Alembic, organised as a **modular monolith** of 11 modules, each with its own Postgres schema and a public `api.py` facade.
- **services/ml:** a separate FastAPI service for planners, forecasts, routing, assignment and LLM calls, with heavy dependencies isolated.
- **Workers:** Taskiq (Valkey broker + scheduler) for the api (`worker`) and the ML service (`ml-worker`).
- **Contract:** FastAPI's OpenAPI document generates the TypeScript client (Hey API) used by both frontends.
- Module boundaries enforced by import-linter in CI.

## Alternatives considered
- **NestJS (TypeScript) backend** — rejected: second backend language; ML would still need Python.
- **Django** — rejected: synchronous-first heritage and a heavier ORM; its admin is not needed because we build a console.
- **Celery for jobs** — rejected in favour of async-native Taskiq.
- **ML inside the api process** — rejected: torch/OR-Tools dependencies and long CPU jobs would slow or crash order handling.

## Consequences
- (+) One language for business logic and AI; automatic OpenAPI keeps frontend and backend in sync.
- (+) ML failures never block orders (timeouts and rule-based fallbacks).
- (−) Async SQLAlchemy has a learning curve; patterns are documented in module specs.
- (−) Two Python services to version and deploy (shared uv workspace mitigates this).

## Related
[ADR-0014](0014-final-stack-and-service-based-architecture.md) · [ADR-0006](0006-postgresql-centric-data-platform.md) · Handbook [Chapter 04](../handbook/04-system-architecture.md)

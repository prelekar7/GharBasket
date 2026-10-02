# ADR-0006 — PostgreSQL-centric data platform (no MongoDB, Firebase DB or Elasticsearch)

- **Status:** Accepted
- **Date:** 2026-10-02
- **Decision ID:** D6

## Context
The team asked whether to use SQL or NoSQL (MongoDB, Firebase). GharBasket's data is mostly relational and transactional: wallet top-ups, debits, refunds and partner earnings (atomic, auditable); orders and inventory under concurrency (two customers must not take the last kilogram); deeply connected entities (user → household → address → society → hub → route → rider); geo queries; semi-structured attributes (nutrition, recipe steps, KYC metadata); Hinglish fuzzy search; vector similarity for substitutes; time-ordered events; and ML training over history. Four developers also need an enforced schema so data shapes do not drift.

## Decision
- **PostgreSQL 18** is the single source of truth, with extensions **PostGIS** (geo), **pgvector** (similarity), **pg_trgm + unaccent + full-text search** (Hinglish search), **JSONB** for flexible fields and **Row-Level Security** for franchise isolation. `uuidv7()` keys; money in integer paise; double-entry ledger; **one schema per module**.
- **Valkey** for cache, OTP throttles, sessions, the Taskiq queue and live rider locations.
- **S3-compatible object storage** for photos, KYC documents, invoices and the Parquet data lake (MinIO locally).
- **DuckDB + Parquet** for ML training and heavy analytics.
- Substitution graph as Postgres tables with recursive queries; events in partitioned tables.

## Alternatives considered
- **MongoDB** — rejected: multi-document transactions are slower and easy to misuse for money; joins via `$lookup` are clumsy for reports; a schema-less store lets four developers store the same field differently.
- **Firebase Firestore** — rejected: no joins, limited transactions, pay-per-read billing that grows with dashboards, strong vendor lock-in.
- **MySQL** — rejected: weaker JSON, no PostGIS/pgvector equivalents, no RLS.
- **Postgres + MongoDB** — rejected: two databases to keep consistent and two query languages.
- **Elasticsearch / Neo4j / Pinecone** — rejected: unnecessary operational load at our scale (< 5,000 products, < 100,000 vectors).
- **Backend-as-a-service (Supabase/Firebase as the backend)** — rejected: overlaps with our FastAPI backend and limits the ops/AI logic we own.

## Consequences
- (+) ACID safety for money and orders, SQL reporting, geo and vector search in one engine.
- (+) The schema and migrations act as a contract between teammates.
- (−) Requires disciplined migrations (one per PR, single Alembic head in CI).
- (−) Postgres extensions require a custom Docker image locally (PostGIS + pgvector).

## Related
[ADR-0015](0015-aws-mumbai-topology-with-neon-postgres.md) · [ADR-0004](0004-closed-loop-wallet-and-upi-autopay.md) · Handbook [Chapter 05](../handbook/05-data-architecture.md)

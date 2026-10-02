# ADR-0015 — AWS Mumbai topology with Neon Postgres and S3

- **Status:** Accepted
- **Date:** 2026-10-02
- **Decision ID:** D15

## Context
ADR-0011 chose AWS. The team also wanted shared development databases that do not collide: Neon offers a database branch (copy) per pull request. Neon does not appear to offer an India region; the nearest is likely Singapore (about 60 ms per round trip — to be verified).

## Decision
- **AWS Mumbai (ap-south-1):** one EC2 or Lightsail instance (about 2–4 vCPU, 8 GB) running Docker Compose: api, worker, ml, ml-worker, MLflow, OSRM, Valkey, Caddy, console.
- **Neon** for Postgres: `main` (pilot), `staging`, and an automatic branch per pull request. Everyone also runs Postgres locally in Docker.
- **S3** for media, the Parquet data lake and MLflow artifacts (code uses the S3 API; MinIO locally).
- Images in GHCR (ECR optional); secrets in SSM Parameter Store; nightly `pg_dump` to S3; DNS via Cloudflare or Route 53.
- **Before Phase P6 (pilot):** decide whether to keep Neon (mitigate latency by batching queries and pooling) or move only the production database to Postgres in Mumbai (EC2 or RDS) while keeping Neon for development branches.

## Alternatives considered
- **AWS RDS for everything** — rejected for now: uses credits faster and loses per-PR branches.
- **Supabase** — not chosen; no per-PR branching on the free tier.
- **Local Docker + one shared VPS database** — rejected: teammates would share one staging database.

## Consequences
- (+) Safe parallel development; low cost.
- (−) Cross-region latency for the database (open question Q3).

## Related
[ADR-0011](0011-hosting-on-aws-student-credits.md) · [ADR-0006](0006-postgresql-centric-data-platform.md) · Handbook [Chapter 12](../handbook/12-deployment-and-operations.md)

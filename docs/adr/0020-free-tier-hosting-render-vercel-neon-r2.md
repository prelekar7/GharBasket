# ADR-0020 — Hosting on free tiers: Render, Vercel, Neon, Cloudflare R2, Infisical

- **Status:** Accepted (supersedes ADR-0011 and ADR-0015)
- **Date:** 2026-10-04 (decided 2–3 October 2026)
- **Decision ID:** D20

## Context
ADR-0011 and ADR-0015 planned one AWS EC2 instance in Mumbai (Docker Compose, Dokploy, Caddy, S3, SSM, GHCR) paid for by student credits. AWS needs a payment card on the account and can bill beyond the credits if something is misconfigured or left running. For a student team that risk is not acceptable. The services below have free tiers that cannot bill us at all.

## Decision
| Piece | Where | Notes |
|---|---|---|
| API (`backend/api`) | **Render** free web service, Singapore | Docker image built by Render from the repo. Free services sleep after 15 minutes idle, so a free uptime monitor (UptimeRobot) calls `/v1/health` every 10 minutes. 24×7 fits the 750 free hours a month. `/v1/health` never touches the database, so Neon can still go idle. |
| Valkey | **Render Free Key Value** (same region, private network) | In-memory and lost on restart. It only holds short-lived data (rate-limit counters, the sign-out deny-list), and the API keeps working without it. |
| Postgres 18 | **Neon** (Singapore) | One shared database for the Infisical `dev` and `staging` environments. Guardrails: migrations reach it only from the deploy workflow after merge (`GB_ALLOW_REMOTE_MIGRATIONS=1`); downgrades on it are refused; tests and `just seed` refuse any non-local database; synthetic rows carry `data_source='synthetic'`. Day-to-day work uses local Docker. |
| Files | **Cloudflare R2** (S3 API, region `auto`) | `gb-staging-*` buckets; SeaweedFS locally. Code uses the S3 API only. |
| PWA and console | **Vercel** | Same-origin `/v1/*` rewrites to the API, so browsers use cookies without CORS (ADR-0024); preview deployments per PR. |
| Secrets | **Infisical** (`dev`, `staging`, `prod`) | `infisical run --env=…`; synced to GitHub Actions secrets. Never in the repo. |
| Product analytics | **PostHog** | No names or phone numbers in events. Sentry is not used. |
| Migrations and deploys | **GitHub Actions** | After CI passes on `main`: `alembic upgrade head` on Neon, then the Render deploy hook. Render's pre-deploy step is paid-only. |
| Scheduled jobs | GitHub Actions schedule | See ADR-0021. |

- **Free tiers only until the pilot (P6).** Before the pilot we decide on paid plans (Render Starter for the API, a worker, the Firebase Blaze plan for real OTPs) and on where the pilot database lives (open question Q3).
- **Hosting for the ML service, OSRM and MLflow:** decided in [ADR-0025](0025-ml-runtime-spaces-actions-r2.md) (Hugging Face Spaces, GitHub Actions, R2; MLflow local only). Locally they run in Docker Compose.

## Alternatives considered
- **AWS on student credits** (ADR-0011/0015): rejected because of card and billing risk; it also needs a server to patch and run.
- **One VPS with Dokploy:** rejected. It costs money every month and still needs server care.

## Consequences
- (+) No bill possible during development; no server to maintain; Vercel previews per PR.
- (−) The free API sleeps (handled by the keep-warm monitor) and Key Value is not durable (only short-lived data goes there).
- (−) Neon is in Singapore (about 60 ms from Mumbai): batch queries and use pooling (risk R11, question Q3).
- (−) Background workers are not free, so jobs run without a broker (ADR-0021).
- Handbook chapters 04, 11, 12 and 13 and TDD §3.7 and §10 now describe this setup.

## Related
[ADR-0011](0011-hosting-on-aws-student-credits.md) · [ADR-0015](0015-aws-mumbai-topology-with-neon-postgres.md) · [ADR-0021](0021-jobs-without-a-broker.md) · [ADR-0024](0024-sign-in-firebase-otp-own-tokens.md) · Handbook [Chapter 12](../handbook/12-deployment-and-operations.md)

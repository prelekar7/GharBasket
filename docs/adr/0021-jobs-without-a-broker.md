# ADR-0021 — Background jobs without a broker until the pilot

- **Status:** Accepted (refines ADR-0003 and ADR-0014: Taskiq is deferred, not dropped)
- **Date:** 2026-10-04 (decided 3 October 2026)
- **Decision ID:** D21

## Context
ADR-0003 and ADR-0014 planned Taskiq workers on Valkey for events and scheduled jobs: the outbox relay, the 21:00 cut-off, 19:00 reminders and clean-up. Under ADR-0020, background workers and cron jobs are not free on Render. Free Key Value is not durable, and the free web service sleeps. A scheduler inside the API (APScheduler) would miss jobs while the API sleeps.

## Decision
- **Postgres is the queue.** Events are rows in `platform.outbox`, written in the same transaction as the change. The relay picks them with `FOR UPDATE SKIP LOCKED`, so nothing is lost on a restart.
- **After each commit** the API relays fresh outbox rows itself, as a background task, so events usually go out within seconds.
- **Scheduled jobs are internal endpoints:** `POST /v1/internal/jobs/<name>` (outbox sweep, cut-off, reminders, clean-up), protected by the `JOBS_TOKEN` secret. A **GitHub Actions scheduled workflow** calls them. This is free, and the call also wakes a sleeping API.
- Every job is **idempotent** and takes a Postgres advisory lock. Business rules use the *scheduled* time, not the run time: GitHub may start a schedule a few minutes late, but the API already refuses box edits after 21:00 IST by itself, and the job only does the follow-up work.
- **Taskiq returns at the pilot,** when a paid worker exists. The job functions are plain async functions, so only the trigger changes.

## Alternatives considered
- **Taskiq worker now:** rejected. It needs a paid Render worker.
- **APScheduler inside the API:** rejected. It misses jobs while the free service sleeps, and would run twice with two instances.
- **Upstash or another hosted queue:** rejected. It adds one more service with free-tier limits, for a durability Postgres already gives us.

## Consequences
- (+) Free; no lost events (Postgres is durable); a job can be tested by calling its endpoint.
- (−) Schedule precision is minutes, not seconds; acceptable because rules use the scheduled time.
- (−) Long ML jobs (training, forecasts) cannot run inside an API request. They run in the ML service or offline until there is a paid worker.
- Docs that say "dispatches to Taskiq handlers" now describe the relay and the job endpoints.

## Related
[ADR-0003](0003-python-fastapi-backend-and-ml-service.md) · [ADR-0014](0014-final-stack-and-service-based-architecture.md) · [ADR-0020](0020-free-tier-hosting-render-vercel-neon-r2.md) · [M0 spec](../modules/M0-platform.md) · Handbook [Chapter 07](../handbook/07-workflows.md)

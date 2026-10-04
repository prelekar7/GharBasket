# ADR-0025 — ML runtime: serve on Hugging Face Spaces, train in GitHub Actions, store on R2

- **Status:** Accepted (settles the open item in ADR-0020; refines ADR-0021 for minute-level jobs; replaces the MLflow registry wording of handbook 08 §8.2)
- **Date:** 2026-10-04
- **Decision ID:** D25

## Context
ADR-0020 left the hosting of the ML service, OSRM and MLflow open. Render's free web service has 512 MB of memory, too little for the forecasting and embedding libraries, and no persistent disk for the 400 MB road network. The team has no card on file by choice. The ML work is one person's, alongside the backend, so every extra service costs time we do not have. Batch training must not run inside an API request (ADR-0021). A private repository has 2,000 free GitHub Actions minutes a month, which rules out minute-level schedules there.

## Decision
- **Serving: one Hugging Face Space** (free CPU tier: 2 vCPU, 16 GB, Docker, no card) runs `backend/ml` with uvicorn and `osrm-routed` as a second process. The OSRM data for the Mumbai region is baked into the image at build time (no persistent disk). Secrets are Space settings. UptimeRobot pings `/health` every 10 minutes so the Space does not sleep (free Spaces pause after 48 h idle; a cold start takes 30–90 s, longer than the gateway's 3 s timeout, and the API's rule fallbacks answer meanwhile).
- **The ML service stays stateless and light.** It receives everything it needs in the request and returns results; the API (`intel_gateway`) writes `intel.*`. `backend/ml` installs only solvers and inference libraries (ortools, numpy, scipy, lightgbm). No household ids or other personal data travel to the Space (it is hosted outside India; DPDP).
- **Training, forecasting and snapshots run in GitHub Actions**, in a new workspace member **`backend/training`** (`gharbasket-training`) with its own test gate. Daily and weekly workflows run `python -m gharbasket_training.jobs.<name>` on the runner, reading Neon through a read-only role and reading and writing Cloudflare R2 through the S3 API. Secrets are repository secrets, synced from Infisical. torch-based models (Chronos-2, bge-m3) are an optional extra used only in these jobs.
- **R2 is the lake and the model registry.** Snapshots go to `s3://gb-lake/snapshots/date=…/`, external data to `s3://gb-lake/external/…`, models to `s3://gb-mlflow/models/<name>/<version>/` with a `latest.json` pointer per model. Promotion is manual (`just promote <name> <version>`) in v1; the Space polls `latest.json` every 10 minutes and hot-reloads. MLflow stays a local experiment tracker (Docker Compose), never hosted. Evaluation reports are HTML files in R2 and `docs/eval/`.
- **Minute-level jobs are triggered by cron-job.org** (the 21:00 cut-off, the outbox sweep every few minutes, proposal expiry) calling `POST /v1/internal/jobs/<name>` with `JOBS_TOKEN`, the same endpoints GitHub Actions calls for daily and weekly jobs. The repository stays private.

## Alternatives considered
- **Render free for the ML service:** rejected. 512 MB and no disk; it could serve the solvers only, so forecasting and embeddings would need a second home anyway.
- **Oracle Cloud Free (always-on ARM VM):** rejected for now. It would run everything, but needs a card on file, which the team decided against (ADR-0020).
- **A hosted MLflow (own Space or server):** rejected. One more service to keep alive for a registry that a JSON pointer on R2 provides.
- **Making the repository public for unlimited Actions minutes:** declined by the team; cron-job.org covers the minute-level schedules for free.
- **Training inside the Space:** rejected. It would block serving and leave artifacts on an ephemeral disk.

## Consequences
- (+) No card, no bill; serving has the memory the models need; training has 7 GB runners for free.
- (+) One clear boundary: the API owns the data, the Space computes, Actions trains, R2 stores.
- (−) A cold Space answers late; the product must keep its rule fallbacks and show `source: "fallback"` honestly.
- (−) Actions schedules start late under load and are limited to daily/weekly cadence here; business rules use `scheduled_for` (ADR-0021).
- (−) Two trigger systems (Actions, cron-job.org) to keep in sync; both are documented in M0 §6.
- Handbook 08 §8.1–8.2, TDD §hosting, M10 §3/§10 and `.env.example` describe this runtime; `backend/ml/Dockerfile` and `deploy-ml.yml` implement it.

## Related
[ADR-0020](0020-free-tier-hosting-render-vercel-neon-r2.md) · [ADR-0021](0021-jobs-without-a-broker.md) · [ADR-0008](0008-simulator-first-data-source-tagging.md) · [M10 spec](../modules/M10-intelligence.md) · [Evaluation protocol](../research/eval-protocol.md) · Handbook [Chapter 08](../handbook/08-ai-and-ml.md)

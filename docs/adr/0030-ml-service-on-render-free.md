# ADR-0030 — The staging ML service runs on a free Render web service, without OSRM

- **Status:** Proposed (2026-10-28)
- **Date:** 2026-10-28
- **Decision ID:** D30
- **Amends:** [ADR-0025](0025-ml-runtime-spaces-actions-r2.md), the "Serving: one Hugging Face Space" decision and the rejected alternative "Render free for the ML service". Training in GitHub Actions, R2 as the lake and registry, and cron-job.org stay as decided there.

## Context
Hugging Face now requires a paid plan (PRO) for Docker and Gradio Spaces; only static Spaces are free. The ML service was never deployed to a Space, so staging has always answered with the API's rule fallbacks. The team still keeps no card on file (ADR-0020).

On 28 October we measured the serving image (`backend/ml`, without OSRM) under Render's free limits, 512 MB of memory and 0.1 CPU (#413). Peak memory was 246 MB (idle 126 MB), and nothing was killed. ADR-0025 rejected Render because of memory for forecasting and embeddings; those run in GitHub Actions (`backend/training`), not in the serving image. Latency at 0.1 CPU, against the API's timeouts:
- **In time:** route solving (5.5 s against an 11 s limit), kitchen assignment (under 2 s), a customer's green-slot quote (0.2–1 s against 2 s), and the pantry quantities (0.2 s).
- **Never in time:** the CP-SAT weekly planner (about 4 s and no feasible plan, against 3 s). The weekly plan therefore comes from the rule planner, which is the designed fallback.
- **Cold start:** about 60 s after 15 idle minutes. Calls during a cold start fall back.

## Decision
- **On staging, the ML service is a second free Render web service**, `gharbasket-ml` in `render.yaml`. It is built from `backend/ml/Dockerfile.render`, a Python-only image, and runs in Singapore next to the API. Render deploys it for each commit on `main` that changes its inputs; no deploy hook or Hugging Face secret is needed.
- **Staging has no OSRM.** Routing and slot pricing use their haversine fallback, which the service already has. `backend/ml/Dockerfile`, the image with the Mumbai road network baked in, stays the full image for local runs (`E2E_ML=1`) and for any paid host later.
- **The API and the ML service share `ML_SERVICE_TOKEN`** (Infisical `staging`). `ANTHROPIC_API_KEY` is not set on staging, so the assistant answers with its fallback until the owner approves its cost.
- **No keep-alive pings.** Both free web services share the workspace's 750 instance hours a month. A monitor that keeps either one awake would leave the other asleep part of the month, so UptimeRobot is not set up.
- **Demos that need the CP-SAT planner or real road routing** run the full image on a laptop (`just ml` or the Docker image), with `ML_SERVICE_URL` pointed at it for the demo. Moving to a paid host (Render Starter, Hugging Face PRO) or Modal's free monthly credit is decided before the pilot, together with the other paid plans (handbook 14 §14.6).

## Alternatives considered
- **Hugging Face PRO (about ₹800 a month):** this keeps ADR-0025 unchanged, but it needs a card. We can revisit it before the pilot.
- **Modal (a free monthly credit; reportedly no card):** more CPU and memory, and possibly OSRM. It needs a Modal-specific wrapper, and we have not checked its terms on Modal's own pages. This is the first alternative to try if Render's 0.1 CPU is not enough.
- **Oracle Cloud Always Free or Google Cloud Run:** both need a card on file.
- **Koyeb free:** the same 512 MB and 0.1 CPU as Render, so it adds nothing.
- **Laptop only:** the service would be up only during demos, and the staging journeys could not exercise it.

## Consequences
- (+) Staging calls a real ML service for routing, kitchen assignment, green-slot prices and the pantry twin, at no cost and with no card.
- (+) The rule fallbacks are exercised honestly whenever the service is asleep or slow: every answer shows `source: "fallback"`.
- (−) The weekly plan on staging always comes from the rule planner. The MS-C demo of the CP-SAT planner runs from a laptop or a paid host.
- (−) Routes use straight-line distances on staging, not Mumbai's roads.
- (−) The first ML call after 15 idle minutes falls back while the service wakes.
- `render.yaml`, `backend/ml/Dockerfile.render`, `backend/ml/README.md` and `.github/workflows/smoke-ml.yml` implement this. Handbook 08 §8.1–8.2, handbook 12, the TDD's hosting section and M10 still describe the Hugging Face Space; the docs sync after this ADR updates them.

## Related
[ADR-0020](0020-free-tier-hosting-render-vercel-neon-r2.md) · [ADR-0025](0025-ml-runtime-spaces-actions-r2.md) · [ADR-0029](0029-public-repository.md) · Handbook [Chapter 08](../handbook/08-ai-and-ml.md) and [Chapter 12](../handbook/12-deployment-and-operations.md)

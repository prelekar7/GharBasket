---
title: GharBasket ML
emoji: 🥬
colorFrom: green
colorTo: yellow
sdk: docker
app_port: 7860
pinned: false
---

# backend/ml: GharBasket ML and optimisation service (M10)

A separate FastAPI service that only the API calls (through `intel_gateway.api`) with the shared
service token. It serves the CP-SAT weekly meal planner (v1: nutrition, price spikes, sides), the
pantry twin's quantities, and soon routing and kitchen assignment. The contracts are M10 §3.

| Command | What |
|---|---|
| `just dev-ml` | Run on http://localhost:8100 (docs at `/docs`) |
| `uv run --directory backend/ml pytest --cov` | Tests with the coverage gate (90%) |
| `docker build -t gharbasket-ml backend/ml` | The Space image: uvicorn on 7860 plus `osrm-routed` with the Mumbai road network baked in (about 10 minutes the first time, 220 MB download) |
| `just cloud-ml https://<space>.hf.space $ML_SERVICE_TOKEN` | Smoke-test a deployed service |

**Hosting (ADR-0025):** one Hugging Face Space (free CPU tier, Docker). `.github/workflows/deploy-ml.yml`
uploads this folder to the Space after CI passes on `main` with a change here (secrets `HF_TOKEN`,
`HF_SPACE`); the Space builds the `Dockerfile` and restarts. The YAML header above is the Space's
card. The API reaches it through `ML_SERVICE_URL` and `ML_SERVICE_TOKEN` (Render); a cold Space
answers late, so the API's rule fallbacks keep working and say `source: "fallback"`. UptimeRobot
pings `/health` every 10 minutes so the Space does not sleep.

Settings: `ML_SERVICE_TOKEN` (required outside local development), `OSRM_URL` (set by the image to
the bundled road network; `/health/ai` reports `ok`, `down` or `not_configured`), `MODELS_MANIFEST`
(a local stand-in for the R2 registry pointer), `MODELS_DIR` (the learned models' folder in the registry
layout, `<name>/latest.json` and `<name>/<version>/model.txt`; unset: the fallbacks answer). The service is stateless and receives no personal
data (DPDP; the Space is hosted outside India).

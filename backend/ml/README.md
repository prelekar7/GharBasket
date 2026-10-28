# backend/ml: GharBasket ML and optimisation service (M10)

A separate FastAPI service that only the API calls (through `intel_gateway.api`) with the shared
service token. It serves the CP-SAT weekly meal planner (v1: nutrition, price spikes, sides), the
pantry twin's quantities, and soon routing and kitchen assignment. The contracts are M10 §3.

| Command | What |
|---|---|
| `just dev-ml` | Run on http://localhost:8100 (docs at `/docs`) |
| `uv run --directory backend/ml pytest --cov` | Tests with the coverage gate (90%) |
| `docker build -f backend/ml/Dockerfile.render -t gharbasket-ml-render .` (from the repository root) | The staging image: Python only, no road network, installed from `uv.lock`; `docker run -p 10000:10000 -e ML_SERVICE_TOKEN=<32+ bytes> gharbasket-ml-render` |
| `just ml-lock && docker build -t gharbasket-ml backend/ml` | The full image: uvicorn on 7860 plus `osrm-routed` with the Mumbai road network baked in (about 10 minutes the first time, 220 MB download). `just ml-lock` writes the git-ignored `requirements.lock` from `uv.lock`; the image installs exactly those versions and runs as user 1000. For a local run or a paid host; `start.sh` supervises both processes |
| `just cloud-ml https://<service>.onrender.com $ML_SERVICE_TOKEN` | Smoke-test a deployed service (also the manual `Smoke ML` workflow) |

**Hosting (ADR-0030, which supersedes ADR-0025's Hugging Face Space):** staging runs `gharbasket-ml`, a
second free Render web service next to the API (`render.yaml`, Singapore, `Dockerfile.render`). It has
no OSRM: `OSRM_URL` is unset, so `/route/solve` and `/slots/price` price with the haversine matrix
(distance x 1.3, a fixed speed; `source: "haversine"`) and `/health/ai` says `osrm: not_configured`. The
free instance has 512 MB RAM and 0.1 CPU, sleeps after 15 minutes without traffic and needs about a
minute to wake; both free web services share the workspace's 750 instance hours a month. While it is
asleep, or when a call is slower than the API's timeouts, the API answers from its rule fallbacks
(`source: "fallback"`), never an error. Render deploys it by itself once the commit's GitHub checks
pass, and only when `backend/ml/**`, `pyproject.toml` or `uv.lock` changed (`autoDeployTrigger:
checksPass` and a `buildFilter` in `render.yaml`); no deploy-hook secret is involved. In the Render
dashboard set `ML_SERVICE_TOKEN` (the same value as the API's); `ANTHROPIC_API_KEY` stays unset, so
the assistant answers 503 on staging. The API reaches it through `ML_SERVICE_URL` and
`ML_SERVICE_TOKEN`. Measured on the free instance's limits (`docker run --memory=512m --cpus=0.1`), the
service idles at about 125 MiB and peaks near 240 MiB; the planner does not finish inside the API's
3 s timeout there, so the weekly plan comes from the rules on staging, while routing, kitchen assignment,
slot pricing and the pantry twin answer in time.

Settings: `ML_SERVICE_TOKEN` (required outside local development), `OSRM_URL` (set by the full image to
the bundled road network; `/health/ai` reports `ok`, `down` or `not_configured`), `OSRM_TIMEOUT_S`
(default 2.5: `/route/solve` prices with haversine when OSRM is slower), `MODELS_MANIFEST`
(a local stand-in for the R2 registry pointer), `MODELS_DIR` (the learned models' folder in the registry
layout, `<name>/latest.json` and `<name>/<version>/model.txt`; unset: the fallbacks answer),
`MODELS_RELOAD_S` (how often the models are re-read, default 600: a promotion is served within ten
minutes, and a bad pointer or model keeps the last good one or the fallback, never a 500), `ANTHROPIC_API_KEY`
(a service secret; Ghar Saathi's `POST /assistant/turn` answers 503 without it), `ASSISTANT_MODEL`
(`claude-opus-5-5` until the S8 evaluation, ADR-0013), `ASSISTANT_REQUEST_TIMEOUT_S` (default 12, one
retry) and `ASSISTANT_TURN_TIMEOUT_S` (default 25, below the API's 30 s; 504 `ml.assistant_timeout`).
The service is stateless and receives no personal data (DPDP; the service is hosted outside India). For
Ghar Saathi the API sends only the conversation, already scrubbed of phones, e-mails, Aadhaar numbers,
PIN codes, addresses and the household's own names, and a trimmed week (delivery date, status, cut-off,
whether it can still change, lines with per-turn handles, prices in paise and reason codes, the total
and the meal plan's dish names): no household or user id, name, address or token. The assistant answers
`get_my_week` and `explain_item` from that week and calls nothing back; `edit_box_line` and `skip_week`
come back as intents, which the API resolves into the cards the person confirms (M13 §3).

# gharbasket-api

The GharBasket backend: a FastAPI modular monolith (see `docs/handbook/04-system-architecture.md`).

| Command (from the repo root) | What it does |
|---|---|
| `just dev` | Run the API with auto-reload on http://localhost:8000 |
| `just test` | Run the test suite |
| `just openapi` | Regenerate `services/api/openapi.json` (the contract the frontend client is built from) |

Configuration comes from environment variables (via Infisical, or a local `.env`); see `src/gharbasket/platform/settings.py`.
Every endpoint lives under `/v1`. Errors use RFC 9457 `application/problem+json` with a namespaced `code`.

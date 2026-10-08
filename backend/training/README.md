# backend/training: data, training and forecasting jobs (M10, ADR-0025)

Batch work that must not run inside an API request or the serving image: it runs in **GitHub Actions** (`.github/workflows/training-jobs.yml`), reads Neon through a read-only role and R2 through the S3 API, and hands results to the API's internal endpoints with `JOBS_TOKEN`. Locally the same jobs run against the Docker stack (`just up-storage` for S3).

```
uv run --directory backend/training python -m gharbasket_training.jobs.collect_mandi --out data/external/mandi
just eval --smoke                      # the harness, one seed and one week (CI); `just eval` for the five seeds
uv run --directory backend/training pytest --cov
```

## Jobs
| Job | Schedule | What it does |
|---|---|---|
| `eval` | by hand (`workflow_dispatch` job `eval`) and `just eval` locally | The evaluation harness (docs/research/eval-protocol.md): planner A/B and the pantry baseline over five simulator seeds, an HTML + JSON report in `docs/eval`, rows to `POST /v1/internal/intel/model-metrics` |
| `export_snapshots` | daily 02:03 IST (Actions) | The module tables to `snapshots/date=<day>/` in the lake (no personal data, typed columns) and the point-in-time household features as of the run to `intel.feature_snapshots` (`--push-features`); reads the database with `DATABASE_URL_RO` |
| `forecast` | T-1 21:10 IST (Actions; `--push`) | Next week's uncommitted demand per hub × product (StatsForecast, rolling-origin backtest, conformal q_tau) with newsvendor order quantities → `POST /v1/internal/intel/forecasts`; WAPE per model → model-metrics |
| `train_ranker` | weekly (Actions, by hand until the data is real) | Trains the next-basket ranker (LightGBM LambdaRank) on every household's approved and locked boxes and their edits, one model per `data_source` (never pooled), scores it leave-last-basket-out against the baselines, writes `basket_ranker/<version>-<data_source>/` under `models/` in the `gb-mlflow` registry (`--out` for a local `MODELS_DIR`), metrics to model-metrics (`--push`). It never moves `latest.json`: promotion is manual (ADR-0025); until `just promote` exists, `--promote <data_source>` does it for this run's model |
| `collect_mandi` | daily 18:33 IST (Actions) | Pulls today's wholesale prices for Maharashtra markets from data.gov.in (Agmarknet), maps commodities to our product slugs, writes `external/mandi/date=<day>/maharashtra.parquet` to the lake, and pushes a 4-week price index per product to `POST /v1/internal/intel/price-index` |

The data.gov.in resource keeps no history, so this collector is the history: it has run every day since 4 Oct 2026. The 2019–2025 bulk history (India Data Portal) is imported separately in January (forecast v0).

## Settings (environment)
`DATA_GOV_IN_API_KEY` (your own key; the public sample key returns 10 rows), `DATABASE_URL_RO` (a read-only Neon role; locally `DATABASE_URL`), `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION`, `S3_BUCKET_LAKE`, `S3_BUCKET_MLFLOW` (the model registry, default `gb-mlflow`), `API_URL`, `JOBS_TOKEN`, `MANDI_MARKETS` (comma-separated, default `Mumbai,Vashi New Mumbai,Pune`).

## Conventions
- Prices: Agmarknet reports ₹ per quintal; ₹ X per quintal is exactly X paise per kg, which is what we store (`modal_paise_per_kg`).
- Commodity names are mapped in `src/gharbasket_training/data/commodities.json`; unmapped names are logged, never guessed.
- Tests use recorded responses and a local folder instead of S3; the coverage gate is 70 %.

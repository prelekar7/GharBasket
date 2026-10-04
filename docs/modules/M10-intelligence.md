# M10 — Intelligence (ML & Optimisation Service)

**Status:** Draft v1 — refine at phase start (spec-first)
**Owner:** Prathamesh Relekar (backend), Yash Salunkhe (screens) · **QA:** Rhythm Thakur, Manish Raje (evaluation review, paper figures)
**Phase / Sprint:** Simulator and skeleton from P1 / S0; models in P4 / S7–S8
**Deployables:** `backend/ml` (FastAPI; hosting to be decided before P3/P4) — long jobs run in the service or offline until a paid `ml-worker` (Taskiq queue `ml`) exists at the pilot (ADR-0021); gateway module in the API: `gharbasket.modules.intel_gateway`
**Postgres schema:** `intel` (written by the ML service through the gateway's contract)

## 1. Purpose
All prediction and optimisation: the household simulator, pantry digital twin, next-basket ranker, CP-SAT meal planner and Budget Lock optimiser, demand forecasting and newsvendor procurement quantities, OR-Tools routing, fair kitchen assignment, slot pricing, churn/uplift, trust-and-safety scoring, catalog embeddings, the training pipeline with MLflow, and the evaluation harness for the research paper. M10 never blocks customer flows: batch results go to `intel.*`; online calls have timeouts and rule-based fallbacks.

## 2. User stories
| # | Story | Acceptance criteria |
|---|---|---|
| US-1 | As a customer, I want an explained weekly meal plan so that I trust the box. | Plan respects all hard constraints; every box line has a reason code. |
| US-2 | As a new customer, I want a sensible first box so that I don't need history. | Cold-start from cohort prior; quantity error within agreed tolerance on simulator. |
| US-3 | As a hub manager, I want order quantities with uncertainty so that I buy just enough. | Forecast quantiles per item; newsvendor order quantity. |
| US-4 | As a hub manager, I want feasible routes within 30 s so that vans leave on time. | CVRPTW with pickups; capacity and time windows respected. |
| US-5 | As a kitchen partner, I want fair order distribution so that income isn't concentrated. | Earnings Gini reported; λ configurable. |
| US-6 | As the team, I want reproducible experiments so that the paper's numbers are defensible. | Every run logged in MLflow with data snapshot id and seed. |
| US-7 | As the team, I want new models promoted only when better so that quality never regresses. | Champion/challenger gate on time-based holdout. |
| US-8 | As a researcher, I want one command to regenerate evaluation figures so that the paper stays current. | `just eval` produces a marimo HTML report. |

## 3. API endpoints (internal, service-to-service; called only via `intel_gateway.api`)
| Method | Path | Caller | Idempotency | Notes |
|---|---|---|---|---|
| POST | `/plan/week` | subscriptions (batch via job) | Request id | Meal plan + box lines; 5 s per household |
| POST | `/basket/rank` | subscriptions | — | Ranked candidates |
| GET | `/substitutes/{product_id}` | catalog | — | Learned + similarity |
| POST | `/forecast/run` | ops (job) | Request id | Async; writes `intel.predictions` |
| POST | `/route/solve` | logistics | Request id | Async; 30 s limit |
| POST | `/assign/kitchens` | partners (job) | Request id | Fair assignment |
| POST | `/slots/price` | orders / community | — | Marginal insertion cost |
| POST | `/embed` | catalog (job) | — | bge-m3 embeddings |
| GET | `/models`, `/health/ai` | console (admin) | — | Champion versions, drift metrics |

## 4. Tables owned
| Table | Key columns |
|---|---|
| `intel.feature_snapshots` | household_id, as_of, features jsonb |
| `intel.predictions` | id, kind (plan, forecast, route, assignment, churn), subject_id, for_date, payload jsonb, model_name, model_version, created_at |
| `intel.experiments` | id, key, variant, subject_id, assigned_at |
| `intel.model_metrics` | model_name, version, metric, value, split, data_source, computed_at |

Outside Postgres (S3-API object storage; SeaweedFS locally, hosted storage decided with the ML hosting): Parquet snapshots in `s3://gb-lake/`, MLflow artifacts in `s3://gb-mlflow/`, simulator ground truth in `data/sim_truth/`.

## 5. Events
**Published:** `intel.plans_ready`, `intel.forecast_ready`, `intel.routes_ready`, `intel.assignment_ready`, `intel.substitutions_learned`.
**Consumed:** `subscriptions.cycle_generated` feedback, `orders.locked`, `orders.delivered`, edit feedback (via snapshots).

## 6. Jobs
| Job | Schedule | Notes |
|---|---|---|
| `export_snapshots` | Daily 02:00 | DuckDB reads Postgres → Parquet in object storage (S3 API); pandera validation |
| `generate_weekly_plans` | T-3 18:00 | Batch CP-SAT across CPU cores |
| `forecast_uncommitted` | T-1 21:10 | StatsForecast/MLForecast/Chronos-2 + reconciliation |
| `retrain_models` | Sunday 03:00 | Champion/challenger; MLflow alias `@champion` |
| `monitor_drift` | Daily | WAPE, acceptance, waste vs thresholds |

Until a paid worker exists, these batch jobs run in the ML service or offline, triggered by the API's internal job endpoints on a GitHub Actions schedule (ADR-0021).

## 7. Permissions
Service-to-service token for API ↔ ML calls; `models:read` for admin in the console.

## 8. Screens
**Console:** AI health (champion versions, drift charts, forecast vs actual), experiment results. Customer-facing outputs are rendered by M3/M4 screens.

## 9. Tests required
- Solver golden tests (fixed seeds) for routing, meal planner and assignment (feasibility and objective within tolerance).
- Model-quality gates on fixture datasets (minimum Recall@10, maximum WAPE).
- Leakage tests: features computed strictly as of cut-off.
- Timeout and fallback tests from the API side (ML service down → rule-based box).
- Evaluation reproducibility: same seed and snapshot → same metrics.

## 10. Dependencies
Reads Postgres snapshots (read-only role). Writes results only to `intel.*`. Other modules call it only through `intel_gateway.api`.

## 11. Open questions
- Embedding model confirmation (bge-m3 vs smaller multilingual model) in the S7 spike.
- Nutrition targets per age band (ICMR-NIN RDA tables) for the meal planner.
- Whether to fine-tune Chronos for the paper (optional, free Kaggle/Colab GPU).

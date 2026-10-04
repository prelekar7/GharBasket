# M10 — Intelligence (ML & Optimisation Service)

**Status:** Draft v1.1. §3.1 (`POST /plan/week`, v0) is frozen and built in `backend/ml`; the rest is refined at phase start (spec-first).
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
| POST | `/plan/week` | subscriptions (batch via job) | Request id | Meal plan; ≤ 5 s per household (§3.1, v1 in §3.2) |
| POST | `/pantry/quantities` | subscriptions | Request id | Per-item quantities with intervals from the pantry twin; rule table fallback in the gateway |
| POST | `/basket/rank` | subscriptions | — | Ranked candidates (P4) |
| POST | `/route/solve` | logistics | Request id | Two waves of ≤ 10 s; returns routes, the API writes `logistics.*` via its facade |
| POST | `/assign/kitchens` | partners (job) | Request id | Greedy now, fair (λ) in S8 |
| POST | `/forecast/run` | hub_ops (job, from `backend/training`) | Request id | Returns forecasts; the API writes `intel.predictions` and `hub_ops.procurement_lines` |
| POST | `/slots/price` | orders / community | — | Marginal insertion cost (S9) |
| GET | `/substitutes/{product_id}` | catalog | — | Learned + similarity (stretch) |
| POST | `/embed` | catalog (job) | — | Embeddings (stretch, training jobs only) |
| GET | `/models`, `/health/ai` | console (admin) | — | Served model versions from the R2 pointer, drift metrics |

The ML service is **stateless** (ADR-0025): it returns results and never writes the database; `intel_gateway` records predictions in `intel.*` and hands results to the owning module's facade. Long jobs (forecast training, snapshots) run in `backend/training` on GitHub Actions and call the same facades through the API's job endpoints.

### 3.1 `POST /plan/week` (v0, from spike S-4)
Stateless: the API sends everything the planner needs, so the ML service needs no database for it. The API calls it only through `intel_gateway.api.plan_week()`, with a timeout (default 3 s). On a timeout, an error or no service configured, the gateway uses its rule-based planner instead, so ML never blocks a customer.

```
POST /plan/week          Authorization: Bearer <ML_SERVICE_TOKEN>   (X-Request-ID passed through)
  request : {
    "household": { "size": 4, "diet": "veg" | "egg", "jain": false, "diabetic": false,
                   "cuisines": ["maharashtrian"], "weekday_cook_minutes": 30,
                   "weekend_cook_minutes": 60, "no_cook_days": [6],     0 = Monday
                   "weekly_budget_paise": 138568 | null },
    "fasting_days": [ { "day": 1, "no_onion_garlic": true } ],          days of this week
    "recipes": [ { "slug": "kanda-poha", "cuisine": "maharashtrian",
                   "slots": ["breakfast"], "cook_minutes": 20, "diet": "veg",
                   "jain_ok": false, "no_onion_garlic": false, "fasting_ok": false,
                   "diabetic_ok": false, "no_cook": false,
                   "cost_per_serving_paise": 1268, "perishables": ["coriander"] } ],  1 to 500
    "time_limit_s": 2.0 }                                                0.1 to 5
  200 : { "status": "optimal" | "feasible" | "infeasible",
          "relaxed": ["repeat"],          comfort rules relaxed to find a plan (see below)
          "plan": [ { "day": 0, "slot": "breakfast", "recipe": "kanda-poha" | null } ] | null,
                                          21 entries (7 days × breakfast, lunch, dinner);
                                          null recipe = tiffin (no-cook days, lunch and dinner)
          "score": { "preference": 17, "variety": 15, "perishables": 7, "cost_paise": 111904 } | null,
          "seconds": 0.012, "model": "cpsat-v0" }
  401 "ml.unauthorized" (wrong or missing token) · 422 "request.invalid"
```

**Rules** (handbook 08 M-C; the S-4 note gives the evidence):
- **Hard:**
  - one choice per (day, slot); tiffin only for lunch and dinner on no-cook days, with a no-cook breakfast;
  - no egg dishes for veg households; Jain households only `jain_ok` dishes; diabetic households only `diabetic_ok` lunches and dinners;
  - on a fasting day at least one `fasting_ok` dish, and no onion or garlic all day if the fast says so;
  - cook time **per meal** within the weekday or weekend limit;
  - no dish twice within 2 days (breakfast) or 3 days (lunch and dinner);
  - the week's cost (`cost_per_serving_paise` × size) within the budget.
- **Soft:** 3 × cuisine match + 5 × variety − 4 × distinct perishables.
- **Fallback when infeasible:** relax `repeat` (a dish may come back on a later day, never twice on the same day), then `cook_time`, then `budget`, one rule and then two. **Never** relax diet, Jain, diabetic or fasting rules. `relaxed` says what was used, and `infeasible` is returned if nothing works.
- **Deterministic mode:** in-process callers (the simulator) can pass `deterministic=True` to `plan_week`. CP-SAT then interleaves its workers in fixed batches with a deterministic time limit, so the same request always gives the same plan. Several plans can tie for the best score, and the faster default can return any of them. The HTTP endpoint keeps the default.

### 3.2 `POST /plan/week` v1 (planned for Nov–Dec 2026; additive, so v0 clients keep working)
Every new field is optional; a request without them is a v0 request and gets a v0 answer.

```
  request additions:
    "household": { …v0…,
      "members": [ { "age_band": "18_59" | "under_5" | "5_17" | "60_plus", "sex": "f" | "m" | "other" } ],
                                      for nutrition targets (ICMR-NIN RDA); size stays the count
      "diet": "veg" | "egg" | "nonveg" }   nonveg households may be offered any dish; no nonveg
                                           recipes exist in the pilot catalog
    "recipes": [ { …v0…,
      "nutrition_per_serving": { "kcal": 320, "protein_g": 12, "fibre_g": 6 } | null,   from IFCT
      "sides": ["chapati"],              side recipes this main is eaten with (slugs); sides are
                                         recipes flagged "is_side": true and never planned alone
      "is_side": false,
      "price_spike": 0.62 | null } ],    (current price − 4-week index) / index, from intel.price_index
    "weights": { "preference": 3, "variety": 5, "perishables": 4, "nutrition": 4, "price": 3 } | null
                                         defaults as given; only evaluation ablations override them
  200 additions:
    "plan": [ { …v0…, "sides": ["chapati"], "reasons": ["FESTIVAL", "BUDGET"] } ]
                                         reason codes per meal (handbook 08 §8.4), params in "reason_params"
    "score": { …v0…, "nutrition": 0.82, "price_penalty": 3 }   nutrition = share of member-days
                                         within ±20 % of the RDA targets for kcal and protein
```
- **Soft objective v1:** v0 terms + `nutrition` × adherence − `price` × Σ price_spike of chosen dishes (an ingredient 62 % above its 4-week index costs 0.62 × its share). Hard rules are unchanged; the relaxation order is unchanged.
- **Fallback:** the rule-based planner gains sides pairing and ignores nutrition and price (it records `reasons: ["FALLBACK"]`).
- Kits on busy days and preference from the ranker (M-B) come with v2 in P4.

### 3.3 `POST /pantry/quantities` (v0, the pantry twin; M-A)
How much of each item a household goes through in a week, with an 80 % interval. Stateless: the gateway sends the cohort prior and the household's observations; `intel_gateway.fallback.pantry_quantities` computes the same mean with a normal interval when the service is unreachable.

```
  request:
    "items": [ { "product": "milk", "step": 500,            base units in one step (the count unit)
                 "prior_mean": 1400, "prior_weeks": 2,      the cohort's weekly use (the rule table today) and its weight
                 "observations": [ { "qty": 1500, "signal": "ran_out" | "wasted" | "just_right" | null } ] } ]
                                                            the last 12 locked boxes' quantities and taps, oldest first
  200:
    "items": [ { "product", "mean", "p10", "p90", "weeks_observed", "alpha", "beta" } ],
    "model": "pantry-gamma-poisson-v0"
```
- **Model:** Gamma prior on the weekly rate (mean `prior_mean`, weight `prior_weeks`); each observed week adds the box's quantity in steps, corrected by the tap (ran out ×1.3, wasted ×0.7); the posterior is Gamma(α₀ + Σ counts, β₀ + weeks); `mean` is what the box carries, `p10`/`p90` the interval (SciPy's Gamma percentiles in the service, a normal approximation in the fallback).
- **Use (M3 §3.1 step 2):** for the daily-use items, once a household has one locked box, the twin's `mean` replaces the rule table's quantity (`reason_code: pantry_low`, params `mean, p10, p90, weeks, source`); the rule table alone drafts the first boxes. Recorded in `intel.predictions` (kind `pantry`).
- **v1 (P4):** NumPyro refits the cohort priors per cohort × product in `backend/training`; the formulas here stay.

### 3.2 Household simulator v0 (`data/simulator`, `just sim`)
Not an endpoint: a command-line tool (handbook 05 §5.11, ADR-0008) that runs against the local database only.
- **Input:**
  - the seed world from `just seed` (hub, societies, catalog, prices, festival calendar), read through the catalog services;
  - the cohorts in `data/simulator/cohorts.json` (assumptions for Rhythm and Manish to review);
  - the arguments `--seed` (default 7), `--weeks` (8), `--start` (Mon 5 Oct 2026), `--planner cpsat|rules` and `--adoption` (share of flats, default 0.1).
- **Households:**
  - sampled per seed society with a hub, using cohort size, diet, Jain, cuisines, fasts, a diabetic elder (with health consent), cook time, no-cook Sunday and Budget Lock;
  - onboarded through M1's `create_household` with `data_source='synthetic'`;
  - phones +91 98001 00001 onwards (reserved);
  - a rerun keeps existing households.
- **Weeks:**
  - **Plan:** the planner plans each week (CP-SAT in deterministic mode, or the rules fallback). Fasts come from the household's observances and the festival calendar. The weekly budget is the monthly Budget Lock × 7 / 30.
  - **Accept or edit:** each planned dish is accepted with probability sigmoid(accept bias + taste). Otherwise it is swapped for the household's favourite dish allowed under the same hard rules, and a fasting dish is only swapped for another fasting dish.
  - **Skip:** a skipped week means no box; the family still cooks.
  - **Consumption:** the cooked dishes' ingredients × adult equivalents, plus latent daily-use items (milk, atta, oil, salt, curd, fruit) with weekly noise.
- **Output:** Parquet ground truth in `data/sim_truth/seed-<n>/` (git-ignored):
  - `households`, `daily_use` and `taste` (the latent truth);
  - `weeks`, `meals` and `consumption` (behaviour), with columns as in `gharbasket_sim/truth.py`;
  - `manifest.json`.
  The weeks reach the database through M3/M4 once those exist.
- **Guarantees (tested):**
  - the same seed and catalog give identical tables;
  - every cooked dish keeps the planner's hard rules;
  - Jain households never consume root vegetables, onion or garlic, and veg households never eggs;
  - the database catalog equals the seed files.
- **Seed 7 (4 Oct 2026):**
  - 87 households in 5 societies, 696 household-weeks in about 35 s;
  - every plan optimal, with 28 % needing the repeat rule relaxed (the small menu, as in S-4);
  - 76 % of planned dishes accepted;
  - 8 % of boxes skipped.

## 4. Tables owned
| Table | Key columns |
|---|---|
| `intel.feature_snapshots` | household_id, as_of, features jsonb |
| `intel.predictions` | id, kind (weekly_plan, pantry, basket_rank, forecast, route, assignment, slot_price, churn), subject_type, subject_id, for_date, source (ml, fallback), payload jsonb, model_name, model_version, request_id, created_at |
| `intel.experiments` | id, key, variant, subject_id, assigned_at |
| `intel.model_metrics` | model_name, version, metric, value, split, data_source, computed_at |
| `intel.price_index` | product_id, day, market, modal_paise_per_kg, index_4w (today over the 4-week median; 1.62 = 62 % above usual), source, updated_at |

Outside Postgres (S3-API object storage; SeaweedFS locally, hosted storage decided with the ML hosting): Parquet snapshots in `s3://gb-lake/`, MLflow artifacts in `s3://gb-mlflow/`, simulator ground truth in `data/sim_truth/`.

## 5. Events
**Published:** `intel.plans_ready`, `intel.forecast_ready`, `intel.routes_ready`, `intel.assignment_ready`, `intel.substitutions_learned`.
**Consumed:** `subscriptions.cycle_generated` feedback, `orders.locked`, `orders.delivered`, edit feedback (via snapshots).

## 6. Jobs
| Job | Schedule | Notes |
|---|---|---|
| `collect_mandi` | Daily 18:33 IST (`training-jobs.yml`, `backend/training`) | data.gov.in (Agmarknet) prices for Maharashtra markets → `s3://gb-lake/external/mandi/date=…/`; pushes the 4-week price index per product to `POST /v1/internal/intel/price-index` (operator token) → `intel.price_index`, read by `intel_gateway.api.price_spikes` |
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
- Model-quality gates on fixture datasets, with the pre-registered targets of [`docs/research/eval-protocol.md`](../research/eval-protocol.md) (e.g. Recall@10 ≥ 0.6, WAPE ≤ 25 %); a miss fails the eval report's gate, never the build.
- Leakage tests: features computed strictly as of cut-off.
- Timeout and fallback tests from the API side (ML service down → rule-based box).
- Evaluation reproducibility: same seed and snapshot → same metrics.

## 10. Dependencies
Reads Postgres snapshots (read-only role). Writes results only to `intel.*`. Other modules call it only through `intel_gateway.api`.

## 11. Open questions
- Embedding model confirmation (bge-m3 vs smaller multilingual model): a stretch item after MS-C (ADR-0025 roadmap).
- Nutrition targets per age band (ICMR-NIN RDA tables) for the meal planner.
- Whether to fine-tune Chronos for the paper (optional, free Kaggle/Colab GPU).

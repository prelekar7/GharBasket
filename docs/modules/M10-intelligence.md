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

### 3.2 `POST /plan/week` v1 (implemented 5 Oct 2026; additive, so v0 clients keep working)
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
- **Soft objective v1:** v0 terms + `nutrition` × adherent days − `price` × Σ price_spike of chosen dishes (a dish whose costliest ingredient is 62 % above its 4-week index costs 0.62 × the weight). A day is adherent when its three meals' kcal and protein per person (mains plus their sides) fall within ±20 % of the household's average ICMR-NIN target (`gharbasket_ml/nutrition.py`, from `data/external/nutrition/rda.json`); CP-SAT linearises it with one indicator per day. Hard rules are unchanged; the relaxation order is unchanged. Reason codes: `FESTIVAL` on fasting days, `TIFFIN` on no-cook slots, `BUDGET` when the budget rule was relaxed, `FALLBACK` from the rules planner.
- **Fallback:** the rule-based planner pairs sides, never plans a side as a meal, and ignores nutrition and price (it records `reasons: ["FALLBACK"]`).
- **v2 (built 5 Oct 2026, F26; additive):** `recipes[].preference` (0..1, null = unknown) and `weights.liking` (default 3). The objective adds `liking × Σ round(10 × preference)` over chosen dishes: a dish the household always kept is worth about two dishes of variety, so taste tilts the plan without overriding any rule. `score.liking` reports the mean preference of the chosen dishes. M3 fills `preference` from the ranker (§3.7): once a household has two past approved or locked weeks, `subscriptions.service.ranker_candidates` builds the ranker's features from its boxes (weeks since kept, share of weeks kept, removed last time, the hub's popularity last week, today's price), `intel_gateway.api.rank_basket` scores them (`/basket/rank`, frequency fallback, recorded as `basket_rank`), and `planning.recipe_preferences` turns product scores into a dish liking (mean over scored ingredients, then rank-scaled to 0..1 across the mains' distinct values, ties sharing a value). The rules fallback orders dishes by liking after cuisine. The simulator's `cpsat_v2` uses the household's observed acceptance share instead; the harness reports it next to `cpsat` (v1) and checks `v2_liking_lifts_acceptance`. Kits on busy days stay open.

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

### 3.4 `POST /route/solve` (v0, M-F; from spike S-3)
One delivery slot's van routes for a hub: a stop is a society (the captain does the last 50 metres). Stateless; the API (M7 through `intel_gateway.api.solve_routes`) sends the day's societies with their loads and windows and gets routes with ETAs back, recorded in `intel.predictions` (kind `route`, subject the hub).

```
  request:
    "hub": { "lat", "lng" }, "depart_s": 23400, "return_by_s": 41400,      seconds after midnight IST
    "stops": [ { "id": "<society id>", "lat", "lng", "orders", "kg_tenths", "crates", "empties",
                 "donation_kg_tenths", "window_start_s": 25200, "window_end_s": 32400 } ],
    "food_bank": { "lat", "lng", "window_start_s", "window_end_s" } | null,
    "vans": { "count", "capacity_kg": 400, "capacity_crates": 24, "traffic_factor": 1.5, "reload_s": 1200 },
    "service": { "drop_s": 240, "per_crate_s": 30 }, "cost": { "fixed_paise_per_van": 25000, "paise_per_km": 60,
                 "wage_paise_per_s": 3, "late_paise_per_s": 50 }, "time_limit_s": 10
  200:
    "status": "solved" | "fallback",                 fallback = nearest neighbour, when the solver found no plan
    "source": "osrm" | "haversine",                  the road network, or straight lines x 1.3 when OSRM is down
    "routes": [ { "van", "start_s", "end_s", "stops": [ { "id", "kind": "drop" | "bank", "eta_s", "late_s" } ] } ],
    "summary": { "vans", "km", "cost_paise", "on_time", "late_min", "problems": [] },   the checker's findings
    "seconds", "model": "cvrptw-waves-v0"
```
- **Model:** OR-Tools CVRPTW with pickups and deliveries (empties and donations ride back; each donation reaches the food bank on the same van), solved in **two waves** (S-3): the earliest-window societies first, then the rest with the same vans from the moment they are back and reloaded, new vans only where needed. Each wave gets half the time limit.
- **Checker:** every answer is replayed on the matrix (windows with waiting, loads at every stop, pairing, return time, reload gaps); `summary.problems` lists what fails, so M7 never publishes a route the checker rejects.
- **Fallbacks:** OSRM down → haversine distances; no plan → nearest neighbour; the service down → the gateway returns `None` and M7 keeps the previous day's sequence (M7 §6).

### 3.5 `POST /assign/kitchens` (v0, M-G; greedy baseline inside)
Tomorrow's tiffin portions to home kitchens for a hub (M8 §6 `assign_kitchens` at T-1 21:30, through `intel_gateway.api.assign_kitchens`, recorded in `intel.predictions` kind `assignment`).

```
  request:
    "orders": [ { "id": "<order id>", "lat", "lng", "portions", "skills": ["veg", "jain"] } ],
    "kitchens": [ { "id": "<partner id>", "lat", "lng", "max_portions", "skills": [...], "rating": 4.5,
                    "earnings_paise": 120000 } ],                      rolling earnings before tonight
    "fairness_lambda": 0.5,            0 = cost only; the eval harness sweeps it for the Pareto curve
    "rating_floor": 3.8, "max_km": 5, "paise_per_portion": 6000, "quality_paise_per_star": 500,
    "time_limit_s": 5
  200:
    "status": "solved" | "fallback" | "infeasible",   fallback = greedy nearest kitchen
    "assignments": [ { "order_id", "kitchen_id", "portions", "km" } ],
    "summary": { "portions", "kitchens_used", "km_per_portion", "cost_paise_per_portion",
                 "gini", "earnings_paise": [...] } | null,
    "seconds", "model": "assignment-cpsat-v0"
```
- **Model:** CP-SAT; each order to one kitchen within reach that has the skills, the rating and the room; objective = distance (₹3 a portion-km) + quality penalty (₹5 a star below 5) + λ × the largest deviation of any kitchen's earnings (rolling + tonight) from the fair share — the linear proxy for the Gini in handbook 08 M-G.
- **Fallbacks:** no plan or too many orders → greedy nearest kitchen with room (`status: fallback`); the service down → the gateway returns `None` and M8's job assigns greedily itself.

### 3.6 The forecast job (v0, M-E; `backend/training`, not an endpoint)
`python -m gharbasket_training.jobs.forecast --push` at T-1 21:10 IST (M6 §6): next week's **uncommitted** demand per hub × product with newsvendor order quantities, from the module tables with the read-only role, handed to `POST /v1/internal/intel/forecasts` (one `intel.predictions` row per hub and delivery day, kind `forecast`, payload `items[{product_id, committed, uncommitted_q50, uncommitted_qtau, order_qty, model}]`); M6 reads it through `intel_gateway.api.hub_forecast(hub_id, for_date)`. The backtest's WAPE per model goes to `model_metrics` (split `backtest`).
- **Series:** weekly buckets per hub × product from orders that reached `locked` or later; `committed` = box lines, `uncommitted` = add-on and tiffin lines.
- **Models:** StatsForecast AutoETS, CrostonOptimized, SeasonalNaive(4); baselines SeasonalNaive and Naive ("buy last week"). Rolling-origin backtest (horizon one week, up to three windows); the champion per hub has the lowest WAPE; a series shorter than four weeks gets the naive answer.
- **Intervals and quantities:** conformal — q_tau = point + the tau-quantile of the champion's backtest residuals; `order_qty = committed + Q_tau(uncommitted)`, tau = C_u / (C_u + C_o) (default 0.3 / 0.5 → 0.375, `--tau`).
- **v1 (built 5 Oct 2026, F25):** a global LightGBM per hub joins the backtest (`LightGBM`, the native booster; lags 1–3, the 4-week mean, the week of the year on the circle, and what is known in advance: festival and fast days of the target week from M2's calendar, the product's mandi price index from `intel.price_index`); it is scored only when every backtest window could fit it (8 training rows) and wins the champion slot like any other model. Predictions are stored as `forecast-<champion>-v1`. Weather and MinT reconciliation stay open.

### 3.7 `POST /basket/rank` and the ranker job (v0, M-B)
The next-basket ranker: which candidates belong in a household's box this week. The training job (`backend/training`, `train_ranker`, weekly) learns from every household's boxes and edits; the Space serves the model from the registry.

```
  request:  "candidates": [ { "product", "weeks_since", "frequency", "drafted", "drafted_rule", "removed_last",
                              "popularity", "price_paise" } ], "top_k": 10
  200:      "source": "ml" | "fallback", "model", "ranked": [ { "product", "score", "rank" } ]
```
- **Baskets and labels:** one basket per household x week; a candidate's label is 1 when it is in the final box (kept or added), 0 when drafted and removed or never taken.
- **Candidates:** the household's own past items, the frequent items of its 5 nearest households (Jaccard on past baskets; TIFU-KNN simplified), and the week's drafted lines.
- **Features** (computed from earlier weeks only): weeks since last bought, share of past weeks bought, drafted this week and by the rule table, removed last time, share of households buying it last week, unit price.
- **Model:** LightGBM LambdaRank grouped by basket; **leave-last-basket-out** NDCG@10 and Recall@10 against "repeat last week" and "top frequency" (eval protocol §4), in the model card and in `model_metrics`.
- **Registry (ADR-0025):** `models/basket_ranker/<version>/{model.txt, card.json}` and `models/basket_ranker/latest.json` on R2, the same layout under the Space's `MODELS_DIR`; `/models` lists it. Without a model the fallback ranks by frequency then popularity (`source: "fallback"`).
- v1 (P4): SHAP explanations, price delta and festival features, the ranker's preference term in planner v2.

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
| `intel.feature_snapshots` | subject_type, subject_id, as_of, features jsonb — v0 household features: weeks_observed, cycles, approved_share, skipped_share, edits_per_box, taps_ran_out, taps_wasted, weekly_use_<slug> per daily-use item; computed only from rows before `as_of` (leakage test in backend/training) |
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
| `export_snapshots` | Daily 02:03 IST (Actions, `DATABASE_URL_RO`) | The module tables through the API's SQLAlchemy models with a read-only role → `snapshots/date=<day>/<table>.parquet` in the lake (no personal data: households without owner, members or address; every table with `data_source`), typed-column checks; then the point-in-time household features as of the run → `intel.feature_snapshots` (`POST /v1/internal/intel/feature-snapshots`) |
| `generate_weekly_plans` | T-3 18:00 | Batch CP-SAT across CPU cores |
| `forecast` (`backend/training`) | T-1 21:10 IST (Actions, cron-job.org for the minute) | StatsForecast v0 (§3.6); MLForecast and reconciliation in v1 |
| `retrain_models` | Sunday 03:00 | Champion/challenger; MLflow alias `@champion` |
| `monitor_drift` | Daily 03:33 IST (`jobs-staging.yml`; API job) | For the last 7 delivery days: realised WAPE of each hub's forecast (uncommitted quantity per product against the add-on and tiffin lines of locked-or-later orders, `orders.api.sold_lines`) → `model_metrics` split `live`, with `drift` = 1 when more than 10 points worse than the model's latest backtest WAPE; plan acceptance per plan source (`subscriptions.api.acceptance_on`) → `planner_<source>` `acceptance`. Once per hub and day (details carry `for_date`). Waste waits for M6 |

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

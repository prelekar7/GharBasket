# M10 — Intelligence (ML & Optimisation Service)

**Status:** Draft v1.1. §3.1 (`POST /plan/week`, v0) is frozen and built in `backend/ml`; the rest is refined at phase start (spec-first).
**Owner:** Prathamesh Relekar (backend), Yash Salunkhe (screens) · **QA:** Rhythm Thakur, Manish Raje (evaluation review, paper figures)
**Phase / Sprint:** Simulator and skeleton from P1 / S0; models in P4 / S7–S8
**Deployables:** `backend/ml` (FastAPI, stateless; served from a Hugging Face Space with OSRM as a second process, ADR-0025); `backend/training` (training, forecasting, snapshots and data jobs, run in GitHub Actions, ADR-0025); models and the lake on R2 with a `latest.json` pointer per model; MLflow local only; gateway module in the API: `gharbasket.modules.intel_gateway`
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
    "time_limit_s": 1.0 }        0.1 to 5; the solver's wall-clock budget for the whole request,
                                 every relaxation included (the gateway caps it at half its timeout)
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
                                         reason codes per meal (handbook 08 §8.4); "reason_params"
                                         (the codes' parameters) is not implemented in v1
    "score": { …v0…, "nutrition": 0.83, "price_penalty": 0.62 }
                                         nutrition = share of cooking days (no-cook days left out)
                                         whose kcal and protein per person are within ±20 % of the
                                         household's average RDA target; price_penalty = Σ
                                         price_spike of the chosen dishes
```
- **Soft objective v1:** v0 terms + `nutrition` × adherent days − `price` × Σ price_spike of chosen dishes (a dish whose costliest ingredient is 62 % above its 4-week index costs 0.62 × the weight). Every term is on one integer scale (hundredths of a weight point), so one cuisine match is worth `preference`, one adherent day `nutrition`, and a 3 % spike only 0.03 × `price`. The costliest ingredient is the one with the largest quantity × today's price per serving (M3 `planning.price_spike`), so a spiked garnish never marks a dish. A day is adherent when its three meals' kcal and protein per person (mains plus their sides) fall within ±20 % of the household's average ICMR-NIN target (`gharbasket_ml/nutrition.py`, from `data/external/nutrition/rda.json`); CP-SAT models it with one indicator per day that enforces the four bounds when set. No-cook days are left out of the term and of `score.nutrition`: their tiffins carry no nutrition data, so they could never be adherent. Hard rules are unchanged; the relaxation order is unchanged.
- **Solve time v1:** the indicators make optimality slow to prove (1.5 to 3 s for a seed week), so the planner first solves the v0 objective with 30 % of the limit and passes that plan to the v1 search as a hint (`add_hint`); the v1 search then has the rest of `time_limit_s`. The default limit is 1 s and covers every relaxation, and the gateway sends at most half of `ml_timeout_s` (3 s), so the answer arrives in about 1 s with room for the network; a plan that is not proven optimal comes back as `"feasible"`.
- **Reason codes:** `FESTIVAL` on fasting days, `TIFFIN` on no-cook slots, `BUDGET` when the budget rule was relaxed, `FALLBACK` from the rules planner.
- **Fallback:** the rule-based planner pairs sides, never plans a side as a meal, and ignores nutrition and price (it records `reasons: ["FALLBACK"]`).
- Kits on busy days and preference from the ranker (M-B) come with v2 in P4.

### 3.3 `POST /pantry/quantities` (v0, the pantry twin; M-A)
How much of a perishable daily-use item (milk, curd) a household goes through in a week, beyond what the week's recipes need, with an 80 % interval for next week's use. Stateless: the gateway sends the cohort prior and the household's observations; `intel_gateway.fallback.pantry_quantities` computes the same mean with a normal interval when the service is unreachable.

```
  request:
    "items": [ { "product": "milk", "step": 500,            base units in one step (the count unit)
                 "prior_mean": 1400, "prior_weeks": 2,      the cohort's weekly daily use (the rule table today) and its weight
                 "observations": [ { "qty": 1500, "signal": "ran_out" | "wasted" | "just_right" | null } ] } ]
                                                            the last 12 locked boxes' daily-use shares and taps, oldest first
  200:
    "items": [ { "product", "mean", "p10", "p90", "weeks_observed", "alpha", "beta" } ],
    "model": "pantry-gamma-poisson-v0"
```
- **Observation:** a locked box's daily-use share: the delivered quantity minus that week's planned recipes' need for the product (computed as the draft computes it, from the cycle's `meal_plan`), never below 0. The twin learns daily use only; the recipes come on top in the draft, so it cannot feed back on itself.
- **Model:** Gamma prior on the weekly rate (mean `prior_mean`, weight `prior_weeks`); each observed week adds its share in steps, corrected by the tap (ran out ×1.3, wasted ×0.7); the posterior is Gamma(α₀ + Σ counts, β₀ + weeks). `mean` = α/β × step. `p10`/`p90` are for next week's use: the posterior predictive is Negative Binomial (r = α, p = β/(β+1)) in steps, scaled by the step (SciPy percentiles in the service; in the fallback a normal approximation with mean α/β and variance α(β+1)/β²).
- **Scope (v0):** perishable daily-use items only (shelf life ≤ 96 h, the catalog's perishability line: milk and curd today). A staple carries over in the cupboard, so what a box delivers says little about a week's use; staples stay on the rule table until v1 models stock.
- **Use (M3 §3.1 step 2):** only with `PANTRY_TWIN_ENABLED=true` (default off). Then, once a household has one locked box, the twin's `mean` replaces the rule table's daily-use quantity of milk and curd, and that week's recipe need is added on top (`reason_code: pantry_low`, params `mean, p10, p90, weeks, source`). Recorded in `intel.predictions` (kind `pantry`). The weekly job uses it; the customer-facing preview (`GET /v1/cycles/preview`) does not, so it waits on one ML call at most. It is switched on only once the eval harness shows the pre-registered target holds (MAE ≤ 15 % after 4 weeks, `just eval` prints it).
- **Eval:** scored on the same scope (milk and curd, their daily-use share) next to the rule table and a running mean of the actual daily use, with the 80 % interval's coverage.
- **v1 (P4):** NumPyro refits the cohort priors per cohort × product in `backend/training` and models stock, so staples can join; the formulas here stay.

### 3.2 Household simulator v1a (`data/simulator`, `just sim`)
Not an endpoint: a command-line tool (handbook 05 §5.11, ADR-0008) that runs against the local database only.
- **Input:**
  - the seed world from `just seed` (hub, societies, catalog, prices, festival calendar), read through the catalog services;
  - the cohorts in `data/simulator/cohorts.json` (assumptions for Rhythm and Manish to review);
  - the price episodes in `data/simulator/shocks.json` (also assumptions);
  - the arguments `--seed` (default 7), `--weeks` (8), `--start` (Mon 5 Oct 2026; prices are the hub's list on that day), `--planner cpsat|rules`, `--adoption` (share of flats, default 0.1) and `--no-shocks`.
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
  - **Box (v1a):** drafted like M3's `lines_from_plan` (planned dishes' non-optional ingredients plus the rule table plus two fruits, summed per product, less the staples the pantry still holds, in steps at the week's prices). The household then edits it, each edit with a reason:
    - `taste`: lines follow the dishes it cooks (keeping the rule share), and sometimes a liked fruit is added;
    - `stock`: the most over-stocked staples are removed or reduced;
    - `price`: a spiked item is swapped for a same-category, same-unit product that is cheaper per unit and in total, or halved. The substitute is cooked in place of the original.
  - **Pantry (v1a):** the box is delivered into a pantry and the cooking draws it down. Shortfalls are bought elsewhere. Perishables left at the end of the week are waste, and staples are waste once past their shelf life. On some items the household taps *ran out*, *wasted* or *just right* (cohort `feedback_rate`).
- **Output:** Parquet ground truth in `data/sim_truth/seed-<n>/` (git-ignored):
  - `households`, `daily_use` and `taste` (the latent truth);
  - `weeks`, `meals` and `consumption` (behaviour);
  - `boxes`, `box_lines`, `edits`, `pantry` and `prices` (v1a), with columns as in `gharbasket_sim/truth.py`;
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

Outside Postgres (S3-API object storage; SeaweedFS locally, Cloudflare R2 when hosted, ADR-0025): Parquet snapshots in `s3://gb-lake/`, model versions in `s3://gb-mlflow/models/<name>/<version>/` with a `latest.json` pointer per model, simulator ground truth in `data/sim_truth/`.

## 5. Events
**Published:** `intel.plans_ready`, `intel.forecast_ready`, `intel.routes_ready`, `intel.assignment_ready`, `intel.substitutions_learned`.
**Consumed:** `subscriptions.cycle_generated` feedback, `orders.locked`, `orders.delivered`, edit feedback (via snapshots).

## 6. Jobs
| Job | Schedule | Notes |
|---|---|---|
| `collect_mandi` | Daily 18:33 IST (`training-jobs.yml`, `backend/training`) | data.gov.in (Agmarknet) prices for Maharashtra markets → `s3://gb-lake/external/mandi/date=…/`; pushes the 4-week price index per product to `POST /v1/internal/intel/price-index` (operator token) → `intel.price_index`, read by `intel_gateway.api.price_spikes` |
| `export_snapshots` | Daily 02:03 IST (Actions, `DATABASE_URL_RO`) | The module tables through the API's SQLAlchemy models with a read-only role, in one `REPEATABLE READ, READ ONLY` transaction → `snapshots/date=<day>/<table>.parquet` in the lake. `<day>` is the IST day of the run; a partition holds only rows created (or, for events, happened) before the end of that IST day, so `--day` backfills never see later rows. One fixed Arrow schema per table. No personal data: households without owner, members or address; no society anywhere (the hub is the only geography); the monthly budget as a band (`budget_band`); every table with `data_source`. Then the point-in-time household features → `intel.feature_snapshots` (`POST /v1/internal/intel/feature-snapshots`, timezone-aware `as_of`) as of the latest order cut-off at or before the run: 21:00 IST of the previous evening for the 02:03 IST run (`orders.api.cutoff_for`). **Retention and erasure:** snapshot partitions are deleted after 180 days (a lifecycle rule on `snapshots/` in the bucket, proposed); an erased household (`DELETE /v1/me`, handbook 13) is left out of every later partition and feature run, and its random id leaves the older partitions as they age out |
| `generate_weekly_plans` | T-3 18:00 | Batch CP-SAT across CPU cores |
| `forecast_uncommitted` | T-1 21:10 | StatsForecast/MLForecast/Chronos-2 + reconciliation |
| `retrain_models` | Sunday 03:00 | Champion/challenger; promotion moves the model's `latest.json` on R2 (manual `just promote` in v1, ADR-0025) |
| `monitor_drift` | Daily | WAPE, acceptance, waste vs thresholds |

These batch jobs run in `backend/training` on GitHub Actions (daily and weekly schedules), never inside the ML service or an API request (ADR-0025, ADR-0021); results reach `intel.*` through the API's internal endpoints.

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

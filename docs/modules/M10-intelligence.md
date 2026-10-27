# M10 — Intelligence (ML & Optimisation Service)

**Status:** Draft v1.1. §3.1 (`POST /plan/week`, v0) is frozen and built in `backend/ml`; §3.8 (`POST /slots/price` and green slots) drafted 2026-10-28, frozen on approval; the rest is refined at phase start (spec-first).
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
| POST | `/slots/price` | subscriptions (customer quote) / community (weekly job) | — | Marginal insertion cost per society and slot; route cost, never a price (§3.8, S9) |
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
- **v2 (built 5 Oct 2026, F26; additive; review fixes 8 Oct):** `recipes[].preference` (0..1, null = unknown, counted as 0) and `weights.liking` (default 3). The objective adds `liking × Σ round(SCALE × preference)` over the chosen meals, on the same scale as every other term: at the defaults one meal of a fully liked dish is worth exactly one cuisine match (3) and 0.6 of a distinct dish of variety (5), so taste tilts the plan without overriding any rule or collapsing variety (on the seed week with every main liked on a 0..1 ramp, v2 keeps v1's 19 distinct dishes and raises the plan's mean liking from 0.55 to 0.68; `test_v2_liking_tilts_the_plan_without_collapsing_variety`). `score.liking` reports the mean preference of the chosen dishes. M3 fills `preference` from the ranker (§3.7): once a household has two past approved or locked boxes, `subscriptions.service.ranker_candidates` builds the ranker's features with the training job's definitions (M3 §3, "How a cycle is generated"), `intel_gateway.api.rank_basket` scores them (`/basket/rank`, `top_k` ≤ 100, the frequency fallback on any failure, recorded as `basket_rank`), and `planning.recipe_preferences` turns product scores into a dish liking (mean over scored ingredients, a candidate below the returned top 100 taking the lowest returned score; then rank-scaled to 0..1 across the mains' distinct values, ties sharing a value). The customer preview skips the ranker (one ML call at most). The rules fallback orders dishes by liking after cuisine, except in its cost-only (budget) pass, which is cheapest first. The simulator's `cpsat_v2` uses the household's observed acceptance share instead (from two weeks of history); the planners that ignore liking (v1, rules, greedy, random) plan and cache without it. The harness pairs `cpsat_v2` with `cpsat` by seed, over all weeks and over weeks ≥ 2 (before them v2 plans exactly as v1), with the verdict of eval protocol §7 (8 Oct): paired acceptance CI lower bound ≥ 0, hard rules kept, perishables ≤ v1 + 1, none below 5 seeds. Kits on busy days stay open.

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
- **v1 (built 5 Oct 2026, F21; review fixes 8 Oct):** `backend/training` refits the cohort priors with NumPyro (`pantry_priors.py`, `jobs/refit_pantry.py`, Sunday 03:03 IST). **Scope:** exactly what the serving twin observes: the job reads M3's own `subscriptions.api.daily_use_history` (the function `pantry_observations` is built on), so each observation is a locked box's daily-use share of a twin product (milk, curd: delivered minus the recipe need from the cycle's stored `meal_plan`, clipped at 0) and its tap, in that product's steps, kept fractional; a parity test checks the two on the same rows. **Model:** per product and per `data_source` (never pooled, ADR-0008), a hierarchical Gamma-Poisson over the households: cohorts by adult equivalents (`small` < 2, `medium` 2 to < 3.5, `large` ≥ 3.5), each with a weekly use **per adult equivalent** `mu_c` and a prior weight `w_c`, each household `lam_h ~ Gamma(w_c mu_c AE_h, w_c)`, each week's tap-corrected count under the Poisson log-density the conjugate update uses: once M3 multiplies `mu_c` by the household's equivalents it is exactly the prior the online update assumes, so the formulas here stay. A product or cohort with fewer than three households keeps the rule table. **Card:** the out-of-sample MAE of learned priors against the rule table: refit on each household's weeks before its last observed week, both priors scored on the held-out last weeks through the serving update over at most `OBSERVED_WEEKS` earlier weeks, the rule table with the household's own equivalents and weight 2 as M3 serves it. **Registry and promotion (ADR-0025):** the job uploads one candidate per source, `models/pantry_priors/<version>-<data_source>/priors.json` (priors fitted on all weeks, plus that card), and with `--push` only its metrics to `model_metrics` (`pantry_priors`, `pantry_priors_rule_table`; `real` is labelled `live`); it never writes `latest.json` or `intel.pantry_priors`. `just promote pantry_priors <version>-<data_source>` is the only thing that changes serving: it refuses a candidate that does not beat the rule table out of sample (`--force` overrides and the pointer says so), pushes that version's priors to `POST /v1/internal/intel/pantry-priors` (which replaces every stored prior, so a rollback leaves nothing of the newer version), and only then moves `latest.json` (`--no-push` moves the pointer alone, for a local registry). **Serving:** M3 reads `intel_gateway.api.pantry_priors(cohort)` → `{product: (prior_mean_per_ae, prior_weeks)}` and sends `prior_mean = prior_mean_per_ae × the household's equivalents`, `prior_weeks` per item (`reason_params.prior: learned | rule`), still only with `PANTRY_TWIN_ENABLED`. **Eval:** the harness adds a reference arm, the twin from priors learned on another seed's households (no target; protocol change log 8 Oct). On the simulator the learned priors do not beat the rule prior (seed 7, 5 weeks, 2 % adoption, priors from seed 11: after 4 weeks 0.255 against 0.183): the simulator draws each household's daily use around the rule table itself, and the box M3 drafts from that table carries little more than the taps, whose ×1.3/×0.7 corrections the learned mean inherits.

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
                 "wage_paise_per_s": 3, "late_paise_per_s": 50 }, "time_limit_s": 10, "max_late_s": 1800
  422:
    a window that starts after it ends, depart_s not before return_by_s, or donations without a food_bank
    (the gateway's RouteRequest refuses the same, so M7 sees a ValidationError, not a missing route)
  200:
    "status": "solved" | "fallback",                 fallback = nearest neighbour, when the solver found no plan
    "source": "osrm" | "haversine",                  the road network, or straight lines x 1.3 when OSRM is down or slow
    "routes": [ { "van", "start_s", "end_s", "stops": [ { "id", "kind": "drop" | "bank", "eta_s", "late_s" } ] } ],
    "summary": { "vans", "km", "cost_paise", "on_time", "late_min", "problems": [] },   the checker's findings
    "seconds", "model": "cvrptw-waves-v0"
```
- **Model:** OR-Tools CVRPTW with pickups and deliveries (empties and donations ride back; each donation reaches the food bank on the same van), solved in **two waves** (S-3): the earliest-window societies first, then the rest with the same vans from the moment they are back and reloaded, plus only the vans of `vans.count` the first wave left at the hub. The plan never uses more vans than the fleet. Each wave gets half the time limit.
- **Checker:** every answer is replayed on the matrix; `summary.problems` lists every rule that fails, and **M7 publishes a route only when `problems` is empty**. The rules:
  - no trip leaves before `depart_s`, and a van's next trip leaves only after it is back plus `reload_s`;
  - kg and crates within capacity leaving the hub and after every stop (crates out, empties and donation crates in);
  - each donation on the same van before its food-bank drop, and the food bank reached before its window ends;
  - a drop more than `max_late_s` (default 30 min) after its window's end; lateness below that only lowers `on_time` and adds to `late_min`;
  - every society and food-bank drop exactly once (`not served: …` names the ones left out, `visited twice: …` the repeats);
  - every van back by `return_by_s`, and no more vans than `vans.count`.
- **Fallbacks:** OSRM down, slow (over `OSRM_TIMEOUT_S`, 2.5 s) or wrong-shaped → haversine distances; a pair OSRM cannot route (`null`) → that pair's haversine estimate, logged. No plan → nearest neighbour within the fleet: each trip goes to the van free first, a van goes out again only if it is back by `return_by_s`, and the societies left over are reported in `problems`. The service down → the gateway returns `None` after `time_limit_s + ML_ROUTE_OVERHEAD_S` (6 s, covering OSRM's 2.5 s) and M7 keeps the previous day's sequence (M7 §6).

### 3.5 `POST /assign/kitchens` (v0, M-G; greedy baseline inside)
Tomorrow's tiffin portions to home kitchens for a hub (M8 §6 `assign_kitchens` at T-1 21:30, through `intel_gateway.api.assign_kitchens`, recorded in `intel.predictions` kind `assignment`).

```
  request:
    "orders": [ { "id": "<order id>", "lat", "lng", "portions", "skills": ["veg", "jain"] } ],   ≤ 2000
    "kitchens": [ { "id": "<partner id>", "lat", "lng", "max_portions", "skills": [...], "rating": 4.5,
                    "earnings_paise": 120000 } ],         ≤ 300; rolling earnings before tonight, ≤ 10^12
    "fairness_lambda": 300,            paise of cost per portion of earnings imbalance; 0 = cost only
    "rating_floor": 3.8, "max_km": 5, "paise_per_portion": 6000, "quality_paise_per_star": 500,
    "max_kitchens_per_order": 15,      only the nearest this many eligible kitchens are candidates
    "time_limit_s": 5                  wall clock, building the model included
  200:
    "status": "solved" | "partial" | "fallback" | "infeasible",
    "assignments": [ { "order_id", "kitchen_id", "portions", "km" } ],
    "unassigned": [ { "order_id", "reason": "below_rating_floor" | "no_skills" | "too_many_portions"
                                          | "out_of_reach" | "no_room" } ],
    "summary": { "portions", "kitchens_used", "km_per_portion", "cost_paise_per_portion",
                 "gini", "gini_eligible",
                 "earnings_paise": [ { "kitchen_id", "earnings_paise" } ] } | null,
    "seconds", "model": "assignment-cpsat-v0" | "greedy-v0"
```
- **Model:** CP-SAT; each order to one kitchen within reach that has the skills, the rating and the room; objective = distance (₹3 a portion-km) + quality penalty (₹5 a star below 5) + λ × (the largest deviation of an eligible kitchen's earnings from the fair share + the mean absolute deviation). Earnings are rolling + tonight and counted **in portions** (÷ `paise_per_portion`), so λ trades like for like with the cost per portion; the max is the linear proxy for the Gini in handbook 08 M-G, the mean keeps the objective from going flat when one kitchen pins the max. *Eligible* = a candidate for at least one order tonight: a kitchen with no room, below the floor, without the skills or out of reach is left out of the share and the deviations.
- **λ default 300:** the knee of a sweep on the test instance (12 orders, 4 kitchens, three seeds: knees at 200-300); on a 60-order, 8-kitchen hub the knee is at 600-700. The eval harness's sweep settles the production value.
- **Partial plans:** every order has a skip with a penalty per portion larger than any order's cost and fairness effect, so one order no kitchen can take never fails the hub. Skipped orders are listed in `unassigned` with the first reason that rules every kitchen out, or `no_room` when kitchens could cook it but are full; `status` is then `partial` (`infeasible` only when nothing is assigned). `summary` per-portion figures count the assigned portions.
- **Summary:** `gini` is over every kitchen in the request (the paper metric), `gini_eligible` over the eligible kitchens (what λ balances).
- **Time:** candidates are the nearest `max_kitchens_per_order` kitchens per order, the variables are grouped by order and kitchen in one pass, and building counts against `time_limit_s` (the solver gets the rest; under 0.05 s left → greedy). Above `max_orders_for_solver` (default 500: a 500-order, 60-kitchen hub ends within ~2 % of optimal in 5 s) greedy answers; so do instances whose objective would not fit in int64. The gateway waits `max(ml_timeout_s, time_limit_s + 5 s)`.
- **Deterministic mode:** `assign(req, deterministic=True)` (the default for in-process callers such as the eval harness) solves with one worker and a deterministic-time limit, so repeated runs give the same plan; the endpoint passes `False` (eight workers, wall-clock limit).
- **Fallbacks:** no plan in time, too many orders or numbers too large → greedy nearest kitchen with room (`status: fallback`, or `partial`; `model: greedy-v0`); the service down → the gateway returns `None` and M8's job assigns greedily itself.

### 3.6 The forecast job (v0, M-E; `backend/training`, not an endpoint)
`python -m gharbasket_training.jobs.forecast --push` at T-1 21:10 IST (M6 §6): the **uncommitted** demand per hub × product × delivery day for the 7 days after the run, with newsvendor order quantities, from the module tables with the read-only role, handed to `POST /v1/internal/intel/forecasts`. The API stores one `intel.predictions` row per hub and delivery day (kind `forecast`, `for_date` = the delivery day, payload `items[{product_id, for_date, committed, uncommitted_q50, uncommitted_qtau, order_qty, model}]` and `data_source`) and publishes `intel.forecast_ready` (hub_id, delivery_dates, model, data_source) in the same transaction; M6 reads each day through `intel_gateway.api.hub_forecast(hub_id, delivery_date)`. The backtest's metrics per model go to `model_metrics` (split `backtest`).
- **Data:** one source per run (`--data-source`, `synthetic` until the pilot; `pilot`/`live` read the orders marked `real`); every row pushed carries the label and sources are never pooled (eval protocol §2).
- **Series:** weekly buckets per hub × product from orders past `locked` (locked, picking, packed, dispatched, at_society, delivered, undelivered, redelivery, refunded); `committed` = the lines of box orders, `uncommitted` = the lines of add-on and tiffin orders (by `Order.kind`, so an add-on inside a box is committed). Only complete weeks: the run's current week is left out of training and of `committed`. Every series runs from its first sale to the run's last complete week, zeros filled; a product with no sale in the last 8 weeks is dropped (stopped).
- **Models:** StatsForecast AutoETS, CrostonOptimized, SeasonalNaive(4) and Naive ("buy last week"); a series needs 7 weeks (AutoETS's minimum) or gets the naive answer.
- **Backtest (eval protocol §3):** rolling origin, an origin every week, a 2-week horizon, as many origins as the history allows (at least 3, at most the latest 8; fewer than 3 → no backtest, the naive answer). WAPE per model over both steps of every window; the champion per hub is the model with the lowest WAPE, the baselines included (a tie goes to the simpler model). The champion's WAPE gap to SeasonalNaive and the protocol's "≥ 10 points better" check are reported as results, never a gate.
- **Intervals:** split conformal on residuals divided by each series' mean weekly uncommitted quantity (then rescaled), per horizon step, with the (n+1) finite-sample quantile: q_tau = max(0, q50 + scale × Q_tau). In the backtest each window is calibrated only on residuals whose target week was known at its origin; the empirical coverage of q_tau, the pinball loss of q50 and q_tau and MASE (scaled by the in-sample mean week-on-week change) are reported on those windows. In production all backtest residuals calibrate.
- **Delivery days:** the model is weekly; a product's weekly q50 and q_tau are split over the hub's delivery days by each weekday's share of the product's units over the last 4 complete weeks (the hub's share of orders per weekday when the product sold nothing then). Days in the run's week use the 1-week-ahead forecast, days in the next week the 2-week-ahead one. Splitting a quantile by a share is the v0 approximation (v1 reconciles).
- **Quantities:** `order_qty = committed + Q_tau(uncommitted)`, tau = C_u / (C_u + C_o) (default 0.3 / 0.5 → 0.375, `--tau`); `committed` here is the same weekday's boxes in the last complete week, which M6 replaces with the cut-off's exact count.
- **v1 (LightGBM, F25):** a global LightGBM per hub (the native booster, one model over every series of the hub; the series id is not a feature) joins the backtest as `LightGBM` and competes for champion like the others (it is the most complex, so it loses a WAPE tie). **Direct multi-step strategy: one booster per horizon step h ∈ {1, 2}.** The features of step h for a target week are counted from the origin, the week h before it: lags 1–3 (y shifted by h, h+1, h+2), the 4-week mean up to the origin, the target week of the year on the circle, the festival and fast days of the target week from M2's calendar (known in advance), and the product's mandi price index **as of the origin**: the last weekly index (the mean of a week's daily `intel.price_index` rows over markets) on or before the origin week, so step h reads the index h weeks before the target and never the target week's own; the job reads `intel.price_index` only up to the cut-off (the Sunday ending the run's last complete week). Seed 7, one thread, LightGBM's `deterministic` mode with row-wise histograms, so a rerun gives the same boosters. It takes part in a backtest window only when that window has at least 8 training rows for every step, and its scaled residuals enter the same per-step conformal calibration as the other models. All models are scored on the same windows, those where every scored model could forecast; a model that would leave fewer than 3 such windows (LightGBM short of history, AutoETS refusing a window) is left out of the comparison instead. When AutoETS refuses a window the other models answer without it; its column is absent, never Naive's answer under AutoETS's name. In production a series the champion cannot forecast gets "buy last week", labelled `Naive`. Predictions are stored as `forecast-<champion>-v1`. Weather and MinT reconciliation stay open.

### 3.7 `POST /basket/rank` and the ranker job (v0, M-B)
The next-basket ranker: which candidates belong in a household's box this week. The training job (`backend/training`, `train_ranker`, weekly) learns from every household's boxes and edits; the Space serves the model from the registry.

```
  request:  "candidates": [ { "product", "weeks_since", "frequency", "drafted", "removed_last",
                              "popularity", "price_paise" } ], "top_k": 10
  200:      "source": "ml" | "fallback", "model", "ranked": [ { "product", "score", "rank" } ]
```
- **Baskets and labels:** one basket per household x week whose cycle is `approved` or `locked` (draft, skipped and cancelled cycles are not final boxes; a skipped week is a gap); a candidate's label is 1 when it is in the final box (kept or added), 0 when drafted and removed or never taken.
- **Candidates:** the household's own past items, the frequent items of its 5 nearest households (Jaccard on their baskets dated before this week; ties broken by product id, so the data never depends on the hash seed), and the week's drafted lines.
- **Features** (as of the cut-off, from earlier weeks only, never from the final box): weeks since last bought (capped at 52, and 52 when never bought, in training and serving), share of past baskets bought, drafted this week, removed last time, share of households buying it last week, unit price from the hub's price list valid at the cut-off (else the newest earlier list holding it, else 0). `drafted_rule` was dropped in v0: removed lines lose their reason code, so it leaked the label; it returns once a draft snapshot exists. **Serving (planner v2):** M3's `ranker_candidates` computes the same features from the API's tables for the household's own past items (the only candidates it sends): weeks since kept and frequency over its full history, removed last time from the previous approved or locked box, popularity over the hub's households (training counts every household of the data source; with one hub they are the same), the price from `catalog.api.latest_prices_on` at the cycle's cut-off, and `drafted` false (the draft does not exist yet). The request carries no `drafted_rule`: the schema forbids extra fields.
- **Model:** LightGBM LambdaRank grouped by basket, one model per `data_source` (never pooled); **leave-last-basket-out** NDCG@10, Recall@10 and Recall@|basket| (eval protocol §4 and §7) against "repeat last week" (the household's previous approved or locked basket, however many weeks ago) and "top frequency", with the candidates' coverage of the final box, in the model card and in `model_metrics`.
- **Registry (ADR-0025):** `s3://gb-mlflow/models/basket_ranker/<version>-<data_source>/{model.txt, card.json}` and `models/basket_ranker/latest.json`; `--out X` writes the same layout without the `models/` prefix, which is what the Space's `MODELS_DIR` holds. The job never moves `latest.json`: `just promote basket_ranker <version>-<data_source>` is the only thing that does (also with `--out X`, the same local layout); it refuses a model whose leave-last-basket-out NDCG@10 is not above both baselines unless `--force`. The Space re-reads the pointer every `MODELS_RELOAD_S` (600 s); a malformed pointer, a corrupt model or a model with other features is logged and listed as `unreadable` on `/models`, and the last good model or the fallback answers. Without a model the fallback ranks by frequency then popularity (`source: "fallback"`).
- v1 (P4): SHAP explanations, price delta and festival features, `drafted_rule` from a draft snapshot, the ranker's preference term in planner v2.

### 3.8 `POST /slots/price` and green slots (M-H; drafted 2026-10-28, frozen on approval)
The marginal insertion cost of a society into a day's tentative van routes, per delivery slot (handbook 08 M-H, Tier 3, MS-D). Two callers use it. The customer app shows **"Neighbours already get delivery Tue 8–10, save ₹15"** when a household moves its box to that slot. M9's weekly job uses the same cost, for a whole society, as the stop cost behind its unlock threshold (M9 §3.6). The ML service returns **route costs only**, never a price. M3 turns a cost into a saving with the hub's rules, and M4 records the saving as the order's discount.

**Where a choice exists.** The van stops at a society, never at a flat: a stop is a `(society, slot)` pair (M7 §3.2, the captain does the last 50 m). A box rides on its society's delivery day (M3 §3.1). So the one household choice that changes route cost is **the time slot of this week's box on its delivery date**. A household alone at its society's stop can move to a slot where its society, or a society nearby, is already on a route. The date and the cut-off never change. A whole society's slot is the hub's choice (M3 §3.5); open question 7 covers showing the same ranking there.

**Who owns what**

| Piece | Owner | Where |
|---|---|---|
| Marginal cost per candidate and slot (metres, seconds, paise of route cost), reason, rank | M10 | `backend/ml`: `POST /slots/price` (`gharbasket_ml/slots.py`) |
| Timeouts, the rule fallback, recording | M10 gateway | `intel_gateway.api.price_slots`, `society_stop_costs`; `intel_gateway.fallback.price_slots` |
| A day's tentative routes | M7 | `logistics.api.tentative_day` (new) |
| Box slots and their capacity; the discount on the order | M4 | `orders.api.box_slots` (new); `ApprovedCycle.discount_paise` → `orders.discount_paise`, `discount_code` (M4 §3.9) |
| The quote, the saving rule, the customer endpoints | M3 | `POST /v1/cycles/{id}/slot-quotes`, `PUT /v1/cycles/{id}/slot` (M3 §3.6) |
| The saving's hub settings | M6 | `hub_ops.hubs.green_saving_share_pct`, `green_saving_max_paise` (made in PR 3) |
| Stop cost → unlock threshold | M9 | `unlock.stop_costs` (M9 §3.6) |
| Money movement | none new | The cut-off debits `total_paise`, already net of `discount_paise`, in the one `box_debit` entry. M5 does not change. |

**The tentative routes.** M7 solves day T's routes at T-1 23:15 (ADR-0026 §6), after the cut-off. A box's slot can only change before the cut-off, so no plan for T exists when a household chooses. The tentative routes are therefore **the plan M7's own fallback would publish if it ran now** (M7 §3.2: `fallback_previous`, else `fallback_nearest`), over the boxes expected on T. A day that already has a published box plan uses that plan's routes as published; M9's reference days are such days.

`logistics.api.tentative_day(session, hub_id, day, *, expected=None) -> intel_api.SlotDay | None` (new, M7):
- **Stops.**
  - With `expected` (`{(society_id, slot_id): boxes}`, from M3's cycles), there is one stop per pair, with `orders = boxes`, `crates = ceil(boxes / 3)`, `kg_tenths = boxes × TENTATIVE_BOX_KG_TENTHS` (80, i.e. 8 kg) and `empties = 0`.
  - Without `expected`, the stops are the published plan's for `day`. With no published plan, they are the stops a solve would send (M7 §3.2, from M4's orders).
  - Location, window and stop id (`<society_id>/<slot_id>`) are as in M7 §3.2. A society without a location is left out.
- **Trips.** The published plan's routes in stop order (`routes_source: "published"`). Otherwise `routing.order_previous` (`"previous"`) or `routing.order_nearest` (`"nearest"`), then `routing.fill`. These are the functions M7's fallback already uses, so no new routing code is added.
- **Hub, vans, `depart_s`, `return_by_s`:** as a solve sends them (M7 §3.2).
- **Slots:** the hub's active box slots for `day`, from `orders.api.box_slots`.
- **None** when the hub has no active box vehicle, or no hub location. Callers then show the slots unpriced, or keep the threshold.

```
POST /slots/price          Authorization: Bearer <ML_SERVICE_TOKEN>   (X-Request-ID passed through)
  request:
    "hub": { "lat", "lng" }, "depart_s": 23400, "return_by_s": 41400,      seconds after midnight IST
    "stops": [ StopIn ],                     0 to 400; StopIn as /route/solve (§3.4), id "<society_id>/<slot_id>"
                                             (M7 §3.2); the day's tentative stops without the box being priced
    "trips": [ { "van": 0, "stops": ["<stop id>", …] } ] | null,
                                             the tentative routes, a van's trips in time order, each stop on
                                             exactly one trip; null: the service builds them by nearest
                                             neighbour (§3.4's fallback)
    "vans", "service", "cost",               as /route/solve
    "candidates": [ { "id": "<society_id>", "lat", "lng", "orders": 1, "kg_tenths": 80, "crates": 1,
                      "own_stops": "join" | "ignore" } ],      1 to 50, ids unique; join: a stop of this
                                             society in a slot takes the load; ignore: the society's stops
                                             are taken off the routes first (M9 prices a stop it may already have)
    "slots": [ { "id": "<slot_id>", "window_start_s": 28800, "window_end_s": 36000 } ],   1 to 12, ids unique
    "near_m": 1000,                          0 to 5000; "neighbours" = a stop of that slot this close by road
    "max_late_s": 1800
  422 "request.invalid":
    as /route/solve, plus: a trip naming an unknown stop or a stop twice, a stop on no trip, a van
    outside vans.count, duplicate candidate or slot ids
  200:
    "source": "osrm" | "haversine",
    "base": { "trips": 6, "vans": 4, "km": 61.2, "cost_paise": 412000, "problems": [] },
                                             the tentative routes replayed by the checker (§3.4)
    "candidates": [ { "id", "slots": [ {
        "id", "rank": 1,                     per candidate: 1 = cheapest; infeasible slots last
        "feasible": true,
        "marginal_cost_paise": 640 | null,   route cost with the candidate − without (§3.4 cost model), ≥ 0
        "marginal_m": 820 | null, "marginal_s": 410 | null,
        "nearest_stop_m": 400 | null,        road metres to the nearest stop in that slot's window
        "reason": "SAME_SOCIETY" | "NEIGHBOUR_STOP" | "NEW_STOP" | "NEW_TRIP" | "INFEASIBLE",
        "position": { "trip": 2 | null, "van": 1, "after": "<stop id>" | null } | null } ] } ],
                                             trip null: a new trip; after null: first on the trip
    "seconds", "model": "slot-insertion-v0"
  401 "ml.unauthorized"
```
- **Matrix:** one table over the hub, the stops and the candidates (at most 451 points; `MAX_TABLE` is 500). It comes from OSRM within `SLOT_OSRM_TIMEOUT_S` (1.0 s). That is shorter than §3.4's 2.5 s because a customer is waiting. When OSRM fails, the service uses haversine × 1.3 (`source`), and fills gaps as in §3.4.
- **Base:** the given trips, replayed with `routing.check`. A van's first trip leaves at `depart_s`, and its next trip when it is back plus `reload_s`. Without trips, the base is `routing.nearest_neighbour`. Each candidate is priced alone against the base; candidates never see each other.
- **Per candidate and slot:**
  1. **Own stop** (`own_stops: join`). If `<candidate.id>/<slot.id>` is a stop, the candidate's load is added to it and the plan is replayed. The reason is `SAME_SOCIETY`, and the marginal cost is usually just the extra crate service time. If the added load breaks a capacity, the candidate is priced as a separate drop (step 2).
  2. **Cheapest insertion.** A drop with the slot's window and the candidate's load is tried at every position of every trip, and as a new trip. A new trip goes after a van's last trip, or on an unused van, which adds `fixed_paise_per_van`. The service keeps the cheapest position whose replay adds no checker problem to the base's own problems; problems the base already had (e.g. lateness) are tolerated. Ties go to the earlier trip, then the earlier position. The cost is `check(with).cost_paise − check(base).cost_paise`, floored at 0. A builder may compute the difference on the changed van only, but it must equal a full replay (test).
  3. **Reason:**
     - `SAME_SOCIETY` in step 1;
     - else `NEIGHBOUR_STOP` when `nearest_stop_m ≤ near_m`;
     - else `NEW_TRIP` when the best position is a new trip;
     - else `NEW_STOP`;
     - `INFEASIBLE` when no position works (costs null).
  4. **Rank:** feasible slots by marginal cost, then window start, then slot id; infeasible slots after them, by window start.
- **Deterministic and fast:** no solver and no randomness, so the same request gives the same answer. Targets on haversine: ≤ 0.5 s for 100 stops × 6 slots × 1 candidate, and ≤ 3 s for 50 candidates (M9's batch).
- **Code:** `gharbasket_ml/slots.py` reuses `routing` (`osrm_matrix`, `haversine_matrix`, `Node`, `Instance`, `check`, `nearest_neighbour`). The request is converted to a `RouteRequest` for them with `model_construct`, since an empty day has no stops. The schemas sit next to §3.4's.
- **As built** (`feat/M10-slots-price`, 2026-10-28). Read with the steps above:
  - **Trip times.** A trip leaves no earlier than `depart_s`, or than its van's return plus `reload_s`, and **just in time for its earliest window**, as `routing.nearest_neighbour` and M7's `fill` time a trip. Leaving at `depart_s` would charge a late slot's trips hours of waiting at wage, which is not a route cost.
  - **No food bank.** The request has no `food_bank`, so a stop with `donation_kg_tenths` is a 422 (`donations need a food_bank`). The gateway (PR 2) sends donations as 0; tentative stops have none. Duplicate stop ids are a 422 too.
  - **422 body.** It is FastAPI's validation body, as on `/route/solve` and the other ML routes; `request.invalid` is the gateway's reading of it.
  - **The search** replays only the van an insertion changes, through `routing.check`. The whole-day rules (`not served`, `visited twice`) are left to the final replay. The van's problems are compared as counted checker strings, so a position that makes an already very late drop later changes its "N min late" text and counts as a new problem. Two cuts keep the search fast without changing its answer: a trip already over capacity with the candidate is skipped, and positions on a trip stop once the van leaves the previous stop after the slot's end plus `max_late_s`. A new trip goes after each used van's last trip, then on the lowest-numbered unused van.
  - **The numbers** are always the whole-day replay of the chosen position. A van's difference equals it exactly when costs are whole paise (the hypothesis test at 1000 paise/km). Otherwise each van's rounding can move it by up to 2 paise (300 random days, 0 beyond).
  - **The output.**
    - `marginal_m` and `marginal_s` are floored at 0 like the cost. An insertion into time the van spent waiting has `marginal_s` 0.
    - A slot's stops are the ids ending in `/<slot_id>` that are on the routes, so stops removed under `ignore` do not count. `nearest_stop_m` is the shorter of the two road directions. A full own stop, priced as a separate drop, is therefore `NEIGHBOUR_STOP` at 0 m.
    - `SAME_SOCIETY`'s position is the own stop's trip and van, with `after` the stop before it.
    - Slots are listed in the request's order, and `rank` orders them; infeasible slots tie-break on the slot id.
    - The same request gives the same JSON apart from `seconds`.
  - **Matrix.** `routing.matrix` (OSRM within the timeout, else haversine) is now shared with `/route/solve`. `SLOT_OSRM_TIMEOUT_S` is a service setting (default 1.0, at most 2).
  - **Speed** (haversine, laptop), for 100 stops in 6 slots: 1 candidate takes about 0.03 s and 50 candidates about 0.35 s. For 400 stops, 50 candidates take about 0.6 s.
- **As built, PR 2** (`feat/M10-slot-price-gateway`, 2026-10-28; the gateway, M7, M4 and M9). Read with the gateway, `tentative_day` and M9 sections:
  - **`SlotPrices`.** `source` is `ml` or `fallback`. The ML body's own `source` (`osrm` or `haversine`) is kept as `distance_source`, since the two names clash. A fallback answer has `base` None, every `position` None, `distance_source: haversine` and `seconds` 0.
  - **`SlotCandidate.id`** is the society's UUID, so `society_stop_costs` returns `dict[uuid, int | None]`.
  - **The 422 checks are repeated in the gateway** (`SlotPriceRequest`), as for `/route/solve`. A day the service would refuse makes no call: over 400 stops, no slot, duplicate ids, or a stop on no trip. It is logged as `slot_request_invalid`. `price_slots` then answers with the fallback, and `society_stop_costs` skips that day.
  - **Timeouts.** The gateway uses `min(ml_slot_timeout_s, ml_timeout_s)` (`slot_timeout_s`) when it calls, instead of a settings validator; `ML_SLOT_TIMEOUT_S` is at most 10. All four POSTs share one helper, `_post(timeout_s)`; the other calls behave as before.
  - **The fallback.**
    - A slot's stops are the ids ending in `/<slot_id>`, as in PR 1. With `own_stops: ignore`, the candidate's own stops are skipped.
    - `near_m` is the request's default, 1000.
    - `nearest_stop_m` is `d`: 0 for `SAME_SOCIETY`, and null for `NEW_TRIP`, where `d` is measured to the hub.
  - **`society_stop_costs(..., session=None, for_date=None)`.** `for_date` is the run's date, for recording. A society is recorded only when the service answered at least one day for it. Candidates go 50 to a call, so a hub with more than 50 societies to price takes more than one call per day.
  - **`tentative_day`.**
    - **The published plan wins over `expected`.** That plan exists only after the cut-off, when M3 no longer quotes.
    - **Published routes.** Cancelled routes and stops that are not drops are left out. So are a route whose vehicle is no longer active and a society without a location.
    - **`expected`.** A pair is left out when its count is 0 or less, or when its slot is not one of the hub's active box slots that date.
    - **`return_by_s`** is the latest window end of the stops **and the hub's box slots**, plus 90 minutes, and at least `depart_s` + 1 h. A candidate in a later slot than any stop can then still be priced.
    - **`SlotDay`** also carries `hub_id`, `day` and `routes_source`, which are never sent.
  - **M7 internals.**
    - `solver._fleet`, `_previous_order` and `_stops` became `fleet_of`, `previous_order(session, hub_id, kind, day)` and `order_stops`.
    - `views` now imports `solver` as a module, so `solver` can be imported first. This breaks the import cycle views → plans → solver → attempts → views.
  - **M4.** `box_slots` returns `SlotAvailability { id, label, start, end, capacity, booked, available }` for any date. It uses `service.slot_loads(session, hub_id, on, kind)`, which is `slots_on` without the date-range check.
  - **M9.**
    - `unlock.stop_costs(session, http, settings, societies, today)` answers `{}` without an HTTP client, so the job keeps every threshold. A society without a hub or a location keeps its threshold.
    - The job prices every society first, then locks each society row as before.
    - The reference days are the 7 days before `today` (IST, from `scheduled_for`) whose `tentative_day` has `routes_source: published`.
- **As built, PR 3**, split in two stacked PRs because it came to about 720 hand-written lines (2026-10-28). **3a** `feat/M3-green-slots` is M3 and M6 (migration `9c2f7a41d6e3`). **3b** `feat/M4-green-discount` is M4 §3.9 and the hand-over of the saving (migration `4e81b0c95a27`). Until 3b, an approved box's order keeps its slot and gets no discount; the switch is off by default. Read with the saving, the customer contract and M4 §3.9:
  - **`CycleOut` also gains `fees_paise`.** M9's follow-up 6 (the fee shown before approval) was never built, and `payable_paise` needs it. It is the order's frozen fee once approved, else `orders.api.box_fee`; a preview without a hub shows 0. The `box_ready` and `cutoff_reminder` payloads still have no fee.
  - **`discount_paise` on the cycle** is `min(slot_saving_paise, total_paise)`, so `payable_paise` stays ≥ 0 when lines are removed after a move. M4 caps the order's discount at its subtotal in the same way.
  - **Infeasible slots.** The contract's `reason_code` has no infeasible value, so a slot the service answers `INFEASIBLE` shows as `unpriced`, with `rank` null, `green` false and saving 0. A box can still move there when the slot has room.
  - **`reason_params`** is `{ metres }` (rounded half up to 100 m) for `neighbour_stop` and `new_stop`, and null otherwise.
  - **A quote belongs to the slot it was made for.** The quote row's `current_slot_id` must still be the cycle's slot. After a move, `POST` makes a new quote, and a `PUT` with an older quote gets 409 `subscriptions.quote_expired`.
  - **`PUT` to the cycle's current slot** changes nothing: 200, the same version, and the saving it already has stays. Re-confirming a slot never drops an honoured saving.
  - **`available`** is read live on every answer, including a reused quote. A slot that has left the hub's active list since shows 0.
  - **`prediction_id` stays null.** `price_slots` does not return the prediction's id. The prediction is still recorded (kind `slot_price`, subject the household, `for_date` the delivery date).
  - **Both endpoints need `cycles:write`** (the owner). The switch is the first dependency, so with it off a signed-in call is a 404 even without an `If-Match` or a valid body.
  - **`weekly_cycles.slot_quote_id` has no foreign key.** A key both ways would make a cycle between the two tables. Quotes cascade with their cycle, and `delete_for_user` deletes them by household (counted as `slot_quotes`). Quotes are not in the data export; they are derived route data.
  - **M6.** `HubIn` accepts the two settings on create as well, like `pre_live_box_fee_paise`. The seed's refresh leaves them as an admin set them.
  - **Migration (3a)** `20261028_1500_subscriptions_green_slots` (`9c2f7a41d6e3`, down_revision `3e9a4c71b5d8`, main's head when it was built), with M3's table and columns and M6's two hub columns. M4's `orders.discount_code` moved to 3b's own migration. It is re-pointed at merge after #382 and #386. Every new column is nullable or has a server default, so existing rows need no backfill.
  - **M4 (3b).** `ApprovedCycle.discount_paise` is the cycle's `slot_saving_paise`, and `discount_code` is `green_slot` when it is above 0. `subscriptions.box_approved` carries both. On a re-approval, the slot moves first, in a savepoint, and then the lines and the discount are set. So `orders.slot_full` leaves the booking, the lines and the discount as they were. A move adds an `approved → approved` timeline row with `{ cycle_version, slot_id }`. The order's version goes up once for a move or a discount change, even when the lines are the same.
  - **M5 does not change** (checked): `debit_and_lock` debits `total_paise`, which is already net of the discount, and `refundable_paise` is that total less what was refunded.
  - **Migration (3b)** `20261028_1510_orders_green_discount` (`4e81b0c95a27`, down_revision `9c2f7a41d6e3`) adds `orders.orders.discount_code` (nullable, `green_slot` or null), so no backfill.

**The gateway (`intel_gateway.api`)**
- **`price_slots(http, settings, day: SlotDay, candidates: list[SlotCandidate], *, session=None, subject_type=None, subject_id=None) -> SlotPrices`.**
  - It calls the ML service within `ml_slot_timeout_s`, a new setting (`ML_SLOT_TIMEOUT_S`, default 2.0 s, at most `ml_timeout_s`).
  - On no service configured, a timeout, an HTTP error or a malformed body, `fallback.price_slots(day, candidates)` answers instead. It never returns None.
  - `SlotPrices` is the 200 body plus `source: "ml" | "fallback"`.
  - With `session` and a subject, the answer is kept in `intel.predictions` (kind `slot_price`, subject `household` or `society`, `for_date` the day, payload the answer, which holds society points only, never a flat). The caller commits.
- **Rule fallback** (`fallback.price_slots`, plain arithmetic, no trips), for each candidate and slot:
  - A stop of the candidate's own society in the slot (and `own_stops: join`): `SAME_SOCIETY`, detour 0.
  - Otherwise `d` is the haversine × 1.3 distance to the nearest stop in that slot's window. With no stop in the window, `d` is measured to the hub and the reason is `NEW_TRIP`.
  - `detour_m = 2 × d`, an upper bound on an insertion next to that stop (triangle inequality).
  - `marginal_s = round(detour_m / 6.0 × traffic_factor) + drop_s (not for SAME_SOCIETY) + per_crate_s × crates`.
  - `marginal_cost_paise = round(detour_m × paise_per_km / 1000) + marginal_s × wage_paise_per_s`.
  - Reason `NEIGHBOUR_STOP` when `d ≤ near_m`, else `NEW_STOP`. The fallback never answers `INFEASIBLE`, since it does no capacity check. Ranks follow the same rule; `model: "slot-nearest-v0"`.
- **`society_stop_costs(http, settings, days: list[SlotDay], candidates, *, session=None) -> dict[str, int | None]`** (M9 §3.6). It uses the ML service only, within `ml_timeout_s` per day: a rule estimate never moves a threshold, as M9 already decides for M10 being down.
  - Per society, the result is the least feasible `marginal_cost_paise` over every day and slot.
  - It is None when no day was answered by the service, or when no slot was feasible.
  - It records one `slot_price` prediction per society (subject `society`, `for_date` the run's date, payload `{ cost_paise, by_day }`).
  - This is the "M10 follow-up on `/slots/price`" that M9 names as `society_stop_cost(society_id)`. It takes the hub's societies in one call per day, so the weekly job makes at most 7 calls per hub.

**The saving (M3, `subscriptions/green.py`; all integer paise).** For a cycle, M3 builds the inputs as follows:
- **The day:** `logistics.api.tentative_day(session, hub_id, delivery_date, expected=…)`. `expected` counts M3's cycles for that date that are not `skipped` or `cancelled`, by `(society_id, slot_id)`, **without this cycle**. The household's own box is what is being priced.
- **The candidate:** the household's society (`community.api.society_points`) with one box (`orders 1, kg_tenths 80, crates 1`, `own_stops: join`).
- **The slots:** the hub's active box slots that date.

Write `m(s)` for a slot's `marginal_cost_paise`, and `m₀` for the cycle's current slot's. Then:
- `green(s)` = `feasible` and the reason is `SAME_SOCIETY` or `NEIGHBOUR_STOP`.
- `saving(s)` = 0 for the current slot, for an infeasible slot, and whenever `m₀` is null. Otherwise:
  ```
  raw    = max(0, m₀ − m(s)) × share_pct // 100
  saving = min(raw, max_paise, the cycle's total_paise) // 100 × 100    whole rupees, rounded down
  saving = 0 if saving < MIN_SAVING_PAISE (500)                         no "save ₹2"
  ```
- **Hub settings (M6):**
  - `green_saving_share_pct` (0 to 100, default 50): the share of the route saving passed on;
  - `green_saving_max_paise` (0 to 10,000, default 1,500, the handbook's ₹15).
  - Either one at 0 means no money; the green badges still show. Both are on `GET`/`PATCH /v1/admin/hubs/{id}` and `HubRef`, like `pre_live_box_fee_paise`.
- **Global switch:** `GREEN_SLOTS_ENABLED` (API setting, default `false`; on in staging for MS-D). Off means the two M3 endpoints answer 404 `subscriptions.green_slots_off`, and nothing else changes. That is the cut (handbook 14's cut order).
- **Only real savings.** A household sharing its stop with neighbours saves the route almost nothing by leaving, so `m₀` is near 0 and no slot shows money. The discount appears only where moving removes a stop that exists for that household alone. So it never becomes a blanket price cut.
- **Never a surcharge in v1.** A costlier slot shows `saving_paise: 0` and can still be chosen when it has room (open question 2). A price is never negative: the saving is at most the lines' total.

**Customer contract (M3 §3.6)**

```
SlotOption : { "slot": { "id", "label", "start", "end" },
               "current": true,                    the cycle's slot now
               "available": 83,                    M4's capacity left that date (orders.api.box_slots)
               "green": true,
               "saving_paise": 1500,               off this box if it moves here; 0 for the current slot
               "rank": 1 | null,                   1 = cheapest for the route; null when unpriced
               "reason_code": "same_society" | "neighbour_stop" | "new_stop" | "new_trip" | "unpriced",
               "reason_params": { "metres": 400 } | null }   rounded to 100 m; never a society's name
SlotQuote : { "id", "cycle_id", "delivery_date", "expires_at",        15 minutes after it was made
              "source": "ml" | "fallback" | "unpriced",                 unpriced: no tentative day
              "items": [ SlotOption ] }                               every active box slot of the hub
                                                                      that date, by start time

POST /v1/cycles/{id}/slot-quotes     owner; no Idempotency-Key (it moves no money)
  201 SlotQuote                      a new quote (one ML call, recorded as slot_price)
  200 SlotQuote                      the cycle's quote that has not expired (no ML call)
  409 "subscriptions.cutoff_passed" · 409 "subscriptions.not_editable" (draft, skipped, cancelled, locked)
  404 "subscriptions.green_slots_off"

PUT /v1/cycles/{id}/slot             owner; If-Match (the cycle's version)
  request : { "slot_id", "quote_id" }
  200 CycleOut (version + 1)         the box's slot is the new one; the item's saving is on the cycle
  409 "subscriptions.cutoff_passed" · 409 "subscriptions.not_editable" · 409 "subscriptions.quote_expired"
  409 "orders.slot_full" · 412 "subscriptions.version_conflict" · 404 "subscriptions.green_slots_off"
  422 "subscriptions.quote_invalid"  not this cycle's quote, or the slot is not in it

CycleOut gains:
  "green_slot": { "quote_id", "saving_paise": 1500, "reason_code": "neighbour_stop" } | null,
  "discount_paise": 1500,            = green_slot.saving_paise, else 0
  "payable_paise"                    now total_paise + fees_paise − discount_paise; total_paise stays the
                                     lines' sum, which is what Budget Lock checks
```
- **When:** for a cycle that is `awaiting_approval` or `approved`, before the cut-off, the same window as line edits. Only the time slot changes; `delivery_date` and `cutoff_at` stay.
- **Showing.** The Box screen's slot row opens a sheet of `items`. It renders the chip from `reason_code`, the slot and the saving, through the localised strings (handbook 08 §8.4), never LLM text. For example, `neighbour_stop` with 1500 shows "Neighbours already get delivery Tue 8–10, save ₹15", and `same_society` with 0 shows "Your society's delivery slot". No mock exists yet; the sheet is new.
- **Honouring the shown price.** A `PUT` with a quote that has not expired copies that slot's `saving_paise` and `reason_code` onto the cycle (`slot_quote_id`, `slot_saving_paise`). The saving then stays until the cut-off, whatever later bookings do to the routes. An expired quote gets 409 `subscriptions.quote_expired`. The app then asks for a new quote and shows its numbers before the customer confirms again. A different saving is never applied silently. Moving to a slot whose item shows 0 clears the saving.
- **An approved cycle.** In the same transaction, M3 calls `orders.api.create_from_cycle` with the new `slot_id`, `version + 1` and `discount_paise`. M4 moves the booking under the slot's row lock (M4 §3.9). On `orders.slot_full`, nothing changes. For a cycle not yet approved, M3 refuses a slot whose `available` is 0, and approval enforces capacity as today.
- **`subscriptions.box_approved`** gains `discount_paise` and `discount_code` (`"green_slot"` | null).
- **Next week** is generated on the society's slot again. The choice is per week (open question 1).
- **Tables:** `subscriptions.slot_quotes` (id, cycle_id, household_id, delivery_date, current_slot_id, items jsonb, source, prediction_id null, created_at, expires_at; index (cycle_id, expires_at)). `subscriptions.weekly_cycles` gains `slot_quote_id` null and `slot_saving_paise` bigint default 0. Both are erased with the household (M3's `delete_for_user`), and the predictions by `intel_gateway.api.delete_for_user`.
- **Privacy:** the candidate is the society's point, never the flat. Customers see rounded metres and no other society's name or flat. Route costs (`marginal_cost_paise`) stay in the quote row and the prediction; they are not in the response.

**Timeouts and fallbacks**

| Call | Budget | When it fails |
|---|---|---|
| OSRM table in the ML service | `SLOT_OSRM_TIMEOUT_S` 1.0 s | Haversine × 1.3 (`source: haversine`) |
| Gateway → `/slots/price` for a customer quote | `ML_SLOT_TIMEOUT_S` 2.0 s | Rule fallback; the quote says `source: fallback` and pays the same rule (open question 4) |
| Gateway → `/slots/price` for M9's job | `ML_TIMEOUT_S` 3 s per day | None: the threshold is kept (M9 §3.6) |
| `tentative_day` is None (no box vehicle, no hub point), or the society has no point | — | The quote's `source` and every item are `unpriced`, with saving 0 and green false; moving still works (no ML call) |
| `GREEN_SLOTS_ENABLED=false` | — | 404 `subscriptions.green_slots_off`; no ML call |

**Tests the builders write** (per PR, below):
- **ML (PR 1):**
  - `SAME_SOCIETY` costs exactly the extra crate service × wage;
  - a candidate on the line between two stops of a trip adds ≈ 0 m; a far one is `NEW_STOP` with a positive cost; an empty day gives `NEW_TRIP` in every slot; full vans give a new trip with `fixed_paise_per_van`, or `INFEASIBLE`;
  - a window the routes cannot meet is `INFEASIBLE`;
  - `NEIGHBOUR_STOP` exactly at `near_m`, and not 1 m beyond;
  - `own_stops: ignore` equals pricing against the base with that society's stops removed;
  - a hypothesis property: the per-van difference equals a full replay, and costs are ≥ 0;
  - the rank rules, and the same request twice gives identical JSON;
  - given trips against `null`; OSRM down or slow → haversine; gaps filled;
  - every 422 above, and 401 without the token;
  - 100 stops × 6 slots under 0.5 s.
- **Gateway, M7 and M9 (PR 2):**
  - no service, a timeout, a 500 or a malformed body → the fallback with `source: fallback`, and the prediction recorded with its source; `ml_slot_timeout_s` is the timeout used;
  - the fallback's numbers on a hand-computed three-stop example;
  - `society_stop_costs` is the least over days and slots, ignores infeasible slots, and gives None when the service is down;
  - `tentative_day`: a published plan's trips; otherwise the previous order or nearest neighbour; `expected` counts → stops with the default loads; unlocated societies left out; no box vehicle → None;
  - M9's job with a fake gateway: priced → `clamp(ceil(cost / 4000), 10, 60)` with source `model`; None → kept; pinned and live societies skipped;
  - import-linter stays green.
- **Product (PR 3):**
  - **The saving rule** (unit and hypothesis): share, cap, the whole-rupee floor, the ₹5 minimum, 0 for the current slot, the cap at the lines' total; `payable_paise ≥ 0`.
  - **Quotes:** a second POST within 15 min makes no ML call; after expiry it makes a new quote; the flag off → 404 with no ML call; a fallback quote says so.
  - **`PUT`:** expired → 409; a foreign quote or a slot outside the quote → 422; after the cut-off → 409; a stale `If-Match` → 412; on an approved cycle the order's slot and discount change in one transaction, and `slot_full` rolls both back.
  - **M4:** the discount is capped at the subtotal; the cut-off debits `subtotal + fee − discount` once; a whole-box refund returns what was paid; a society that goes live before the lock drops the fee and keeps the discount.
  - **E2E (MS-D)**, on the seed:
    - a pre-live society with one household on Tue 10–12, and a live society 400 m away on Tue 8–10;
    - the quote shows Tue 8–10 as green with "save ₹15"; the household moves and approves;
    - the cut-off debits the lines + ₹30 − ₹15;
    - the 23:15 plan has the pre-live society's stop in the 8–10 wave, next to its neighbour.

**Evaluation (paper; Tier 3, reported, never a gate).** `backend/training` eval gains `slot_pricing.py`, which `just eval` runs when the harness has the routing fixtures:
- **Instances:** spike S-3's synthetic Kharghar mornings (30, 50 and 100 societies, 5 seeds). Per instance, 20 random candidate societies are priced over the slots.
- **The truth** is the CVRPTW cost (§3.4) with the candidate minus without it, as the mean of 3 solver seeds, since two heuristic solves differ by noise. It is compared with the insertion estimate (on haversine, for reproducibility) and with the rule fallback.
- **Metrics:**
  - **top-1 agreement:** the estimate's cheapest slot is the truth's cheapest, or within ₹1 of it;
  - Spearman ρ across a candidate's slots;
  - MAE in ₹ per stop.
- **Pre-registered hypothesis** (with this spec; PR 1 adds it to the eval protocol's change log): insertion top-1 ≥ 80 % and ρ ≥ 0.7, both better than the rule fallback.
- **Storage:** rows go to `intel.model_metrics` (`slot_insertion`, `slot_nearest`).
- **Not in scope:** the business figure (₹ and km per order with green slots) needs the simulator to model slot choice (open question 9).

**PR split** (about 400 hand-written lines each, tests not counted):
1. **`feat/M10-slots-price` (`backend/ml`):** the schemas, `slots.py`, the endpoint and its tests (~350 lines). This is an ML PR, so it gets an Opus review and test run before merge (§11.3).
2. **`feat/M10-slot-price-gateway` (API, no migration, ~380 lines):**
   - `SlotDay`, `SlotCandidate`, `SlotPrices`, `price_slots`, `society_stop_costs`, `fallback.price_slots` and the `ML_SLOT_TIMEOUT_S` setting;
   - `logistics.api.tentative_day` and `orders.api.box_slots`;
   - M9's `unlock.stop_costs`, with the job passing `ctx.http` and `ctx.settings`.

   It can be built alongside PR 1 against a fake ML service, and it also gets the Opus review (`intel.*`).
3. **`feat/M3-green-slots` (~420 lines; one migration, `YYYYMMDD_HHMM_subscriptions_green_slots`):**
   - the migration holds M3's table and columns, M4's `orders.orders.discount_code` and M6's two hub columns;
   - the M3 endpoints and the saving rule;
   - M4's `ApprovedCycle.discount_paise` and `discount_code`, the slot move on re-approval and `OrderOut.discount_code`;
   - M6's hub fields and `GREEN_SLOTS_ENABLED`, then `just gen-api`.

   It changes the frozen M3, M4 and M6 contracts, so both owners review it. The mobile slot sheet is Yash's own PR, against the generated client.

**Open questions for the owner** (each with the recommended answer)
1. **Where does a household choose?**
   - *Recommended:* the time slot of this week's box on its delivery date, as specified (per week).
   - *After the pilot:* a standing household slot, re-quoted at each generation.
   - *Rejected:* another day of the week, which would move the cut-off and the plan's week.
2. **Costlier slots (off-route).**
   - *Recommended for v1:* allowed when there is room, with a saving of 0 and no fee.
   - An off-route fee (handbook 02 "delivery fees … off-route slots") is a later pricing decision, taken with M12's unit economics.
3. **Numbers.** *Recommended:* share 50 %, cap ₹15, minimum ₹5, rounded down to whole rupees, all per hub except the minimum.
4. **Savings on fallback quotes.**
   - *Recommended:* pay the same rule. The cap bounds the risk, and `source` is recorded, so AI health counts fallbacks.
   - *Alternative:* badges only, with no money, when the quote is from the fallback.
5. **Quote life.** *Recommended:* 15 minutes. Once applied, the saving is honoured until the cut-off and never re-priced.
6. **"Neighbours".** *Recommended:* a stop within 1 km by road in the same slot (`near_m`).
7. **The hub's delivery-day page** (M3 §3.5).
   - *Recommended:* after MS-D, a small M3 follow-up shows the same ranking per (weekday, slot) for a whole society, with no money.
   - Most of the route saving sits there, because a society's slot is the hub's choice.
8. **Threshold reference.** *Recommended:* the least cost over the past 7 days' published plans (the slot the hub would pick), not the median; a rule estimate never moves a threshold.
9. **Simulator slot choice** (v1b, for the business figure). *Recommended:* only if ahead after MS-D; the insertion accuracy is enough for the paper's M-H paragraph.
10. **A second stop for a society that has a captain.** *Recommended:* no change and no limit in v1. M7 already makes one stop per (society, slot), and the captain takes each handover until 20:00 (ADR-0026 §6). A society without a captain is in rider mode.

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
| `intel.pantry_priors` | cohort (small, medium, large), product_id, prior_mean_per_ae (base units a week per adult equivalent), prior_weeks, version, updated_at — the promoted `pantry_priors` version's, replaced whole by `just promote` (§3.3 v1) |
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
| `forecast` (`backend/training`) | T-1 21:10 IST (Actions, cron-job.org for the minute) | StatsForecast v0 (§3.6); MLForecast and reconciliation in v1 |
| `retrain_models` | Sunday 03:00 | Champion/challenger; promotion moves the model's `latest.json` on R2 (manual `just promote` in v1, ADR-0025) |
| `refit_pantry` (`backend/training`) | Sunday 03:03 IST (`training-jobs.yml`, `DATABASE_URL_RO`) | The pantry twin's cohort priors (§3.3 v1), one candidate per data source → registry `models/pantry_priors/<version>-<data_source>/` and its out-of-sample metrics → `model_metrics`; serving (`intel.pantry_priors`) changes only through `just promote` |
| `monitor_drift` | Daily 03:33 IST (`jobs-staging.yml`; API job) | For the last 7 delivery days, per hub and data source: the forecast stored for that delivery day (`for_date`), its uncommitted q50 per product against that day's sold lines of add-on and tiffin orders (`orders.api.sold_lines`: every status past `locked`, the forecast job's `SOLD`, classified by the order's kind) of the forecast's own source (`synthetic`, or orders marked `real` for `pilot`/`live`) → `model_metrics` split `live`, labelled with that source (never `mixed`), with `drift` = 1 when the realised WAPE is more than 10 points worse than the newest `wape`/`backtest` row of the same model, hub and source (no such row: `wape` only, `backtest_wape` null); plan acceptance per plan source and cycle data source (`subscriptions.api.acceptance_on`; a `real` cycle is labelled `REAL_DATA_SOURCE`, `pilot` until launch) → `planner_<source>` `acceptance`. Once per hub, source and day (details carry `hub_id`, `for_date`). Waste waits for M6 |

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
- Slot pricing (§3.8): the per-PR list there (insertion, fallback, the saving rule, honouring a quote, the MS-D E2E).

## 10. Dependencies
Reads Postgres snapshots (read-only role). Writes results only to `intel.*`. Other modules call it only through `intel_gateway.api`.

## 11. Open questions
- Embedding model confirmation (bge-m3 vs smaller multilingual model): a stretch item after MS-C (ADR-0025 roadmap).
- Nutrition targets per age band (ICMR-NIN RDA tables) for the meal planner.
- Whether to fine-tune Chronos for the paper (optional, free Kaggle/Colab GPU).
- Green slots (§3.8): ten open questions with recommendations, listed at the end of that section.

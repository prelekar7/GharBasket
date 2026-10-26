# M12 — Console & Analytics

**Status:** Draft v2. §3.1–§3.9 were drafted on 2026-10-09 and are frozen for S9 on approval. They cover how M12 reads other modules, the fact tables and materialised views with their refresh jobs, the today board, KPIs, unit economics and P&L, cohorts and partner earnings, impact, the public society page, AI health and the common rules. AI health (§3.8) is built on `feat/M10-forecast-v1` and is not on `main` yet.
**Owner:** Prathamesh Relekar (backend), Yash Salunkhe (screens) · **QA:** Rhythm Thakur, Manish Raje
**Phase / Sprint:** Shell in P1 / S0; screens grow with each phase (P2–P5). The analytics backend (§3.1–§3.7) is S9, for MS-D ("impact dashboard").
**Deployables:** `frontend/console` (Next.js 16) + analytics endpoints in the API (`gharbasket.modules.analytics`, read-only towards every other module)
**Postgres schema:** `analytics` (M12's own fact tables, materialised views over them, assumptions and refresh runs)

## 1. Purpose
The web console for admins, hub managers, franchise owners and NGO coordinators, plus the public society pages.

The analytics module serves read-only numbers: the hub's today board, KPIs, unit economics and the monthly P&L, cohort retention, partner-earnings distribution, impact and AI health.
- **How it reads.** It reads other modules **only through their `api.py` facades** (§3.1), copies what it needs into its own `analytics` tables, and computes materialised views over those tables. It never writes to another schema.
- **What it holds.** No name, phone, flat, address or health field ever reaches the `analytics` schema or an M12 response.

## 2. User stories
| # | Story | Acceptance criteria |
|---|---|---|
| US-1 | As a hub manager, I want a today board (orders, procurement, packing, routes) so that I run the morning in one place. | Live counts per stage; an "attention" list whose rows link to each workflow, open order issues linking to M4's triage queue (§3.3). |
| US-2 | As a franchise owner, I want P&L and unit economics so that I know if my hub is profitable. | Contribution per box = AOV × margin − (delivery + packing + captain + payment fee + CAC); each input is measured or an editable assumption, and says which (§3.4). |
| US-3 | As an admin, I want KPIs (active households, acceptance rate, waste per 100 kg, on-time %, partner earnings) so that I monitor the platform. | Weekly series with trend; filter by hub, dates and data source; never pooled across data sources (§3.3). |
| US-4 | As the team, I want cohort retention so that we can report in the paper. | Weekly cohorts by signup week, per data source (§3.5). |
| US-5 | As an NGO coordinator, I want impact numbers so that we can report outcomes. | Items, kilograms, meals and people reached for their NGO; a CSV for the month (§3.6). |
| US-6 | As a visitor, I want a society landing page so that I can join with my neighbours. | `/s/<society>` server-rendered with unlock progress and a join button; aggregates only, nothing under 5 households (§3.7). |
| US-7 | As an admin, I want a command palette so that I navigate quickly. | ⌘K finds pages, orders, households, partners (console only; search goes through each module's own endpoints). |
| US-8 | As an admin, I want to see the research metrics live per data source so that the pilot's numbers match the paper's definitions. | The live counterparts of the eval protocol's metrics, mapped in §3.5. |

## 3. API endpoints
| Method | Path | Permission / role | Notes |
|---|---|---|---|
| GET | `/v1/analytics/hubs/{hub_id}/today?date=` | hub_manager (hub), franchise_owner (hub), admin | Live board (§3.3) |
| GET | `/v1/analytics/kpis?hub_id=&from=&to=&grain=&data_source=` | hub_manager (hub), franchise_owner (hub), admin | §3.3 |
| GET | `/v1/analytics/societies?hub_id=&data_source=` | hub_manager (hub), franchise_owner (hub), admin | Per-society aggregates (§3.3) |
| GET | `/v1/analytics/unit-economics?hub_id=&from=&to=&data_source=` | franchise_owner (hub), admin | What-if overrides as query parameters (§3.4) |
| GET | `/v1/analytics/pnl?hub_id=&period=&data_source=` | franchise_owner (hub), admin | A month's statement (§3.4) |
| GET / PUT | `/v1/analytics/hubs/{hub_id}/assumptions` | GET: franchise_owner (hub), admin · PUT: the same, If-Match | §3.4 |
| GET | `/v1/analytics/cohorts?data_source=&hub_id=&weeks=` | admin | §3.5 |
| GET | `/v1/analytics/partner-earnings?hub_id=&kind=&from=&to=&data_source=` | hub_manager (hub), franchise_owner (hub), admin | Distribution only, no individual (§3.5) |
| GET | `/v1/analytics/impact?scope=&hub_id=&ngo_id=&month=&format=` | ngo_coordinator (own NGO), hub_manager (hub), franchise_owner (hub), admin | JSON or CSV (§3.6) |
| GET | `/v1/analytics/ai-health` | admin | Built (§3.8) |
| GET | `/v1/public/societies/{slug}` | Public | The society landing page (§3.7) |
| POST | `/v1/internal/jobs/refresh_analytics` · `refresh_analytics_nightly` · `rebuild_analytics` | `JOBS_TOKEN` | §3.2 |

`/v1/analytics/experiments/{key}` from Draft v1 is cut from S9 (owner, 2026-10-09; §11): nothing assigns experiments yet.

### 3.1 How M12 reads other modules (decided in this draft)
**Decision: facts through facades, views over M12's own tables.**
- **The refresh job.** M12's refresh job (§3.2) calls each module's `api.py` for **fact rows**: one row per order, cycle, route, hub-day, donation and so on. Those rows hold only ids, dates, statuses, counts and paise. The job upserts them into M12's own `analytics.fact_*` tables.
- **The views.** The materialised views are built **only from `analytics` tables**.
- **What M12 never does.** M12 has no SQL that names another module's schema, and no database role beyond the API's own.

**Why** (handbook 04 §4–§6, handbook 05 §5.2 rule 5, [ADR-0014](../adr/0014-final-stack-and-service-based-architecture.md)):
- **The rule.** "A module writes only to its own schema. Other modules read through its `api.py` facade." Nothing in the ADRs or handbook 04/05 makes an exception for analytics. Draft v1's "read-only views over all module schemas (granted by a read-only DB role)" would have been the only cross-schema read in the monolith, and import-linter cannot see SQL.
- **Postgres would couple the migrations.** A materialised view in `analytics` that selects from `orders.orders` makes Postgres refuse M4's migrations that drop or retype a column the view uses, until the view is dropped. The owner of `orders` would then have to change the `analytics` schema in its own migration, which breaks "one module, one schema". Module-owned reporting views (`orders.rpt_*`) do not fix this: M12's views would still depend on them across schemas.
- **There is a precedent.** AI health (§3.8) already reads M10 through `intel_gateway.api.model_health`.
- **The volume is small.** The pilot locks about 2,000 orders a week (handbook 05 §5.8), and the simulator's runs are of the same order. A 15-minute pull of four days of facts, and a nightly pull of 35 days, fit easily in the free tier.
- **The facades become the contract.** Each `*_facts` function is a typed, versioned contract owned by its module, reviewed like any other facade change. The rows hold no personal fields, so the facades do not widen anyone's access to personal data.

**Rejected:**
- (a) Materialised views selecting directly from other schemas, for the coupling and the rule above.
- (b) Module-owned reporting views read through a read-only role, which has the same coupling one step removed.
- (c) Consuming every event into M12's own tables. Events do not carry every field the KPIs need (slot windows, line counts, costs), and a missed or reordered event would silently skew money numbers. Pulling facts by date window is idempotent and self-healing.

**The read-only database role** of [ADR-0025](../adr/0025-ml-runtime-spaces-actions-r2.md) stays what it is: `backend/training` in GitHub Actions reads Neon for training and the eval harness. The API's analytics module does not use it.

**Facades M12 calls** (each is new unless marked; all keyword-only, `date_from`/`date_to` are IST delivery or business dates, `hub_ids=None` means all; each returns rows with `data_source` and no personal field; listed as follow-ups in §10):

| Module | Function | Rows |
|---|---|---|
| M1 | `identity.api.household_facts(session, *, created_from=None, ids=None)` | household_id, hub_id, society_id, created_on, status, data_source |
| M3 | `subscriptions.api.cycle_facts(session, *, date_from, date_to, hub_ids)` | cycle_id, household_id, hub_id, society_id, week_start, delivery_date, status, auto_approved, approved_before_cutoff, edits, plan_source, ran_out_taps, wasted_taps, data_source |
| M4 | `orders.api.order_facts(…)` | order_id, household_id, hub_id, society_id, kind, delivery_date, slot_end, status, total_paise, fees_paise, refunded_paise, credited_paise, lines, short_lines, delivered_at, attempts, data_source |
| M4 | `orders.api.issue_facts(…)`, `open_issue_summary(session, hub_id)`, `today_counts(session, hub_id, date)` | issue_id, order_id, hub_id, kind, status, amount_paise, credit_paise, by, created_at, resolved_at; the open count, oldest `created_at` and earliest `callback_due_at`; orders by status |
| M5 | `billing.api.earning_facts(session, *, week_from, week_to)` | partner_id, iso_week, basis, units, amount_paise |
| M5 | `billing.api.kindness_facts(…)` | date, source_type, points credited, donated, expired |
| M6 | `hub_ops.api.hub_day_facts(…)`, `packing_progress(session, hub_id, date)` | hub_id, date, packed_g, waste_g by `cascade_to`, cogs_paise or null (until M6 §3.4 ships), surplus offered and taken; bags packed and total |
| M7 | `logistics.api.route_facts(…)`, `today_routes(session, hub_id, date)` | route_id, hub_id, date, kind, km_m, cost_paise, orders, stops, on_time_stops, late_stops, source |
| M8 | `partners.api.partner_facts(session, *, ids=None)`, `assignment_run_facts(…)` | partner_id, hub_id, kind, status, data_source; run date, hub, meal, portions, gini, km per portion |
| M9 | `community.api.impact_facts`, `referral_facts`, `handoff_facts`, `society_impact`, `public_society` (M9 §3.8) | donations, referrals, hand-offs, pledges; the k-protected society numbers |
| M10 | `intel_gateway.api.model_health` (**exists**) | §3.8 |

### 3.2 Fact tables, materialised views and refresh jobs
```
analytics.fact_households   (household_id pk, hub_id, society_id, created_on, cohort_week, status, data_source, synced_at)
analytics.fact_cycles       (cycle_id pk, household_id, hub_id, society_id, week_start, delivery_date, status,
                             auto_approved, approved_before_cutoff, edits, plan_source, ran_out_taps,
                             wasted_taps, data_source, synced_at)
analytics.fact_orders       (order_id pk, household_id, hub_id, society_id, kind, delivery_date, status,
                             total_paise, refunded_paise, credited_paise, lines, short_lines, on_time,
                             first_attempt, data_source, synced_at)
analytics.fact_issues       (issue_id pk, order_id, hub_id, kind, status, amount_paise, credit_paise,
                             resolved_by, created_at, resolved_at, data_source, synced_at)
analytics.fact_routes       (route_id pk, hub_id, date, kind, km_m, cost_paise, orders, stops,
                             on_time_stops, data_source, synced_at)
analytics.fact_hub_days     (hub_id, date pk, packed_g, waste_ngo_g, waste_bin_g, waste_kitchen_g,
                             waste_imperfect_g, cogs_paise null, surplus_offered_g, surplus_taken_g, synced_at)
analytics.fact_partner_weeks(partner_id, iso_week, basis pk, hub_id, kind, units, amount_paise, data_source, synced_at)
analytics.fact_assignment_runs (run_id pk, hub_id, date, meal, portions, gini_bp, km_per_portion_m, data_source)
analytics.fact_community    (source, source_id pk, hub_id, society_id, ngo_id null, date, kind,
                             items, kg_tenths, meals, points, people_reached, household_id null, data_source)
                             kind: donation_verified | referral_converted | handoff_items | handoff_food |
                             handoff_meals | meals_pledged | society_live
analytics.assumptions       (hub_id null pk, key pk, value bigint, updated_by, updated_at)   null hub = default
analytics.assumption_changes(id, hub_id, key, from_value, to_value, by_user_id, at)
analytics.refresh_runs      (id, job, scheduled_for, window_from, window_to, status, rows jsonb,
                             started_at, finished_at, error)
```

| Materialised view (in `analytics`) | Grain and contents | Refreshed by |
|---|---|---|
| `mv_daily_hub_kpis` | hub_id, date, data_source. Orders delivered, boxes, households active, GMV and refunds (paise), cycles generated, approved, auto-approved, skipped and approved by ML or fallback, edits, on-time and first-attempt counts, short and packed lines, issues and auto-credited issues, packed and waste grams, route km and cost, donations, items, meals and referrals converted | `refresh_analytics` (15 min) and nightly |
| `mv_weekly_unit_economics` | hub_id, iso_week, data_source. Boxes, box and add-on revenue net of refunds and credits, tiffins and tiffin revenue, COGS (or null), route cost of box routes, captain, packer, rider and kitchen earnings (paise) | nightly |
| `mv_cohorts` | data_source, hub_id, cohort_week, week_offset (1–26), households, active (≥ 1 delivered order that week) | nightly |
| `mv_partner_earnings_weekly` | hub_id, kind, iso_week, data_source. Partners paid, total, p10, median, p90 (paise), Gini (basis points) | nightly |
| `mv_impact_monthly` | hub_id, ngo_id (null for hub totals), month. Donations verified, items, kg, households donating, meals pledged and served, food handed over (kg), people reached, referrals converted, societies gone live | `refresh_analytics` (15 min) and nightly |

Each view has a unique index on its grain, so `REFRESH MATERIALIZED VIEW CONCURRENTLY` works and reads never block.

**Jobs** (M0 internal job endpoints with an advisory lock per job name, ADR-0021 and [ADR-0025](../adr/0025-ml-runtime-spaces-actions-r2.md)):

| Job | Schedule | What it does |
|---|---|---|
| `refresh_analytics` | Every 15 minutes (cron-job.org) | Pulls facts for delivery dates today − 3 … today + 1 (IST) and community facts since the last run. It upserts them by source id, changing `synced_at` only when a value changed, then refreshes `mv_daily_hub_kpis` and `mv_impact_monthly`. Budget: under 20 s at pilot scale |
| `refresh_analytics_nightly` | 02:40 IST (GitHub Actions) | The same over today − 35 … today, which covers 48-hour issue credits, redeliveries and the 14-day carry-over refunds (M7 §3.4). It also refreshes every household fact, partner weeks for the last 6 ISO weeks and assignment runs, then every view. Budget: under 3 minutes |
| `rebuild_analytics` | By hand | Every date since the first order, in 7-day chunks, then every view. Used after a facade change or a bug fix |

- **Recorded runs.** Each run writes `analytics.refresh_runs` with the rows read per fact. A failed pull leaves the previous facts in place and the run `failed`; the next run retries.
- **`as_of`.** Every response carries `as_of`: the finish time of the last successful run that covered the data. `stale: true` is set when that is more than 45 minutes old for 15-minute views, or more than 26 hours for nightly ones.
- **The source wins.** Facts are a cache of the source modules: a rebuild from the facades must give the same numbers (§9).

**As built** (`feat/M12-facts-refresh`, 2026-10-25; the endpoints follow in later PRs):
- **Columns added to the draft above.** `fact_cycles.just_right_taps` (the denominator of `pantry_misses_bp` is every tap), `fact_orders.fees_paise` (§3.4's delivery-fee revenue), `fact_hub_days.data_source` (the hub's, so hub-level numbers are never pooled), `synced_at` on every fact table, and `assumptions` keyed by a surrogate `id` with `UNIQUE (hub_id, key) NULLS NOT DISTINCT` (a primary key cannot hold a null hub). The migration inserts the §3.4 defaults. `mv_impact_monthly` carries `data_source` in its grain (§3.6 lets admins read `synthetic`).
- **What the facades cannot give yet** is 0 or null, and each facade says so: `cogs_paise` (paid lines leave stock without lot moves until the 12:30 draw-down). Waste grams come from the waste logs since `feat/M12-today-kpis` (§3.3 "As built"), surplus grams from M6 §3.5's cascade, and NGO hand-offs, meal pledges, meals served and people reached from M9 §3.5, which sends `handoff_*` and `meals_pledged` rows through `impact_facts`. `billing.api.kindness_facts` is built but not stored: no §3.2 table holds daily points, and meals pledged come from M9's `meal_pledges`. Earnings carry no data source in M5: a partner week takes its partner's. Routes, hub-days and assignment runs take their hub's data source.
- **Definitions the draft left open.** `on_time`: delivered by the end of its slot (IST). `first_attempt`: null until dispatched, then delivered on the first trip. `refunded_paise` is the move to `refunded` plus refunded carried items, and `credited_paise` the credited issues, so they never overlap (shortage refunds are already out of `total_paise`). A route's `km_m` is planned (riders' distance is not recorded) and its `cost_paise` the plan's solved cost shared by planned km. Issues are counted on the IST day they were reported. Household `status` is the owner's account status (a household has none). Partner Gini and percentiles are over paid partners, null under 5, in the view itself.
- **Refresh.** Upserts touch a row only when a value changed; facts the source no longer returns for a pulled window are deleted (a purged cycle, a superseded route); issues are never pruned (M4 keeps them). "Since the last run" is the IST date of the last successful `refresh_analytics`'s `scheduled_for`, never further back than 35 days. The rebuild starts at the first order (`orders.api.first_delivery_date`) or the first signup, if earlier. The three jobs share one advisory lock, so they never interleave. A failed run commits its `failed` row, then raises, so the job run fails too. `as_of`/`stale` live in `analytics/freshness.py` for the endpoint PRs.
- **Erasure.** The consumer of `identity.user_deleted` (§4) deletes the household's `fact_households` and `fact_cycles` rows under the refresh jobs' advisory lock. While a refresh holds the lock, it fails and the outbox retries it (with backoff), so a pull that read the household before the purge cannot write it back.
- **Not built here:** Row-Level Security on the fact tables (§3.9; no table in the monolith has RLS yet). The today-board facades come with the endpoints (§3.3 "As built").

### 3.3 Today board, KPIs and societies contract (drafted 2026-10-09, frozen on approval)
The console's Ops board (`(hub)/today`), KPIs (`(analytics)/kpis`) and Customers (`(franchise)/customers`) pages build against this. Changing it needs a PR reviewed by both Prathamesh and Yash.

```
GET /v1/analytics/hubs/{hub_id}/today?date=        live (facades, not views); Cache-Control: no-store;
                                                   the console polls every 60 s; date: today − 7 … today + 1
  200 { "hub_id", "date", "as_of",
        "boxes": { "total": 58, "locked": 58, "cutoff_at": "…",
                   "by_society": [ { "society_id", "name", "boxes": 23 } ] },
        "packing": { "bags_total": 58, "bags_packed": 58, "finished_at": "…" | null },
        "shortages": [ { "product_id", "name", "bags": 4 } ],
        "routes": { "planned": 2, "started": 2, "completed": 1, "stops_total": 6, "stops_done": 4,
                    "late_stops": 0 },
        "deliveries": { "at_society": 10, "delivered": 40, "held": 1, "undelivered": 0 },
        "waste": { "kg": 0.7, "previous_kg": 1.0, "previous_date": "…" | null },
        "steps": [ { "key": "cutoff" | "procurement" | "receiving" | "packing" | "dispatch"
                            | "delivery" | "surplus",
                     "due_at": "06:30", "state": "done" | "current" | "issue" | "todo",
                     "note": "…" | null } ],                 due times from the hub's timings (M6 §3.1)
        "attention": [ { "kind": "issue_open" | "payment_failed" | "shortage" | "plan_needs_review"
                                 | "delivery_held" | "donations_to_verify" | "surplus_open"
                                 | "society_live",
                         "count": 2, "oldest_at": "…" | null, "due_at": "…" | null,
                         "link": "/hub/issues?status=open" } ] }       a console route, never a URL with a token

GET /v1/analytics/kpis?hub_id=&from=&to=&grain=week&data_source=real
       hub_id omitted: every hub (admin only); grain day (≤ 92 days) | week (≤ 104 weeks), default week;
       from/to IST dates, default the last 12 weeks; data_source required: real | synthetic
  200 { "hub_id" | null, "data_source", "grain", "as_of", "stale": false,
        "periods": [ "2026-09-07", … ],                     first day of each period (ISO week starts Monday)
        "series": { "<kpi>": [ 64, 66, … | null ] },         one integer per period, null when undefined
        "totals": { "<kpi>": 66 | null } }                   over the whole range (rates recomputed, not averaged)

GET /v1/analytics/societies?hub_id=&data_source=real
  200 { "hub_id", "as_of", "items": [ { "society_id", "name", "status", "joined", "threshold",
         "active_households": 41, "paused_households": 3,
         "approved_on_time_bp": 9100 | null, "average_box_paise": 64200 | null } ] }
```

**KPI keys** (all integers; rates in basis points, money in paise, weights in grams; each tied to its definition):

| Key | Definition |
|---|---|
| `active_households` | Households with at least one delivered order in the period (for `totals`, in the last week of the range) |
| `weeks_accepted_bp` | Cycles approved (by the customer or auto-approve) ÷ cycles generated. The live counterpart of M-C plan acceptance |
| `auto_approved_bp` · `skipped_bp` | Auto-approved and skipped cycles ÷ cycles generated |
| `accepted_ml_bp` · `accepted_fallback_bp` | Acceptance among cycles whose plan came from the ML service or from the rules fallback |
| `edits_per_100_boxes` | Line edits ÷ approved cycles × 100 (M-B guardrail) |
| `pantry_misses_bp` | "Ran out" + "wasted" taps ÷ feedback taps (M-A guardrail) |
| `orders_delivered` · `boxes_delivered` | Delivered orders, all kinds and boxes only |
| `gmv_paise` · `aov_paise` | Σ totals of delivered orders, net of refunds and credits; ÷ delivered orders |
| `on_time_bp` | Delivered orders delivered by their slot's end ÷ delivered orders (M-F) |
| `first_attempt_bp` | Delivered at the first attempt ÷ dispatched (M7 §3.4) |
| `shortage_lines_bp` | Lines short at packing ÷ lines packed (M-E stock-out) |
| `waste_g_per_100kg` | Σ waste with `cascade_to = bin` ÷ Σ packed × 100 kg (M-E waste). Only the bin is waste: food a kitchen or an NGO took is rescued, not wasted. M6 §3.5 owns the definition (owner, 2026-10-25; the draft's `{ngo, bin}` is superseded) |
| `delivery_cost_per_order_paise` · `km_per_order_m` | Route cost and route km of box routes ÷ orders on them (M-F) |
| `issues_per_1000_orders` · `issue_auto_credit_bp` | Issues ÷ delivered orders × 1,000; issues credited automatically ÷ issues |
| `partner_earnings_paise` · `partners_paid` | Σ earnings accrued (M5 §3.5) and the partners who earned anything |
| `kitchen_gini_bp` | Gini of kitchen partners' weekly earnings (M-G; week grain only) |
| `donations_verified` · `meals_donated` · `referrals_converted` · `societies_live` | From M9's facts |

- **Data sources are never pooled** (eval protocol §2, ADR-0008).
  - `data_source` is required. The values are what the modules store: `synthetic` for simulator rows and `real` for everything else. The console labels `real` as "Pilot" until launch.
  - Handbook 05 §5.10's `pilot`/`live` and M10's `synthetic | pilot | live | mixed` differ from the stored `real | synthetic`. The owner decided on 2026-10-09 that M12 uses the stored values (§11).
- **Hub scope.** A hub manager or franchise owner must give a `hub_id` within their grants (403 `auth.forbidden`). Only an admin may omit it.
- **Aggregates only.** No M12 response carries a household id, name, flat or phone. This answers Draft v1's open question: franchise owners and hub managers get aggregates in M12, and household detail stays in M1 and M4's own screens under their own permissions.
- **The order-issue triage link.** M12 does not serve or duplicate the triage queue. M4 §3.6 owns it (`GET /v1/admin/order-issues`, built in #156). The today board's `issue_open` row (`count`, oldest `created_at`, earliest `callback_due_at` as `due_at`, from `orders.api.open_issue_summary`) and the `issues_per_1000_orders` tile link to the console's Issues page, `/hub/issues?status=open`, which renders M4 §3.6. `donations_to_verify` links to `/community/donations`, `surplus_open` to `/hub/waste`, `delivery_held` to `/hub/deliveries`, and `plan_needs_review` to `/hub/routes`.
- **No map on the board.** The live map of vans comes from M7 §3.3's endpoints, not from this board.

| Mock today (console, `feat/console-sync-shapes-3`) | Contract |
|---|---|
| `GET /v1/hubs/{id}/ops-board` with display strings (`as_of: "8:20 AM"`, `locked_at: "9 PM cut-off"`, `status_line`, `finished_at: "6:40 AM"`) | `GET /v1/analytics/hubs/{hub_id}/today`; timestamps and `HH:MM`, formatted by the console; no `status_line` (compose it from `steps`) |
| `attention: [{ id, what, where, state: { tone, label }, next: { text, href }, severity }]`, e.g. "Eggs broken in a bag" → `/refunds` | `attention: [{ kind, count, oldest_at, due_at, link }]`, one row per kind with a count; open issues link to `/hub/issues` (M4 §3.6), not `/refunds`; copy and tone come from `kind` |
| `markers`, `route` on the board | Not here: the console reads M7's live route endpoints for the map |
| `GET /v1/analytics/kpis` → `{ data_source: "simulator" \| "pilot", weeks: ["7 Sep", …], active_households: […], weeks_accepted_bp, waste_g_per_100kg, on_time_bp, partner_earnings_paise }` | `periods` (ISO dates, not labels) and `series.<key>`; the five tiles' keys are unchanged; `data_source` is `real \| synthetic` and required; more keys available |
| `GET /v1/analytics/customer-summary?hub_id=` → `{ active_households, active_households_change, paused_households, approved_on_time_bp, joining_societies, societies: [...] }` | `GET /v1/analytics/societies`; the hub totals and the change are computed by the console from `items`; `joining_societies` = items with `status: "unlocking"` |

**As built** (`feat/M12-today-kpis`, 2026-10-25; the other analytics endpoints follow):
- **Common.**
  - `analytics:read` is checked as in M3's cut-off monitor: `permit.ensure(hub_id)` (403 `auth.forbidden`), with `X-Active-Role` narrowing the grants; a non-admin who leaves `hub_id` out of KPIs or societies gets 403.
  - Dates are IST on the API's clock. Each read runs under `SET LOCAL statement_timeout = '3s'` (503 `analytics.timeout`), and each endpoint answers 429 `request.rate_limited` after 60 requests a minute per user (Valkey; no limit without it, as elsewhere).
  - KPIs and societies carry `Cache-Control: private, max-age=60`, the board `no-store`. A `data_source` outside `real | synthetic` is the framework's 422, and a missing one `analytics.data_source_required`.
- **Today board.** It reads five new facades, all keyword-only, typed, with ids, timestamps and counts only:
  - `orders.api.open_issue_summary(session, *, hub_id)` and `today_counts(session, *, hub_id, on)`. `by_status` counts every order of the day. `boxes.total` is the day's boxes not cancelled, `locked` those in a status after the cut-off, and `by_society` the same boxes per society.
  - `hub_ops.api.packing_progress(session, *, hub_id, on)`. A task's `bags` count, so `bags_total` is their sum. `finished_at` is the last `packed_at`, only once every task is packed. A shortage's `bags` are the bags with at least one step of the product short, carried lines left out. `inbound_progress` is the fifth facade (not in §3.1): the procurement plan's status and the purchase orders sent and received, for the `procurement` and `receiving` steps.
  - `logistics.api.today_routes(session, *, hub_id, on, now=None)`. `planned` counts routes of published plans that are not cancelled, `started` the in-progress and completed ones, `stops_done` the stops handed over or skipped. A stop is `late` when handed over after its window's end (IST), or still open and past it; skipped stops are never late. Delivery attempts of the day: `at_society` is `with_captain`, and `undelivered` adds `redelivery`, `refunded` and `triage` to `undelivered` (a failed attempt stays one whatever happened after). It also counts solved plans not yet published, for `plan_needs_review`.
  - `waste` is `hub_ops.api.waste_summary` for the date (bin only, as M6 §3.5 defines waste) and `previous_*` is the newest earlier day within 14 days with sales or waste. The product name is in the caller's language (`Accept-Language`).
  - **Steps.** `done` when the work is finished; `issue` when it is not and its time has passed (or, for the cut-off, a payment failed; for delivery, a stop is late or a delivery failed); `current` when it has begun; else `todo`. Due times are the hub's timings; the cut-off's is `21:00` (the day before). `procurement` and `delivery` have no timing, so their `due_at` is `null`. `surplus` has no status to read yet, so it is `todo` until its time and `done` after. `note` is a short count ("4 of 5 bags").
  - **Attention.** Rows are sent only for a kind with a source and a count above 0, in the order of §3.3. `issue_open` is hub-wide, not for the date. `payment_failed` counts `payment_failed` orders of the day (`oldest_at` is the cut-off, `due_at` the end of the two-hour grace). `shortage` counts short products. `delivery_held` counts held attempts. `plan_needs_review` counts solved, unpublished plans. Not sent, for want of a source: `donations_to_verify` (M9 has no queue facade), `surplus_open` (M6 has no open-offer count) and `society_live` (nothing says what needs attention). Links for kinds §3.3 gives none for: `payment_failed` `/hub/cutoff`, `shortage` `/hub/packing`.
- **KPIs.**
  - A week grain covers whole ISO weeks: `from` moves back to its Monday and `to` forward to its Sunday, and the limit counts those weeks. The default is 12 weeks ending with the week of `today`.
  - Every key of the table is always present. `totals` are rates recomputed from the summed parts of the range.
  - `active_households` is the distinct households with a delivered order in the period (`fact_orders`, not the view's daily count); its total is the last 7 days of the range.
  - `partner_earnings_paise`, `partners_paid` and `kitchen_gini_bp` come from `fact_partner_weeks` (not the view, so an admin's all-hub series is one Gini), by ISO week. They are `null` at day grain, and over the range a partner paid in two weeks counts once. The Gini is `null` under 5 paid kitchens and equals `mv_partner_earnings_weekly`'s.
  - `delivery_cost_per_order_paise` is `null` when the range's box-route cost is 0: a plan without a recorded cost is not free.
  - `edits_per_100_boxes` and `issues_per_1000_orders` are rounded half up to integers.
  - `waste_g_per_100kg` is the bin grams over the packed grams × 100,000 (grams per 100 kg), `null` when nothing was packed. It counts `cascade_to = bin` only: M6 §3.5 owns the definition and rescued food (kitchen, NGO) is not waste, which the owner confirmed over the draft's `{ngo, bin}` on 2026-10-25. `hub_ops.api.hub_day_facts` fills `waste_bin_g`, `waste_ngo_g` and `waste_kitchen_g` from `hub_ops.waste_logs` (`kg` × 1,000 by IST day and `cascade_to`; a log without a weight is left out, as in M6's summary), and a day with waste logs but no packing is a hub-day too. `waste_imperfect_g` stays 0 (Imperfect Sabzi is not in v1), and surplus offered and taken stay 0 until the surplus cascade lands.
  - **Null for want of a source:** `meals_donated` (M9's pledges and hand-offs have no facade, so no fact exists), in `analytics.kpis.UNSOURCED`.
- **Societies.**
  - Name, status, `joined` and `threshold` come from the new `community.api.hub_societies(session, *, hub_id)`; societies whose own `data_source` differs from the request's are left out. An admin who gives no `hub_id` gets every hub's, and `hub_id` is then `null`. Every query of the table, these included, runs under the 3-second statement timeout.
  - The rest covers the last 28 days, today included. `active_households` is the society's households with a delivered order. `approved_on_time_bp` is cycles approved before the cut-off over cycles generated, by delivery date. `average_box_paise` is delivered boxes' total less refunds and credits, over their number (add-ons left out).
  - `paused_households` asks the new `subscriptions.api.paused_households(session, *, household_ids, on)` which of the society's synced households (`fact_households`) have a paused subscription on the day. It is handed ids and returns ids, which M12 only counts.
  - `as_of` is the last successful refresh.

### 3.4 Unit economics, P&L and assumptions contract (drafted 2026-10-09, frozen on approval)
Handbook 02 §5: **contribution per box = AOV × margin + delivery fee − (delivery + packing + captain + payment fee + CAC)**. The delivery fee is the pre-live fee customers pay per box until their society goes live (M9 §3.6, owner 2026-10-09); it is revenue, and 0 for boxes in live societies. Each input is **measured** when the pilot (or the simulator) has produced it, and an editable **assumption** otherwise. The console's Unit economics and P&L pages (`(franchise)/unit-economics`, `(franchise)/pnl`) build against this. Changing it needs a PR reviewed by both Prathamesh and Yash.

```
Assumptions : { "aov_paise": 62000, "margin_bp": 2400, "delivery_paise": 4000, "packing_paise": 3000,
                "captain_paise": 600, "payment_fee_bp": 120, "cac_paise": 1300 }
     defaults (hub_id null); captain_paise 600 is the decided ₹6 a bag (M8 §3.6), not the mock's 1000

GET /v1/analytics/hubs/{hub_id}/assumptions          franchise_owner (hub), admin; ETag
  200 { "hub_id", "assumptions": Assumptions, "version": 3,
        "history": [ { "at", "by": { "name" }, "changes": [ { "key", "from", "to" } ] } ] }   newest 20
PUT /v1/analytics/hubs/{hub_id}/assumptions          same roles; If-Match; body Assumptions (all keys)
  200 as GET; audit-logged
  422 "analytics.assumptions_invalid" { errors: [ { "field", "detail" } ] }
      ranges: aov 10,000–500,000; margin_bp 0–6,000; delivery, packing 0–20,000; captain 0–5,000;
      payment_fee_bp 0–500; cac 0–50,000 (paise)

GET /v1/analytics/unit-economics?hub_id=&from=&to=&data_source=real&prefer=measured
                                &aov_paise=&margin_bp=&…                 what-if overrides, never saved
  200 { "hub_id", "data_source", "from", "to", "as_of", "boxes": 412,
        "assumptions": Assumptions,                      the hub's saved values
        "measured": { "aov_paise": 64150, "margin_bp": null, "delivery_paise": 3820,
                      "packing_paise": 400, "captain_paise": 600 },    null: not measurable yet
        "used": Assumptions,                             override > measured (prefer=measured) > saved
        "sources": { "aov_paise": "measured" | "assumed" | "override", … },
        "delivery_fee_paise": 410,                       measured only: Σ fees_paise ÷ boxes; 0 with no
                                                         pre-live boxes; overridable for a what-if
        "per_box": { "margin_paise", "delivery_fee_paise", "delivery_paise", "packing_paise",
                     "captain_paise", "fee_paise", "cac_paise", "contribution_paise" },
                     fee_paise is the payment fee (a cost); delivery_fee_paise is revenue
        "weeks": [ { "week": "2027-02-01", "boxes": 103, "contribution_paise": 4120 } ] }

GET /v1/analytics/pnl?hub_id=&period=2027-02&data_source=real            franchise_owner (hub), admin
  200 { "hub_id", "period": "2027-02", "as_of", "boxes": 412, "tiffins": 220,
        "revenue_paise", "contribution_paise", "previous_contribution_paise" | null,
        "per_box_paise", "partners_paise": { "captains", "kitchens", "riders", "packers" },
        "lines": [ { "key": "box_revenue" | "delivery_fee_revenue" | "tiffin_revenue" | "produce"
                            | "kitchen" | "delivery" | "packing" | "captain" | "fees" | "acquisition",
                     "amount_paise": -1840000,          costs negative, exact paise
                     "source": "measured" | "assumed",
                     "basis": { "kind": "times", "qty": 412, "unit": "box", "rate_paise": 4000 }
                            | { "kind": "share", "bp": 7600, "of": "box_revenue" } } ],
        "weeks": [ { "week", "contribution_paise" } ] }
```

**What is measured** (from M12's facts; otherwise the saved assumption is used and `sources` says so):

| Input | Measured as | Measurable from |
|---|---|---|
| `aov_paise` | (box + add-on revenue of delivered boxes − refunds − issue credits) ÷ boxes delivered; the delivery fee is left out | S2 (M4) |
| `delivery_fee_paise` (revenue, not an assumption) | Σ `fees_paise` of delivered boxes ÷ boxes delivered; 0 when every society is live | With M4's pre-live fee (M9 §11) |
| `margin_bp` | 1 − (COGS of packed lines ÷ revenue) | When M6 §3.4 records lot costs and `hub_day_facts.cogs_paise` is set |
| `delivery_paise` | Σ `cost_paise` of the hub's box routes ÷ boxes on them (M7's route cost model) | S5 (M7) |
| `packing_paise` | Packer earnings ÷ boxes packed (M8 rate card, ₹4 an order); hub overheads stay in the assumption | S6 (M8) |
| `captain_paise` | `bag_delivered` earnings ÷ boxes delivered | S6 (M8) |
| `payment_fee_bp` | Never in v1: M5 does not store Razorpay's fee | Follow-up for M5: store `fee_paise` from the payment entity |
| `cac_paise` | Never measured: marketing spend is outside the system | Always an assumption |

- **Arithmetic** is integer, the same as the console's `boxEconomics`. Money is in paise and percentages in basis points. Each percentage step is rounded half up to a whole paisa, so the server and the console's live what-if give the same numbers on shared fixtures (§9).
- **The P&L's lines** use measured totals where they exist (revenue, kitchen, captain, packing, delivery). `delivery_fee_revenue` is the measured Σ `fees_paise` of delivered boxes (refunded fees left out), kept apart from `box_revenue` so that the produce share is taken of the food only. `produce` is the measured COGS, or the share `10,000 − margin_bp` of box revenue; `fees` (the payment fee) and `acquisition` are assumed. `amount_paise` is exact, and the console rounds for display.
- **Franchise owners** get only their hubs' aggregates. Hub managers do not get P&L or unit economics (the franchise's business).

| Mock today (console) | Contract |
|---|---|
| `GET /v1/analytics/unit-economics?hub_id=` → `{ assumptions, per_box, history }` | Adds `from`/`to`, `data_source`, `measured`, `used`, `sources`, `weeks`; `history` moves to the assumptions endpoint |
| `PUT /v1/hubs/{hub}/unit-economics-assumptions` | `PUT /v1/analytics/hubs/{hub_id}/assumptions` with If-Match (the `/v1/hubs` path belongs to M6); same body and `errors[]` |
| Default `captain_paise: 1000` | 600 (₹6 a bag, decided 2026-10-09) |
| `boxEconomics` has no delivery fee | Adds `delivery_fee_paise` per box as revenue (owner, 2026-10-09: the pre-live fee, M9 §3.6), so the server and the console still agree |
| `GET /v1/analytics/pnl` lines rounded to a whole rupee; no `source` | Exact paise and a `source` per line; `partners_paise` replaces `captains_paise` and `kitchens_paise`; `periods` (the month picker) is dropped: any of the last 24 months |

**As built** (`feat/M12-unit-economics`, 2026-10-25; the other analytics endpoints follow):
- **Common.**
  - Permissions `analytics:economics` (franchise_owner, admin: both reads) and `analytics:assumptions_write` (the PUT) are in `analytics/permissions.py`, so a hub manager and a customer get 403 `auth.forbidden`. Scope, the 3-second statement timeout, the 60-a-minute limit and the 404 `hub_ops.hub_not_found` (from `hub_ops.api.get_hub`) are as in §3.3 "As built".
  - All four need a `hub_id`, a franchise owner within their grants (403 otherwise, also when it is left out). Assumptions belong to one hub, so an admin must name it too: left out is 422 `request.invalid`. A missing `data_source` is 422 `analytics.data_source_required`.
  - **Responses are `Cache-Control: private, no-cache`**, not §3.9's `max-age=60`. The console saves assumptions and reads the numbers back at once, and a cached read would show the old values, or a stale ETag that turns the next save into a 412.
- **The view.** Both reads are over `mv_weekly_unit_economics`, so they are as fresh as the nightly run (`as_of` is its last `ok` nightly run or rebuild, `null` with none). One row there is one ISO week, so:
  - `unit-economics` covers whole ISO weeks as the KPIs' week grain does: `from` moves back to its Monday, `to` forward to its Sunday (the answer says which), the default is the last 12 weeks and the limit 104 weeks.
  - The P&L's month is the weeks that **start** in it, not its calendar days; a week that spans two months is the earlier month's. `weeks` lists those Mondays, and their contributions add up to the month's. Any of the last 24 months, this one included, is allowed (422 `analytics.range_invalid` for a malformed or future month, `analytics.range_too_long` beyond).
- **Measured, as built.**
  - `aov_paise` is box and add-on revenue net of fee, refunds and credits over delivered boxes, so add-ons are in the AOV and in the `box_revenue` line.
  - `margin_bp` needs a COGS on every hub-day of a week, so it is `null` until M6 §3.4 sets `cogs_paise`.
  - `delivery_paise` is box-route cost over the orders on those routes; a week in which any route has no recorded cost is not measured (0 is not a cost).
  - `packing_paise` and `captain_paise` divide packer and captain earnings by **boxes delivered**, because boxes packed is not a fact; both are `null` while the earnings are 0.
  - `delivery_fee_paise` is the fees of delivered boxes over those boxes, 0 with none. A what-if may set it too (`delivery_fee_paise=`, range 0–20,000), and `sources.delivery_fee_paise` is `measured` or `override`.
  - Weeks are measured one by one: `weeks[].contribution_paise` is per box from that week's own inputs (`null` in a week without boxes), and a week without captain earnings yet does not turn the other weeks' captain figures into assumptions. The range's `measured` takes each input over the weeks that measured it (the AOV over every week): a week without captain earnings, a COGS or a costed route is left out of that input's figure, so its boxes do not water the rate down. In the P&L the same holds per line: a line is `measured` only if every week with boxes or tiffins for it to measure was; `fees` and `acquisition` are `assumed` even in an empty month.
- **What-if.** `prefer=measured` (default) or `assumed` (only the saved values). The eight override parameters are checked against the §3.4 ranges first (422 `analytics.assumptions_invalid` with `errors[]`; the delivery fee uses the delivery cost's range). `weeks` and `per_box` use the same rule. Nothing is saved.
- **P&L lines.**
  - `box_revenue` is food revenue (boxes and add-ons, net) and `delivery_fee_revenue` the fees, kept apart. `kitchen` is the kitchen partners' earnings and `delivery` the box-route cost (assumed: boxes × `delivery_paise`). `packing` and `captain` are the partners' earnings, else boxes × the assumption.
  - `produce` is the measured COGS (basis share = COGS ÷ food revenue), else the share `10,000 − margin_bp` of food revenue. `fees` is `payment_fee_bp` of food revenue, as the per-box fee is of the AOV, and `acquisition` is boxes × `cac_paise`. Both are assumed.
  - There is no assumption for the tiffin kitchens' pay: with tiffins but no kitchen earnings the `kitchen` line is 0 and `assumed`. Tiffin routes' cost is in no line.
  - A measured line's `basis` is `times` with the average rate (`rate_paise` = amount ÷ qty), since a total is not always a whole rate × quantity; `amount_paise` is the exact figure. An assumed `produce` or `fees` line carries the assumption's own basis points.
  - `partners_paise` totals the month's weeks (captains, kitchens, riders, packers). `previous_contribution_paise` is the month before's, `null` when that month has no boxes, tiffins or kitchen earnings. An empty month is all zeros, `per_box_paise` is `null`, and `revenue_paise` 0.
- **Assumptions.**
  - **Version.** The new table `analytics.assumption_versions (hub_id pk, version)` holds each hub's version, the ETag; a hub with no row is at version 1 (the defaults). `assumption_changes` gains `version`, the version a change produced, so the history groups one PUT's keys (not by timestamp: a transaction has one).
  - **The PUT** writes only the keys that changed, one `assumptions` row per key of the hub (the rest follow the default), a change row per key, and one audit entry (`analytics.assumptions_changed`, before and after of the changed keys and the versions). The version row is locked, so two writers holding the same version cannot both win; a stale or unreadable `If-Match` is 412 `request.precondition_failed`, a missing one 428. A PUT that changes nothing answers 200 with the current values and version, with no history and no audit.
  - **Body.** All seven keys as whole numbers (a missing, unknown, float or string key is the framework's 422 `request.invalid`); a range error is 422 `analytics.assumptions_invalid` with every bad field in `errors[]`.
  - **History.** The newest 20 saves, newest first; `by.name` is `null` when the editor has no name on record (`identity.api.user_names`).
- **Not built here:** the payment fee and CAC are never measured (as §3.4 says), and `boxes packed` for the packing rate waits for a packing fact.

### 3.5 Cohorts, partner earnings and the research metrics contract (drafted 2026-10-09, frozen on approval)
```
GET /v1/analytics/cohorts?data_source=real&hub_id=&weeks=12        admin; weeks 4–26
  200 { "data_source", "hub_id" | null, "weeks": 12, "as_of",
        "cohorts": [ { "week": "2026-09-07", "households": 200, "small": false,
                       "retention_bp": [ 9400, 8800, 8400, null, … ] } ] }
        week: the household's signup ISO week; retention_bp[i]: share of the cohort with a delivered
        order in week i + 1 after it; null = not reached yet; small: under 5 households

GET /v1/analytics/partner-earnings?hub_id=&kind=kitchen&from=&to=&data_source=real
  200 { "hub_id", "kind": "kitchen" | "captain" | "rider" | "packer", "as_of",
        "weeks": [ { "week", "partners": 14, "total_paise", "p10_paise" | null, "median_paise" | null,
                     "p90_paise" | null, "gini_bp" | null } ] }
        percentiles and Gini are null for weeks with fewer than 5 paid partners
```

**Research metrics** (`docs/research/eval-protocol.md`). The paper's numbers and figures come from `just eval` (eval protocol §6): HTML reports and `intel.model_metrics`. M12 does not recompute them. It shows the **live counterparts**, always per data source, so the pilot's behaviour can be compared with the registered definitions:

| Eval protocol | Live in M12 | Model-side metrics |
|---|---|---|
| M-A pantry twin: "ran out" + "wasted" rate | `pantry_misses_bp` (§3.3) | MAE and cold start: AI health (§3.8) |
| M-B ranker: edits per box | `edits_per_100_boxes` | NDCG@10: AI health |
| M-C planner: plan acceptance | `weeks_accepted_bp`, split by `accepted_ml_bp` / `accepted_fallback_bp` | Simulator A/B and ablations: eval reports |
| M-E forecast: waste %, stock-out % | `waste_g_per_100kg`, `shortage_lines_bp` | WAPE and drift per hub: AI health |
| M-F routing: ₹ per order, km per order, on-time % | `delivery_cost_per_order_paise`, `km_per_order_m`, `on_time_bp` | Solve-time benchmark: eval reports |
| M-G kitchens: Gini of weekly earnings, ₹ per portion | `partner-earnings` (kitchen `gini_bp`), `kitchen_gini_bp` | λ sweep and Pareto figure: eval reports |
| US-4 cohort retention (the paper's adoption figure) | `cohorts` per data source | — |

**As built** (`feat/M12-cohorts-earnings`, 2026-10-25; no new view or migration: `mv_cohorts` and `mv_partner_earnings_weekly` of §3.2 already hold everything):
- **Common.** Both endpoints answer 429 after 60 requests a minute per user, run under the 3-second read limit, carry `Cache-Control: private, max-age=60` and, as the KPIs do, `stale` beside `as_of` (the last nightly run or rebuild, 26 hours). `data_source` is required (`analytics.data_source_required`) and is `real | synthetic`, never pooled. `analytics:cohorts` is new and admin-only; `partner-earnings` uses `analytics:read` and the hub scope of §3.3 (a non-admin must give `hub_id`).
- **Cohorts.**
  - `weeks` is 4 to 26 (default 12): the number of signup weeks shown, this one last, and the length of every `retention_bp`. Over 26 is 422 `analytics.range_too_long`, under 4 `analytics.range_invalid`.
  - A signup week with no households is left out, and the list is oldest first.
  - A week after signup counts as reached once it has ended (its Sunday is past) and the view has been refreshed since: a week in progress, or one whose last deliveries the nightly refresh has not pulled yet (before 02:40 on Monday, or after a failed run), would read as households lost, so it is `null`. "Since" is the IST day of `as_of`; with no recorded run, today.
  - `retention_bp` is rounded half up. `small` (under 5 households) only flags the cohort: its rates are still sent, to an admin.
  - With no `hub_id`, hubs are added up (households and active households per cohort week), not averaged. A household that left through the erasure purge leaves the cohorts at the next nightly refresh (§4).
- **Partner earnings.**
  - `kind` defaults to `kitchen`; `from`/`to` default and limit as the KPIs' week grain (last 12 weeks, at most 104). The answer lists every ISO week of the range: a week nobody was paid in has 0 partners and everything else `null`.
  - A partner counts as paid when their week's earnings, over every basis, add up above 0. Percentiles interpolate linearly (as Postgres `percentile_cont`) and round half up to a paisa.
  - For one hub the weeks are read from the view. With no `hub_id` (admin) they are worked out from `fact_partner_weeks` in one pass, so the all-hub Gini is one Gini and not an average of hubs. The two give the same numbers for one hub (a test checks it) and `hub_id` is then `null`.
  - **k = 5 applies to the total too (review, 2026-10-25).** The contract nulls the percentiles and the Gini under 5 paid partners, but a week with one paid partner would still show that partner's earnings as `total_paise`, against "Distribution only, no individual" (§3). So under 5 paid partners only `partners` is sent: `total_paise`, the percentiles and the Gini are `null`, for every caller, the admin included, and on both the view and the all-hub route (any mean added later follows the same rule). `total_paise` is therefore `int | null`. No individual id is ever sent. The console's fairness page reads only `gini_bp`.
- **Research metrics.** All the live counterparts of the table above are keys of `/v1/analytics/kpis` (§3.3) and need no new code: `pantry_misses_bp`, `edits_per_100_boxes`, `weeks_accepted_bp` with `accepted_ml_bp` and `accepted_fallback_bp`, `waste_g_per_100kg`, `shortage_lines_bp`, `delivery_cost_per_order_paise`, `km_per_order_m`, `on_time_bp` and `kitchen_gini_bp`. A test checks they are present and that M-G's two forms (the KPI series and the kitchen `gini_bp` of `partner-earnings`) agree. "₹ per portion" (M-G) has no live counterpart in the table and is not built.

### 3.6 Impact contract (console) (drafted 2026-10-09, frozen on approval)
The hub, NGO and platform impact views (M9 §3.7 keeps the customer's `me` and `society` scopes). The console's Impact page (`(community)/impact`), the NGO coordinator's report and the MS-D impact dashboard build against this. Changing it needs a PR reviewed by both Prathamesh and Yash.

```
GET /v1/analytics/impact?scope=hub&hub_id=&month=2027-02&format=json|csv
     scope: platform (admin) | hub (hub_manager, franchise_owner of the hub; admin) |
            ngo (ngo_coordinator of ngo_id; admin; hub staff for an NGO serving their hub)
     month: IST calendar month, default the current one, within the last 24
  200 { "scope", "hub_id" | null, "ngo_id" | null, "month": "2027-02", "as_of", "stale",
        "totals": { "food_saved_kg": 53.5,          surplus handed to NGOs (kind food)
                    "items_donated": 30, "kg_donated": 12.5, "donations_verified": 9,
                    "households_donating": 8 | null,          null under 5
                    "meals_donated": 14, "meals_served": 10,
                    "people_reached": 120 | null,              as NGOs report it; null if none reported
                    "referrals_converted": 6, "societies_live": 1,
                    "partners_earned_paise": 1580000 | null,   null under 5 paid partners; never for scope ngo
                    "partners_paid": 14 | null,
                    "waste_g_per_100kg": 1900 | null },        hub and platform only
        "trend": { "months": [ "2026-09", … ],     the 6 months up to `month`
                   "food_saved_kg": [ … ], "items_donated": [ … ], "meals_served": [ … ] },
        "societies": [ { "society_id", "name", "items_donated", "kg_donated", "meals_donated",
                         "households_joined" } ],             scope hub only (staff); no earnings
        "ngos": [ { "ngo_id", "name", "handoffs", "items", "kg", "meals", "people_reached" } ] }
  format=csv: one row per (month, hub, ngo) with the totals' columns; Content-Disposition attachment;
  at most 10,000 rows
```

- **NGO coordinators** see only what their NGO received and the people they reached. They never see partner earnings, societies or households.
- **No partner pay per society.** `societies[]` carries no earnings: in most societies that number is one captain's pay.
- **The data source is `real`**, with `synthetic` allowed for admins (`&data_source=`) for demos.
- **No server PDF.** The handbook 03 "download impact report" is the CSV plus the console page's print stylesheet.

| Mock today (console `mocks/impact.js`) | Contract |
|---|---|
| `{ month, label, refreshed_at, totals: { food_saved_kg, items_donated, earned_paise, people_reached }, trend: { labels, … } }` | `as_of` for `refreshed_at`; `partners_earned_paise` for `earned_paise` (the same name as mobile's impact card, M9 §3.7); `trend.months` (`YYYY-MM`) for `labels`; more totals |
| `societies: [{ id: "sunrise-towers", name, food_saved_kg, items_donated, earned_paise }]` | `society_id` (UUID); no `food_saved_kg` (surplus is per hub) and no `earned_paise` per society |
| `GET /v1/analytics/impact/report?month=` → PDF; access admin and hub_manager | `format=csv`; NGO coordinators and franchise owners too |

### 3.7 Public society page contract (drafted 2026-10-09, frozen on approval)
`/s/<slug>` on the console app (server-rendered, `(public)/s/[slug]`) is the referral landing page (M9 §3.3; handbook 02 §8). Anyone can open it, so it carries **aggregates only**.

```
GET /v1/public/societies/{slug}          no auth; Cache-Control: public, max-age=300,
                                         stale-while-revalidate=600; 60 requests a minute per IP
  200 { "slug": "sunrise-towers-kharghar", "name": "Sunrise Towers", "area": "Sector 20, Kharghar",
        "status": "unlocking" | "live",
        "homes_joined": 46 | null,          null under 5 households
        "unlock_target": 20,
        "progress_bp": 10000,               min(joined ÷ target, 1), so the bar works under 5 too
        "live_since": "2027-02-27" | null,  IST date
        "delivery_days": [ "tue" ],         live societies only (M3 delivery days)
        "impact": { "items_donated": 18, "meals_donated": 7 } | null,   M9's k rule (5 contributors)
        "join_url": "https://app.gharbasket.in/join?s=sunrise-towers-kharghar" }
  404 "community.society_not_found"   unknown, prospect or paused (a prospect's page would announce a
                                      place that is not being opened)
  429 "request.rate_limited"
```

- **Never on this page:** a captain (her name and photo are for residents only, M9 §3.1), a flat or tower count of who joined, a household, a partner, or earnings.
- **Referral codes.** The page passes `?ref=` from its own URL into `join_url`. The API never says whether a code exists, because a public validity check would let anyone enumerate codes. An unknown code simply fails at the claim (M9 §3.3).
- **Sources.** M12 composes the page from `community.api.public_society` and `society_impact` (k already applied in M9) and `subscriptions.api.delivery_days`. Nothing is read from M12's views, so the page is current within the 5-minute cache.
- **Not in v1:** a public list of societies (`GET /v1/public/societies`, proposed by the console mock) and public cook profiles (M8 owns "know your cook").

| Mock today (console `mocks/public.js`) | Contract |
|---|---|
| `{ slug, name, area, status, homes_joined, unlock_target, captain_name: "Meena" \| null }` | No `captain_name`; `homes_joined` may be `null`; adds `progress_bp`, `live_since`, `delivery_days`, `impact`, `join_url` |
| `GET /v1/public/societies` (list) | Not in v1 |

### 3.8 AI health (built on `feat/M10-forecast-v1`, F25)
`GET /v1/analytics/ai-health` (admin only, `analytics:ai_health`; Cache-Control: no-store) returns:
- **Models.** For every model, its newest value per (metric, split, data source, hub) from `intel.model_metrics`.
- **Drift per hub.** For each hub and data source, `monitor_drift`'s rows over 7 days (`hubs[]`): drift, drifted and scored days, the newest realised WAPE, and the backtest WAPE of the same hub and source that it was compared with. A model's `drift` is true when any hub drifted.
- **ML or fallback.** How often each prediction kind came from the ML service or the fallback over 7 days.

It reads M10 only through `intel_gateway.api.model_health`, the pattern §3.1 generalises. It is unchanged by this draft.

### 3.9 Common rules, limits and errors
- **Read-only and synchronous.** M12 publishes no event and consumes only `identity.user_deleted` (§4). Requests never call the ML service.
- **Scope.** Every hub-scoped endpoint checks the `hub_id` against the caller's grants. The `analytics` fact tables carry `hub_id` under the same Row-Level Security policy as other hub-scoped tables (handbook 05 §5.9), as defence in depth.
- **Performance.**
  - **Response times.** At pilot scale (5 hubs, 2 years), view-backed endpoints answer in p95 ≤ 300 ms, and the today board (live facade counts) in ≤ 800 ms.
  - **Statement timeout.** Analytics reads run with `SET LOCAL statement_timeout = '3s'`. A timeout is 503 `analytics.timeout`, and the console shows "try a shorter range".
  - **Ranges.** Day grain covers ≤ 92 days, week grain ≤ 104 weeks, months ≤ 24 and cohorts ≤ 26 weeks; a longer range is 422 `analytics.range_too_long`. CSV is ≤ 10,000 rows.
  - **Rate limits.** 60 requests a minute per user on `/v1/analytics/*`, and 60 a minute per IP on `/v1/public/*`.
- **Caching.** Authenticated analytics responses are `Cache-Control: private, max-age=60`, except the today board and AI health (`no-store`).

**Error codes**

| Status | Code | When |
|---|---|---|
| 403 | `auth.forbidden` | A hub, NGO or scope outside the caller's grants; `hub_id` omitted by a non-admin |
| 404 | `community.society_not_found` | Public page: unknown, prospect or paused |
| 422 | `analytics.range_invalid` · `analytics.range_too_long` | `to` before `from`, a future month; over the limits above |
| 422 | `analytics.data_source_required` | `data_source` missing where required |
| 422 | `analytics.assumptions_invalid` | Out-of-range assumption (`errors[]`) |
| 412 / 428 | `request.precondition_failed` · `request.precondition_required` | Assumptions PUT with a stale or missing If-Match |
| 429 | `request.rate_limited` | Over the per-user or per-IP limit |
| 503 | `analytics.timeout` | A read over 3 s |

## 4. Tables owned
The `analytics` schema: the fact tables, `assumptions`, `assumption_changes`, `assumption_versions` (§3.4 "As built") and `refresh_runs` (§3.2), and the materialised views `mv_daily_hub_kpis`, `mv_weekly_unit_economics`, `mv_cohorts`, `mv_partner_earnings_weekly` and `mv_impact_monthly`. Only M12's refresh jobs write the fact tables; only the assumptions PUT writes `assumptions`.

**Personal data:** none. The fact tables hold pseudonymous ids (household, partner, order) with dates, statuses, counts and paise.
- **Deletion.** The consumer of `identity.user_deleted { user_id, household_id }` deletes the household's `fact_households` and `fact_cycles` rows, mirroring M3, which deletes cycles at the purge. The household then leaves the cohort tables at the next nightly refresh: its consent is gone.
- **Kept rows.** `fact_orders`, `fact_issues` and `fact_community` keep the household id exactly as M4 and M9 keep it (M1 §3.5: an id that no longer leads to a person).
- **Export.** M12 has no data export section: it holds derived aggregates, like M10's predictions (M1 §3.3).

## 5. Events
**Published:** none. **Consumed:** `identity.user_deleted` (§4). Everything else is pulled by the refresh jobs (§3.1).

## 6. Jobs
| Job | Schedule | Notes |
|---|---|---|
| `refresh_analytics` | Every 15 minutes (cron-job.org) | §3.2: last 4 delivery dates; refreshes `mv_daily_hub_kpis`, `mv_impact_monthly` |
| `refresh_analytics_nightly` | 02:40 IST (GitHub Actions) | §3.2: last 35 days, all households, partner weeks; every view `CONCURRENTLY` |
| `rebuild_analytics` | By hand | §3.2: full history in 7-day chunks |

## 7. Permissions
| Permission | Roles (scope) |
|---|---|
| `analytics:read` | hub_manager (hub), franchise_owner (hub), admin: today, KPIs, societies, partner earnings |
| `analytics:economics` | franchise_owner (hub), admin: unit economics, P&L, reading assumptions |
| `analytics:assumptions_write` | franchise_owner (hub), admin |
| `analytics:cohorts` | admin |
| `analytics:impact` | ngo_coordinator (ngo), hub_manager (hub), franchise_owner (hub), admin |
| `analytics:ai_health` | admin (built) |

The built `analytics/permissions.py` grants `analytics:read` to admin, hub_manager and franchise_owner, `analytics:ai_health` to admin, and (§3.4) `analytics:economics` and `analytics:assumptions_write` to admin and franchise_owner; the rest are added with their contracts.

## 8. Screens
Console route groups: `(auth)`, `(public)`, `(admin)`, `(hub)`, `(franchise)`, `(analytics)`, `(community)`; the full list is in `docs/FRONTEND_DESIGN.md` Section 10. Charts use ECharts, maps MapLibre + deck.gl, and tables TanStack Table.

S9 pages on this contract:
- Ops board (`(hub)/today`), and a new Issues page (`(hub)/issues`) over M4 §3.6.
- KPIs, cohorts and AI health (`(analytics)`).
- Unit economics, P&L and Customers (`(franchise)`).
- Impact, Drives, Point rates, NGOs, Hand-offs and Donations (`(community)`; the last five over M9).
- `/s/[slug]` (`(public)`).

## 9. Tests required
- **Facts:**
  - KPI numbers match fixture expectations on a seeded simulator week, for every key in §3.3.
  - A full rebuild gives the same views as the incremental runs.
  - A rerun of a refresh changes nothing.
  - A failed facade pull leaves the previous facts and records the run `failed`.
- **Boundaries:**
  - import-linter: `analytics` imports only other modules' `api` (no `models`).
  - A test greps M12's SQL and migrations for any schema name other than `analytics`.
  - No response or fact table has a name, phone, flat or address column (a schema walk).
- **Data sources:** a KPI request without `data_source` is 422, and synthetic rows never appear in a `real` series.
- **Scope:**
  - A hub manager or franchise owner cannot read another hub (403), and a non-admin cannot omit `hub_id`.
  - An NGO coordinator gets only their NGO and never `partners_earned_paise` or `societies`.
  - Partner percentiles and Gini are `null` under 5 partners.
- **Unit economics:**
  - The server's per-box arithmetic equals the console's `boxEconomics` on shared fixtures (rounding half up in paise and basis points).
  - `sources` reports measured, assumed and override correctly.
  - `delivery_fee_paise` and the `delivery_fee_revenue` line count the fees of delivered boxes only (refunded fees left out), and are 0 for a hub whose societies are all live.
  - A stale If-Match on the PUT gets 412, and every change is audit-logged with a history row.
- **Public page:**
  - Prospect and paused societies 404.
  - `homes_joined` and `impact` are `null` under 5.
  - No captain field ever appears.
  - The answer is the same with or without `?ref=`.
  - The per-IP limit applies.
- **Erasure:** after `identity.user_deleted`, the household's `fact_households` and `fact_cycles` rows are gone, and the next nightly refresh does not bring them back.
- **E2E (Playwright):**
  - Login and the role-based redirect via `proxy.js`.
  - The today board's issue row opens the triage queue.
  - The unit-economics calculator's what-if.
  - The impact page and its CSV download.
  - `/s/<slug>` rendered without signing in.
- **Accessibility:** axe on all console pages.

## 10. Dependencies
M1 for auth and household facts, and every module's facade listed in §3.1. The generated API client. M0 jobs and rate limiting.

**Follow-ups for other specs** (each a facade or event addition, reviewed with that module). Built on `feat/M12-facts-refresh` (§3.2 "As built"): `household_facts`, `cycle_facts`, `order_facts`, `issue_facts`, `earning_facts`, `kindness_facts`, `hub_day_facts`, `route_facts`, `partner_facts`, `assignment_run_facts`, `impact_facts` and `referral_facts(by="converted")`, and the handbook 05 and 10 rows. Built on `feat/M12-today-kpis` (§3.3 "As built"): `open_issue_summary`, `today_counts`, `packing_progress`, `today_routes`. Still open: `society_impact` (M9 §3.7; hand-offs and pledges come through `impact_facts`, M9 §3.5), `delivery_days`, and M5's `fee_paise`.
- **M1:**
  - `household_facts`.
  - §3.5's purge matrix gains M12: the consumer of `identity.user_deleted` deletes `fact_households` and `fact_cycles`, and `fact_orders` is kept like M4's orders.
  - §3.3 notes that M12 has no export section.
- **M3:** `cycle_facts` (with `approved_before_cutoff`, `edits`, `plan_source` and the pantry feedback taps), and `delivery_days(society_id)` on the facade for the public page.
- **M4:**
  - `order_facts` (with `fees_paise`, the pre-live delivery fee of M9 §3.6), `issue_facts`, `open_issue_summary` and `today_counts`.
  - §3.2's "M12 queues the open ones" and "the console endpoint comes with M12" are now stale: M4 §3.6 serves the queue and the photo links, and M12 only counts and links.
- **M5:** `earning_facts` and `kindness_facts`; store Razorpay's `fee_paise` on payments, so the payment fee can be measured.
- **M6:** `hub_day_facts` (with `cogs_paise` once §3.4 records lot costs) and `packing_progress`.
- **M7:** `route_facts` and `today_routes`.
- **M8:** `partner_facts` and `assignment_run_facts`.
- **M9:** the §3.8 facade (M9 spec).
- **Handbook:**
  - 05 §5.3's schema map has no `analytics` row: add "M12 · `analytics` · fact tables and views over other modules' facades".
  - 05 §5.2 rule 5: add "analytics reads through facades too (M12 §3.1)".
  - 04 §4: add an arrow from `analytics` to the facades.
  - 12: add the two refresh jobs to the job table.

## 11. Open questions and decisions
No open questions remain for §3.1–§3.9.

- **Decided by the owner (2026-10-09)** (every recommendation of this section accepted as written):
  - ~~`data_source` values? Transactional tables store `real | synthetic`; handbook 05 §5.10 says `synthetic | pilot | live`; M10's tables use `synthetic | pilot | live | mixed`; the console mocks use `simulator`/`sim` and `pilot`.~~ Decided: M12 takes the stored `real | synthetic`, and the console labels `real` as "Pilot" until launch (§3.3). Renaming `real` to `pilot` across the schemas before the pilot would be a later ADR, at one migration per module.
  - ~~Experiments endpoint?~~ Decided: cut from S9. Nothing assigns experiments, and the B9 uplift offers are a stretch feature. The simulator A/B lives in the eval harness.
  - ~~Hub managers and P&L?~~ Decided: no. P&L and unit economics are for franchise owners and admins; hub managers get KPIs and the today board (§3.4, §7).
  - ~~Refresh cadence on the free tier?~~ Decided: every 15 minutes for the daily views (96 short runs a day on cron-job.org) and nightly for the rest (§6). If Neon compute hours run short, `refresh_analytics` moves to 30 minutes, since nothing else depends on it.
  - ~~The small-number threshold for partner distributions, impact and the public page?~~ Decided: 5, shared with M9 §3.7.
  - ~~Default assumptions?~~ Decided: the console mock's numbers (AOV ₹620, margin 24 %, delivery ₹40, packing ₹30, payment fee 1.2 %, CAC ₹13), with the captain at the decided ₹6 (§3.4). They are hypotheses (handbook 02 §5), shown as "assumed" until measured.
  - ~~Boxes before a society is live (asked in M9 §11)?~~ Decided, changing M9's recommendation: households in a society that is not yet live get boxes with a per-box delivery fee (per hub, proposed default ₹30) until it goes live (M9 §3.6). For M12 the fee is revenue: `delivery_fee_paise` per box in unit economics and a `delivery_fee_revenue` line in the P&L (§3.4), from `fees_paise` in `order_facts` (§3.1, §10).
- **Decided in this draft (owner to confirm):**
  - ~~Can franchise owners see household-level data?~~ No: aggregates only, in every M12 response (§3.3).
  - ~~Read-only views over other schemas?~~ No: facades into M12's own fact tables (§3.1).

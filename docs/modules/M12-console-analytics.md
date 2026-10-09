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

`/v1/analytics/experiments/{key}` from Draft v1 is cut from S9: nothing assigns experiments yet (open question, §11).

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
| M4 | `orders.api.order_facts(…)` | order_id, household_id, hub_id, society_id, kind, delivery_date, slot_end, status, total_paise, refunded_paise, credited_paise, lines, short_lines, delivered_at, attempts, data_source |
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
| `waste_g_per_100kg` | Σ waste with `cascade_to ∈ {ngo, bin}` ÷ Σ packed × 100 kg (M6 §3.5; M-E waste) |
| `delivery_cost_per_order_paise` · `km_per_order_m` | Route cost and route km of box routes ÷ orders on them (M-F) |
| `issues_per_1000_orders` · `issue_auto_credit_bp` | Issues ÷ delivered orders × 1,000; issues credited automatically ÷ issues |
| `partner_earnings_paise` · `partners_paid` | Σ earnings accrued (M5 §3.5) and the partners who earned anything |
| `kitchen_gini_bp` | Gini of kitchen partners' weekly earnings (M-G; week grain only) |
| `donations_verified` · `meals_donated` · `referrals_converted` · `societies_live` | From M9's facts |

- **Data sources are never pooled** (eval protocol §2, ADR-0008).
  - `data_source` is required. The values are what the modules store: `synthetic` for simulator rows and `real` for everything else. The console labels `real` as "Pilot" until launch.
  - Handbook 05 §5.10's `pilot`/`live` and M10's `synthetic | pilot | live | mixed` differ from the stored `real | synthetic` (§11).
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

### 3.4 Unit economics, P&L and assumptions contract (drafted 2026-10-09, frozen on approval)
Handbook 02 §5: **contribution per box = AOV × margin − (delivery + packing + captain + payment fee + CAC)**. Each input is **measured** when the pilot (or the simulator) has produced it, and an editable **assumption** otherwise. The console's Unit economics and P&L pages (`(franchise)/unit-economics`, `(franchise)/pnl`) build against this. Changing it needs a PR reviewed by both Prathamesh and Yash.

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
        "per_box": { "margin_paise", "delivery_paise", "packing_paise", "captain_paise",
                     "fee_paise", "cac_paise", "contribution_paise" },
        "weeks": [ { "week": "2027-02-01", "boxes": 103, "contribution_paise": 4120 } ] }

GET /v1/analytics/pnl?hub_id=&period=2027-02&data_source=real            franchise_owner (hub), admin
  200 { "hub_id", "period": "2027-02", "as_of", "boxes": 412, "tiffins": 220,
        "revenue_paise", "contribution_paise", "previous_contribution_paise" | null,
        "per_box_paise", "partners_paise": { "captains", "kitchens", "riders", "packers" },
        "lines": [ { "key": "box_revenue" | "tiffin_revenue" | "produce" | "kitchen" | "delivery"
                            | "packing" | "captain" | "fees" | "acquisition",
                     "amount_paise": -1840000,          costs negative, exact paise
                     "source": "measured" | "assumed",
                     "basis": { "kind": "times", "qty": 412, "unit": "box", "rate_paise": 4000 }
                            | { "kind": "share", "bp": 7600, "of": "box_revenue" } } ],
        "weeks": [ { "week", "contribution_paise" } ] }
```

**What is measured** (from M12's facts; otherwise the saved assumption is used and `sources` says so):

| Input | Measured as | Measurable from |
|---|---|---|
| `aov_paise` | (box + add-on revenue of delivered boxes − refunds − issue credits) ÷ boxes delivered | S2 (M4) |
| `margin_bp` | 1 − (COGS of packed lines ÷ revenue) | When M6 §3.4 records lot costs and `hub_day_facts.cogs_paise` is set |
| `delivery_paise` | Σ `cost_paise` of the hub's box routes ÷ boxes on them (M7's route cost model) | S5 (M7) |
| `packing_paise` | Packer earnings ÷ boxes packed (M8 rate card, ₹4 an order); hub overheads stay in the assumption | S6 (M8) |
| `captain_paise` | `bag_delivered` earnings ÷ boxes delivered | S6 (M8) |
| `payment_fee_bp` | Never in v1: M5 does not store Razorpay's fee | Follow-up for M5: store `fee_paise` from the payment entity |
| `cac_paise` | Never measured: marketing spend is outside the system | Always an assumption |

- **Arithmetic** is integer, the same as the console's `boxEconomics`. Money is in paise and percentages in basis points. Each percentage step is rounded half up to a whole paisa, so the server and the console's live what-if give the same numbers on shared fixtures (§9).
- **The P&L's lines** use measured totals where they exist (revenue, kitchen, captain, packing, delivery). `produce` is the measured COGS, or the share `10,000 − margin_bp` of box revenue; `fees` and `acquisition` are assumed. `amount_paise` is exact, and the console rounds for display.
- **Franchise owners** get only their hubs' aggregates. Hub managers do not get P&L or unit economics (the franchise's business).

| Mock today (console) | Contract |
|---|---|
| `GET /v1/analytics/unit-economics?hub_id=` → `{ assumptions, per_box, history }` | Adds `from`/`to`, `data_source`, `measured`, `used`, `sources`, `weeks`; `history` moves to the assumptions endpoint |
| `PUT /v1/hubs/{hub}/unit-economics-assumptions` | `PUT /v1/analytics/hubs/{hub_id}/assumptions` with If-Match (the `/v1/hubs` path belongs to M6); same body and `errors[]` |
| Default `captain_paise: 1000` | 600 (₹6 a bag, decided 2026-10-09) |
| `GET /v1/analytics/pnl` lines rounded to a whole rupee; no `source` | Exact paise and a `source` per line; `partners_paise` replaces `captains_paise` and `kitchens_paise`; `periods` (the month picker) is dropped: any of the last 24 months |

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
The `analytics` schema: the fact tables, `assumptions`, `assumption_changes` and `refresh_runs` (§3.2), and the materialised views `mv_daily_hub_kpis`, `mv_weekly_unit_economics`, `mv_cohorts`, `mv_partner_earnings_weekly` and `mv_impact_monthly`. Only M12's refresh jobs write the fact tables; only the assumptions PUT writes `assumptions`.

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

The built `analytics/permissions.py` on `feat/M10-forecast-v1` grants `analytics:read` to admin, hub_manager and franchise_owner and `analytics:ai_health` to admin; the rest are added with this contract.

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

**Follow-ups for other specs** (each a facade or event addition, reviewed with that module):
- **M1:**
  - `household_facts`.
  - §3.5's purge matrix gains M12: the consumer of `identity.user_deleted` deletes `fact_households` and `fact_cycles`, and `fact_orders` is kept like M4's orders.
  - §3.3 notes that M12 has no export section.
- **M3:** `cycle_facts` (with `approved_before_cutoff`, `edits`, `plan_source` and the pantry feedback taps), and `delivery_days(society_id)` on the facade for the public page.
- **M4:**
  - `order_facts`, `issue_facts`, `open_issue_summary` and `today_counts`.
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

## 11. Open questions
- **`data_source` values.** Transactional tables store `real | synthetic`; handbook 05 §5.10 says `synthetic | pilot | live`; M10's tables use `synthetic | pilot | live | mixed`; the console mocks use `simulator`/`sim` and `pilot`.
  - *Recommendation:* M12 takes the stored `real | synthetic`, and the console labels `real` as "Pilot" until launch.
  - Later, an ADR could rename `real` to `pilot` across the schemas before the pilot starts. The cost is one migration per module, and it matters for the paper's tables.
- **Experiments endpoint.** *Recommendation:* cut from S9. Nothing assigns experiments, and the B9 uplift offers are a stretch feature. The simulator A/B lives in the eval harness.
- **Hub managers and P&L.** *Recommendation:* no, P&L and unit economics are for franchise owners and admins; hub managers get KPIs and the today board.
- **Refresh cadence on the free tier.** *Recommendation:* 15 minutes for the daily views (96 short runs a day on cron-job.org) and nightly for the rest. If Neon compute hours run short, move `refresh_analytics` to 30 minutes, since nothing else depends on it.
- **The small-number threshold of 5** for partner distributions, impact and the public page. It is shared with M9 §3.7. *Recommendation:* 5.
- **Default assumptions.** *Recommendation:* the console mock's numbers (AOV ₹620, margin 24 %, delivery ₹40, packing ₹30, payment fee 1.2 %, CAC ₹13), with the captain at the decided ₹6. These are hypotheses (handbook 02 §5), shown as "assumed" until measured.
- **Decided in this draft (owner to confirm):**
  - ~~Can franchise owners see household-level data?~~ No: aggregates only, in every M12 response (§3.3).
  - ~~Read-only views over other schemas?~~ No: facades into M12's own fact tables (§3.1).

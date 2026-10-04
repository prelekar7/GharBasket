# M3 — Plans & Subscriptions (Weekly Autopilot cycle)

**Status:** Draft v1; §3.1 (subscription, delivery days, the weekly cycle, edits, approve, skip, feedback, Budget Lock) frozen for S3
**Owner:** Prathamesh Relekar (backend), Yash Salunkhe (screens) · **QA:** Rhythm Thakur, Manish Raje
**Phase / Sprint:** P2 / S3 (rule-based box); AI plans plugged in during P4 / S7–S8
**Postgres schema:** `subscriptions` · **Python package:** `gharbasket.modules.subscriptions`

## 1. Purpose
Owns the household's subscription and the weekly Autopilot cycle: plans, society delivery days, Budget Lock contracts, weekly cycles and box lines (the "Parchi"), edits, skips and approvals before cut-off. It turns ML plans (or rule-based fallbacks) into an editable draft and hands approved boxes to M4. It does not take payment (M5) or lock orders (M4).

## 2. User stories
| # | Story | Acceptance criteria |
|---|---|---|
| US-1 | As a customer, I want my weekly box drafted for me so that I don't plan groceries. | At T-3 18:00 a cycle in `AWAITING_APPROVAL` exists with lines and reason codes. |
| US-2 | As a customer, I want to swap, remove or change quantities so that the box fits my week. | Edits allowed until cut-off; `version` increments; conflicting edit returns 412. |
| US-3 | As a customer, I want to approve the week in one tap so that it is confirmed. | `subscriptions.box_approved` published; status `APPROVED`. |
| US-4 | As a customer, I want to skip a week so that I'm not charged while travelling. | Status `SKIPPED`; no order created; `subscriptions.box_skipped` published. |
| US-5 | As a customer, I want auto-approve so that the box goes through even if I forget. | Applies only if the pre-debit notice was sent ≥ 24 h before cut-off. |
| US-6 | As a customer, I want a fixed monthly sabzi budget (Budget Lock) so that my bill is predictable. | Budget stored per month; plans respect it; spent tracked. |
| US-7 | As a customer, I want to pause my subscription so that I can stop temporarily. | `paused_until` respected by cycle generation. |
| US-8 | As a customer, I want to see why each item is in my box so that I trust the plan. | Each line has `reason_code` + params rendered as chips. |

## 3. API endpoints
| Method | Path | Permission / role | Idempotency | Notes |
|---|---|---|---|---|
| GET | `/v1/plans` | Authenticated | — | Plan catalogue |
| POST | `/v1/subscriptions` | customer | Required | Choose plan + payment mode; one per household |
| GET / PATCH | `/v1/subscriptions/current` | owner | — | Pause/resume, payment mode |
| GET | `/v1/cycles/current` | owner | — | This week's cycle with meal plan, lines, reasons, Budget Lock |
| GET | `/v1/cycles/preview` | owner | — | The first week, from the cohort prior (W1 step 6); not saved |
| GET | `/v1/cycles/{id}` | owner | — | Any cycle (history) |
| PATCH | `/v1/cycles/{id}/lines` | owner | `If-Match` | `ops`: remove, add, qty, substitute; records feedback |
| POST | `/v1/cycles/{id}/approve` | owner | Required + `If-Match` | → M4 order |
| POST | `/v1/cycles/{id}/skip` | owner | Required | Before the cut-off |
| POST | `/v1/cycles/{id}/feedback` | owner | Required | After delivery: ran out, wasted, just right (the pantry twin's signals) |
| GET / PUT | `/v1/budget-lock` | owner | Required (PUT) | Month budget and what the month's boxes spent |
| GET | `/v1/societies/{id}/delivery-days` | Authenticated | — | |
| PUT | `/v1/admin/societies/{id}/delivery-days` | hub_manager (hub), admin | Required | |

### 3.1 Subscription and weekly cycle contract (frozen for S3)
The Week (Thali), Box (Parchi), substitute sheet, skip/pause, Budget Lock and first-week preview screens, and the backend, build against these shapes. Changing them needs a PR reviewed by both Prathamesh and Yash. Every endpoint needs sign-in (M1 §3.1); a customer only ever sees their own household.

**Statuses of a cycle:** `draft` (generated, notice not sent yet), `awaiting_approval`, `approved`, `skipped`, `cancelled`, `locked` (mirrored from the order after the cut-off). `version` increases on every change; edits, approve and skip send `If-Match`. **The cut-off is the delivery date's**: 21:00 IST the day before (M4 §3.1); after it, `PATCH`, approve and skip answer `subscriptions.cutoff_passed`.

```
Plan : { "code": "autopilot" | "tiffin" | "salad", "name": "…", "fee_paise": 0, "features": ["weekly_box", "budget_lock"] }
GET /v1/plans                      200 { "items": [ Plan ] }      S3 ships "autopilot" only

Subscription : { "id", "household_id", "plan": "autopilot", "status": "active" | "paused" | "ended",
                 "payment_mode": "wallet" | "autopay",         autopay is refused until the pilot
                 "paused_until": "2026-11-01" | null, "auto_approve": false, "started_at" }
POST /v1/subscriptions             Idempotency-Key; { "plan": "autopilot", "payment_mode": "wallet" }
  201 Subscription · 409 "subscriptions.exists" (one per household) · 422 "subscriptions.payment_mode_unavailable"
GET /v1/subscriptions/current      200 Subscription | 404 "subscriptions.not_found"
PATCH /v1/subscriptions/current    { "paused_until"?: "2026-11-01" | null, "auto_approve"?: true }   200 Subscription
                                   a pause skips cycles whose delivery date is before paused_until

DeliveryDay : { "weekday": 1, "slot": { "id", "label", "start", "end" } }          0 = Monday
GET /v1/societies/{id}/delivery-days            200 { "items": [ DeliveryDay ] }
PUT /v1/admin/societies/{id}/delivery-days      hub_manager of the society's hub, or admin
  { "items": [ { "weekday": 1, "slot_id": "<uuid>" } ] }   1 to 3 days; slots of that hub   200 { "items" }

Meal : { "slot": "breakfast" | "lunch" | "dinner", "recipe_id": "<uuid>" | null, "name": "Varan bhaat" | null,
         "tiffin": false, "sides": ["Chapati"] }             null recipe + tiffin: a no-cook slot
CycleLine : {
  "product_id", "slug", "name",                                 name in Accept-Language
  "qty": 2, "step": { "qty": 250, "unit": "g", "label": "250 g" },
  "unit_price_paise": 2000, "total_paise": 4000,              today's hub price; frozen at approval
  "reason_code": "meal_plan" | "daily_use" | "in_season" | "price_swap" | "swapped_by_you" | "added_by_you"
               | "fallback_rules" | "health_tag" | "pantry_low",
  "reason_params": { "recipes": ["varan-bhaat"], "from": "tomato", "pct": 40, … } | null,
  "source": "ai" | "rule" | "customer",
  "replaces": { "product_id", "slug", "name", "unit_price_paise" } | null }   when substituted
CycleOut : {
  "id", "household_id", "week_start": "2026-10-05",             Monday
  "delivery_date": "2026-10-06", "slot": { "id", "label", "start", "end" },
  "cutoff_at": "2026-10-05T15:30:00Z", "status", "version",
  "meal_plan": { "source": "ml" | "fallback", "days": [ { "date", "meals": [ Meal ] } ] },
  "lines": [ CycleLine ], "total_paise",
  "highlights": [ { "code": "in_season" | "tiffin_day" | "health_tag" | "festival" | "budget_near", "params": { } } ],
  "budget_lock": { "month": "2026-10", "budget_paise", "spent_paise", "remaining_paise" } | null,
  "captain": { "name", "flat" } | null,                         null until M9 captains (S4)
  "order_id": "<uuid>" | null,                                   after approval
  "generated_at", "approved_at" | null }

GET /v1/cycles/current             200 CycleOut (the cycle whose delivery is next; 404 "subscriptions.no_cycle"
                                   before the first generation or while paused)
GET /v1/cycles/preview             200 CycleOut with id null and status "preview": the first week from the
                                   household's cohort prior and today's prices (W1); nothing is saved
GET /v1/cycles/{id}                200 CycleOut | 404

PATCH /v1/cycles/{id}/lines        If-Match; { "ops": [ Op ] }   1 to 20 ops, applied in order
  Op : { "op": "remove", "product_id" } | { "op": "add", "product_id", "qty" }
     | { "op": "qty", "product_id", "qty" } | { "op": "substitute", "product_id", "substitute_id" }
  200 CycleOut (version + 1)       qty 1 to 20 steps; a substitute must come from
                                   GET /v1/products/{id}/substitutes (M2); every op writes an
                                   edit_feedback row (removed, added, qty_up, qty_down, swapped)
  409 "subscriptions.cutoff_passed" · 409 "subscriptions.not_editable" (skipped, cancelled, locked)
  412 "subscriptions.version_conflict" · 422 "subscriptions.product_unknown", "subscriptions.substitute_unknown",
  "subscriptions.qty_invalid", "subscriptions.budget_exceeded" (with "over_paise", only when the Budget Lock is on)

POST /v1/cycles/{id}/approve       Idempotency-Key + If-Match; 200 CycleOut (approved, order_id set)
                                   M4 creates the order with these lines and prices in the same
                                   transaction (orders.api.create_from_cycle, so a full slot answers
                                   409 "orders.slot_full" here) and subscriptions.box_approved goes out
                                   for M11 and analytics; an edit after approval (before the cut-off)
                                   keeps the status, M4 replaces the box lines and the event goes again;
                                   approving an approved week again changes nothing
POST /v1/cycles/{id}/skip          Idempotency-Key; 200 CycleOut (skipped); publishes subscriptions.box_skipped
                                   → M4 cancels the order if one exists; the Budget Lock spends nothing
POST /v1/cycles/{id}/feedback      Idempotency-Key; from delivery day, for 14 days; items of this box
  { "items": [ { "product_id", "signal": "ran_out" | "wasted" | "just_right" } ] }   201 { "recorded": 3 }
                                   the pantry twin's observations (handbook 08 M-A); one row per
                                   product, the latest tap wins

BudgetLock : { "month": "2026-10", "budget_paise": 600000, "spent_paise": 138000, "remaining_paise": 462000 }
GET /v1/budget-lock?month=         200 BudgetLock | 404 "subscriptions.no_budget_lock"
PUT /v1/budget-lock                Idempotency-Key; { "month": "2026-10", "budget_paise": 600000 | null }
                                   ≥ ₹500; this month or next; 200 BudgetLock · null switches it off: 204
                                   422 "subscriptions.budget_invalid" (other months, under ₹500)
```

**How a cycle is generated** (`generate_weekly_plans`, T-3 18:00 IST, per hub, under the job's advisory lock; `scheduled_for` decides the week):
1. For every active subscription not paused for that delivery date: the household's rules (M1) and the hub's active recipes with today's prices (M2) go to `intel_gateway.api.plan_week` (recorded in `intel.predictions`, `source` ml or fallback).
2. The plan becomes lines: each recipe's ingredients × household size ÷ servings, summed over the week, rounded up to steps (`source: ai`, `reason_code: meal_plan`, `reason_params.recipes`); plus the **family-size rule table** for daily-use items (milk, atta, oil, salt, curd, fruit per adult equivalent per week; `source: rule`, `reason_code: daily_use`) for a household's first boxes; once it has a locked box, the pantry twin (M10 §3.3) replaces those quantities with its own (`reason_code: pantry_low`, `source: ai`, params `mean, p10, p90, weeks`). **Carry-over:** a staple (shelf life over 96 h) the household's approved or locked boxes of the last 3 weeks already carried is not ordered again until that quantity is used up; perishables are bought fresh every week.
3. With the Budget Lock on, the cheapest substitutes bring the box under the month's remaining budget (`price_swap`), never below the hard rules; if it still does not fit, the box is generated anyway and `highlights` carries `budget_near`.
4. Status `draft` → `awaiting_approval` once `send_box_ready_notices` (T-3 18:30, M11) marks `notice_sent_at`; S3 sets it in the same job until M11 exists.
5. A planner failure gives last week's lines, or the rule table for a new household (`reason_code: fallback_rules`, `meal_plan.source: fallback`).
6. `apply_auto_approve` (T-1 20:55 IST): `awaiting_approval` cycles whose subscription has `auto_approve` and whose `notice_sent_at` is at least 24 h before the cut-off are approved by the system.

**Events** (payloads are the contract for M4 and M11):
- `subscriptions.cycle_generated { cycle_id, household_id, hub_id, delivery_date, total_paise, source }`
- `subscriptions.box_approved { cycle_id, household_id, hub_id, society_id, delivery_date, slot_id, version, lines: [ { product_id, qty, unit_price_paise, reason_code, reason_params } ], total_paise }` (M4's `ApprovedCycle`; sent again after an edit to an approved cycle)
- `subscriptions.box_skipped { cycle_id, household_id, delivery_date }`
- `subscriptions.subscription_paused { subscription_id, household_id, paused_until }`
- Consumed: `orders.locked` (mirror `locked` and `order_id`), `orders.cancelled` with reason `payment` (mirror `cancelled`), `orders.delivered` (open the feedback window), `identity.household_updated` (the next cycle uses the new rules).

**Error codes**

| Status | Code | When |
|---|---|---|
| 404 | `subscriptions.not_found`, `subscriptions.no_cycle`, `subscriptions.no_budget_lock` | Nothing yet, or someone else's |
| 409 | `subscriptions.exists` | A second subscription for the household |
| 409 | `subscriptions.cutoff_passed` | Edit, approve or skip after 21:00 IST the day before delivery |
| 409 | `subscriptions.not_editable` | The cycle is skipped, cancelled or locked |
| 412 | `subscriptions.version_conflict` | `If-Match` is not the current version |
| 422 | `subscriptions.payment_mode_unavailable` | Autopay before the pilot |
| 422 | `subscriptions.product_unknown`, `subscriptions.substitute_unknown`, `subscriptions.qty_invalid`, `subscriptions.budget_exceeded` | Edits |
| 422 | `subscriptions.feedback_window_closed` | Feedback more than 14 days after delivery, or before it |
| 422 | `subscriptions.budget_invalid` | A Budget Lock for another month, or under ₹500 |
| 400 / 422 / 428 | `request.idempotency_key_required`, `request.idempotency_mismatch`, `request.precondition_required`, `request.invalid` | As in M0 |

**For the mobile mocks** (`mocks/subscriptions.js` → contract)

| Mock today | Contract |
|---|---|
| `product_id: "methi"` (slug) | `product_id` is a UUID; `slug` alongside |
| `qty_label` | `qty` × `step.label` |
| `price_paise` per line | `unit_price_paise` and `total_paise` |
| `swapped_from_paise`, `replaces: "<slug>"` | `replaces { product_id, slug, name, unit_price_paise }` |
| `delivery_window { from, to }` | `slot { id, label, start, end }` |
| `cutoff_at` with `+05:30` | UTC with `Z`; show in IST |
| `status: "ready"` | `awaiting_approval` |
| PATCH body `{ op, product_id, substitute_id }` | `{ "ops": [ … ] }`; same op names, plus `remove`, `add`, `qty` |
| 409 `orders.cutoff_passed` on the cycle | `subscriptions.cutoff_passed` |
| `meal_plan.days[].meals[]` with `name` only | plus `recipe_id`, `sides`, and `meal_plan.source` |
| `captain` always set | `null` until M9 captains |
| no preview endpoint | `GET /v1/cycles/preview` for onboarding's first-week screen |

## 4. Tables owned
| Table | Key columns |
|---|---|
| `subscriptions.plans` | id, code, kind (autopilot, tiffin, salad), fee_paise, features jsonb |
| `subscriptions.subscriptions` | id, household_id, plan_id, status, started_at, paused_until, payment_mode (wallet, autopay) |
| `subscriptions.society_delivery_days` | society_id, weekday, slot_id |
| `subscriptions.budget_locks` | id, household_id, month, budget_paise, spent_paise |
| `subscriptions.weekly_cycles` | id, household_id, hub_id, society_id, subscription_id, week_start, delivery_date, slot_id, cutoff_at, plan_prediction_id (intel.predictions), plan_source (ml, fallback), status, generated_at, notice_sent_at, approved_at, auto_approved, order_id, version, highlights jsonb, data_source |
| `subscriptions.box_lines` | cycle_id, product_id, qty (steps), step_qty, step_unit, unit_price_paise, reason_code, reason_params jsonb, source (ai, rule, customer), replaces_product_id |
| `subscriptions.edit_feedback` | id, cycle_id, product_id, action (removed, added, qty_up, qty_down, swapped), substitute_product_id, at |
| `subscriptions.pantry_feedback` | id, cycle_id, household_id, product_id, signal (ran_out, wasted, just_right), at — the pantry twin's observations |

## 5. Events
**Published:** `subscriptions.cycle_generated`, `subscriptions.box_approved`, `subscriptions.box_skipped`, `subscriptions.subscription_paused` (payloads in §3.1).
**Consumed:** `orders.locked`, `orders.cancelled`, `orders.delivered`, `identity.household_updated`. (Plans are requested synchronously through `intel_gateway.api` inside the generation job, so `intel.plans_ready` is not needed in S3.)

## 6. Jobs
| Job | Schedule | Notes |
|---|---|---|
| `generate_weekly_plans` | T-3 18:00 IST per hub (cron-job.org → `POST /v1/internal/jobs/generate_weekly_plans`) | Plans via `intel_gateway.api.plan_week`; rule-table fallback; §3.1 |
| `send_box_ready_notices` | T-3 18:30 | Via M11 (S3: the generation job sets `notice_sent_at`); pre-debit notice via M5 at the pilot |
| `remind_unapproved` | T-1 19:00 | M11 |
| `apply_auto_approve` | T-1 20:55 IST | Only where `notice_sent_at` ≤ cut-off − 24 h |

Scheduled jobs are internal job endpoints (`POST /v1/internal/jobs/<name>`, ADR-0021) triggered by cron-job.org for minute-level timing (ADR-0025); event-triggered work runs in outbox consumers. Rules use `scheduled_for`, so a late start never lets an edit through after the cut-off.

## 7. Permissions
| Permission | Roles (scope) |
|---|---|
| `cycles:write` | customer (own household) |
| `cycles:read` | customer (own), hub_manager (hub) |
| `delivery_days:write` | hub_manager (hub), admin |

## 8. Screens
**Mobile:** Week (Thali), Box (Parchi), Substitute sheet, Add-ons, Skip/Pause, Budget Lock settings. **Console:** society delivery days, cycle status overview per hub.

## 9. Tests required
- Unit: rule-based fallback box by family size; auto-approve eligibility.
- Integration: edits after cut-off rejected; optimistic-lock conflicts; feedback rows written.
- Property: Budget Lock never exceeded by generated box (within tolerance).
- E2E: generate → edit → approve; skip week.

## 10. Dependencies
M1 (households via `identity.api`), M2 (products, prices), M10 (plans via `intel_gateway.api`), M5 (pre-debit notice), M11 (notifications). Hands off to M4 via `subscriptions.box_approved`.

## 11. Open questions and decisions
- **Decided (S3 contract):** add-ons are lines on the box order (M4 §3.1), never separate orders.
- **Decided (S3 contract):** one subscription per household; one cycle per delivery day; a society may have 1 to 3 delivery days a week (the pilot uses 1).
- **Decided:** the "ran out / wasted" taps live in `subscriptions.pantry_feedback` (the twin's observations), separate from edit feedback.
- Delivery frequency per society for the pilot: 1 day a week at MS-A; twice weekly only if captains ask.

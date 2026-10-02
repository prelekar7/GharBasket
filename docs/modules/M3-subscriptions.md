# M3 — Plans & Subscriptions (Weekly Autopilot cycle)

**Status:** Draft v1 — refine at phase start (spec-first)
**Owner:** Yash Salunkhe · **Reviewer:** Prathamesh Relekar · **QA:** Rhythm Thakur, Manish Raje
**Phase / Sprint:** P2 / S3 (rule-based box); AI plans plugged in during P4 / S7–S8
**Postgres schema:** `subscr` · **Python package:** `gharbasket.modules.subscriptions`

## 1. Purpose
Owns the household's subscription and the weekly Autopilot cycle: plans, society delivery days, Budget Lock contracts, weekly cycles and box lines (the "Parchi"), edits, skips and approvals before cut-off. It turns ML plans (or rule-based fallbacks) into an editable draft and hands approved boxes to M4. It does not take payment (M5) or lock orders (M4).

## 2. User stories
| # | Story | Acceptance criteria |
|---|---|---|
| US-1 | As a customer, I want my weekly box drafted for me so that I don't plan groceries. | At T-3 18:00 a cycle in `AWAITING_APPROVAL` exists with lines and reason codes. |
| US-2 | As a customer, I want to swap, remove or change quantities so that the box fits my week. | Edits allowed until cut-off; `version` increments; conflicting edit returns 412. |
| US-3 | As a customer, I want to approve the week in one tap so that it is confirmed. | `subscr.box_approved` published; status `APPROVED`. |
| US-4 | As a customer, I want to skip a week so that I'm not charged while travelling. | Status `SKIPPED`; no order created; `subscr.box_skipped` published. |
| US-5 | As a customer, I want auto-approve so that the box goes through even if I forget. | Applies only if the pre-debit notice was sent ≥ 24 h before cut-off. |
| US-6 | As a customer, I want a fixed monthly sabzi budget (Budget Lock) so that my bill is predictable. | Budget stored per month; plans respect it; spent tracked. |
| US-7 | As a customer, I want to pause my subscription so that I can stop temporarily. | `paused_until` respected by cycle generation. |
| US-8 | As a customer, I want to see why each item is in my box so that I trust the plan. | Each line has `reason_code` + params rendered as chips. |

## 3. API endpoints
| Method | Path | Permission / role | Idempotency | Notes |
|---|---|---|---|---|
| GET | `/v1/plans` | Authenticated | — | Plan catalogue |
| POST | `/v1/subscriptions` | customer | Required | Choose plan + payment mode |
| GET / PATCH | `/v1/subscriptions/{id}` | owner | — | Pause/resume, payment mode |
| GET | `/v1/cycles/current` | owner | — | Current week's cycle with lines and meal plan reference |
| PATCH | `/v1/cycles/{id}/lines` | owner | — | `If-Match` version; add/remove/qty/substitute |
| POST | `/v1/cycles/{id}/approve` | owner | Required | |
| POST | `/v1/cycles/{id}/skip` | owner | Required | |
| GET / PUT | `/v1/budget-lock` | owner | Required (PUT) | Month budget |
| GET | `/v1/societies/{id}/delivery-days` | Authenticated | — | |
| PUT | `/v1/admin/societies/{id}/delivery-days` | hub_manager (hub) | Required | |

## 4. Tables owned
| Table | Key columns |
|---|---|
| `subscr.plans` | id, code, kind (autopilot, tiffin, salad), fee_paise, features jsonb |
| `subscr.subscriptions` | id, household_id, plan_id, status, started_at, paused_until, payment_mode (wallet, autopay) |
| `subscr.society_delivery_days` | society_id, weekday, slot_id |
| `subscr.budget_locks` | id, household_id, month, budget_paise, spent_paise |
| `subscr.weekly_cycles` | id, household_id, week_start, delivery_date, plan_ref, status, generated_at, approved_at, auto_approved, version, data_source |
| `subscr.box_lines` | cycle_id, product_id, qty, unit, reason_code, reason_params jsonb, source (ai, rule, customer), locked |
| `subscr.edit_feedback` | id, cycle_id, product_id, action (removed, added, qty_up, qty_down, swapped), at |

## 5. Events
**Published:** `subscr.cycle_generated`, `subscr.box_approved`, `subscr.box_skipped`, `subscr.subscription_paused`.
**Consumed:** `intel.plans_ready` (materialise drafts), `billing.payment_failed` (flag cycle), `identity.household_updated`.

## 6. Jobs
| Job | Schedule | Notes |
|---|---|---|
| `generate_weekly_plans` trigger | T-3 18:00 per hub | Requests plans from M10; falls back to rule-based box on timeout |
| `send_box_ready_notices` | T-3 18:30 | Via M11; pre-debit notice via M5 |
| `remind_unapproved` | T-1 19:00 | |
| `apply_auto_approve` | T-1 20:55 | Only where notice ≥ 24 h |

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
M1 (households via `identity.api`), M2 (products, prices), M10 (plans via `intel_gateway.api`), M5 (pre-debit notice), M11 (notifications). Hands off to M4 via `subscr.box_approved`.

## 11. Open questions
- Delivery frequency per society (weekly vs twice weekly) for the pilot.
- Whether add-ons are separate orders or lines on the box order.

# M4 — Orders & Checkout

**Status:** Draft v1 — refine at phase start (spec-first)
**Owner:** Yash Salunkhe · **Reviewer:** Prathamesh Relekar · **QA:** Rhythm Thakur, Manish Raje
**Phase / Sprint:** P2 / S2 (cut-off in S3)
**Postgres schema:** `orders` · **Python package:** `gharbasket.modules.orders`

## 1. Purpose
The order of record for boxes, tiffins and add-ons: creation from approved cycles and direct purchases, delivery slots and capacity, the order state machine, cut-off locking, shortages/substitutions, and live status (SSE). Payment is delegated to M5; fulfilment to M6/M7.

## 2. User stories
| # | Story | Acceptance criteria |
|---|---|---|
| US-1 | As a customer, I want my approved box to become an order automatically so that I don't check out manually. | `subscriptions.box_approved` → order `APPROVED` with lines and prices snapshot. |
| US-2 | As the system, I want all approved orders locked at cut-off so that procurement uses committed demand. | 21:00 job moves orders to `LOCKED` after successful payment in under 60 s for 2,000 orders. |
| US-3 | As a customer, I want to add add-ons before cut-off so that I get extra items on the same delivery. | Add-on lines priced from the current list; allowed until `LOCKED`. |
| US-4 | As a customer, I want live order status so that I know when the delivery arrives. | SSE stream emits status changes and ETA. |
| US-5 | As a customer, I want fair handling of shortages so that I'm not charged for missing items. | Shortage → substitution per preference or partial refund event to M5. |
| US-6 | As a customer, I want to cancel before cut-off for free so that plans can change. | Cancel allowed before `LOCKED`; refund not needed (no capture yet). |
| US-7 | As a hub manager, I want slot capacities so that we never overbook a route. | Slot booking fails when capacity is reached. |

## 3. API endpoints
| Method | Path | Permission / role | Idempotency | Notes |
|---|---|---|---|---|
| GET | `/v1/orders` | customer (own), hub staff (hub) | — | Filters: status, date |
| GET | `/v1/orders/{id}` | owner, hub staff | — | Includes lines and timeline |
| GET | `/v1/orders/{id}/events` | owner, hub staff | — | SSE |
| POST | `/v1/orders` | customer | Required | Direct order (tiffin, add-on only) |
| POST | `/v1/orders/{id}/addons` | owner | Required | Before cut-off |
| POST | `/v1/orders/{id}/cancel` | owner | Required | Before `LOCKED` |
| GET | `/v1/slots?hub_id=&date=` | Authenticated | — | Availability |
| POST | `/v1/admin/slots` | hub_manager (hub) | Required | |
| POST | `/v1/hubs/{id}/cutoff/run` | admin | Required | Manual re-run (idempotent) |

## 4. Tables owned
| Table | Key columns |
|---|---|
| `orders.orders` | id, household_id, hub_id, kind (box, tiffin, addon), cycle_id, delivery_date, slot_id, status, subtotal_paise, fees_paise, discount_paise, total_paise, version, data_source |
| `orders.order_lines` | order_id, product_id, qty, unit, unit_price_paise, substituted_from, shortage_qty |
| `orders.order_events` | id, order_id, from_status, to_status, actor, payload jsonb, at |
| `orders.slots` | id, hub_id, label, start_time, end_time, capacity |
| `orders.slot_bookings` | slot_id, date, order_id |

## 5. Events
**Published:** `orders.locked`, `orders.cancelled`, `orders.shortage_substituted`, `orders.packed`, `orders.dispatched`, `orders.at_society`, `orders.delivered`, `orders.delivery_failed` (fulfilment statuses are applied by M6/M7 through `orders.api`, and M4 publishes the events).
**Consumed:** `subscriptions.box_approved`, `billing.payment_captured`, `billing.payment_failed`.

## 6. Jobs
| Job | Schedule | Notes |
|---|---|---|
| `cutoff_lock` | T-1 21:00 per hub | Postgres advisory lock; batched updates; requests debit/capture from M5 |
| `payment_grace_expiry` | T-1 23:00 | `PAYMENT_FAILED` → `CANCELLED` |

## 7. Permissions
| Permission | Roles (scope) |
|---|---|
| `orders:read` | customer (own), packer/rider/captain/hub_manager (hub/society) |
| `orders:write` | customer (own, before lock) |
| `orders:transition` | Internal (M6/M7 via api.py), admin |

## 8. Screens
**Mobile:** Orders list, order detail with timeline and live tracking, add-ons. **Console:** orders board (by status/slot), cut-off monitor, slot configuration.

## 9. Tests required
- Unit: state machine transitions (all illegal transitions rejected).
- Property (hypothesis): random sequences of edits/cancels/locks never yield an order in two states or a negative total.
- Integration: cut-off idempotency (run twice → no double lock or debit).
- Load (k6): cut-off with 2,000 orders under 60 s.
- E2E: approve → lock → status stream to delivered.

## 10. Dependencies
M3 (approved cycles via events), M2 (prices via `catalog.api`), M5 (debit/capture via `billing.api`), M6/M7 call `orders.api` for fulfilment transitions.

## 11. Open questions
- Delivery fee policy for non-unlocked societies.
- Whether tiffin orders share slots with boxes or have separate lunch slots (proposed: separate).

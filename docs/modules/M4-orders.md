# M4 — Orders & Checkout

**Status:** Draft v1; §3.1 (slots, box orders, add-ons, cancel, cut-off) frozen for S2; §3.2 (order issues) frozen on 2026-10-08
**Owner:** Prathamesh Relekar (backend), Yash Salunkhe (screens) · **QA:** Rhythm Thakur, Manish Raje
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
| GET | `/v1/orders` | customer (own), hub staff (hub) | — | Filters: status, from, to; cursor |
| GET | `/v1/orders/{id}` | owner, hub staff | — | Includes lines and timeline; ETag |
| GET | `/v1/orders/{id}/events` | owner, hub staff | — | SSE (P3; S2 clients poll the detail) |
| POST | `/v1/orders` | customer | Required | Direct order (tiffin, add-on only); P3 |
| POST | `/v1/orders/{id}/addons` | owner | Required + `If-Match` | Add, change or remove add-on lines before cut-off |
| POST | `/v1/orders/{id}/cancel` | owner | Required | Before `locked` |
| POST | `/v1/orders/{id}/issues` | owner | Required | Report a problem with a delivery; auto-credit or triage (§3.2) |
| GET | `/v1/orders/{id}/issues` | owner, hub staff | — | The order's issues |
| GET | `/v1/slots?hub_id=&date=` | Authenticated | — | Availability |
| POST | `/v1/admin/slots` | hub_manager (hub), admin | Required | Define a slot |
| PATCH | `/v1/admin/slots/{id}` | hub_manager (hub), admin | — | Capacity, active |
| POST | `/v1/internal/jobs/cutoff_lock` | operator token | — | The cut-off (replaces `/v1/hubs/{id}/cutoff/run`); idempotent per scheduled time |

### 3.1 Orders and slots contract (frozen for S2)
The Orders list, order detail, add-ons sheet and the console's orders board and slot settings, and the backend, build against these shapes. Changing them needs a PR reviewed by both Prathamesh and Yash. Every endpoint needs sign-in (M1 §3.1); a customer only ever sees their own household's orders, hub staff (`X-Active-Role`) their hub's.

**Money and time.** Amounts are integer paise. `delivery_date` is a calendar date in IST; slot times are IST clock times (`"07:00"`). Timestamps are UTC. Order reads send `Cache-Control: no-store`. **The cut-off of an order is a property of its delivery date:** 21:00 IST on the day before. The API refuses edits after that moment itself; the `cutoff_lock` job only does the follow-up work (ADR-0021).

**Statuses.** S2 uses `approved`, `locked`, `payment_failed`, `cancelled`. Fulfilment statuses arrive with M6/M7 (S4–S5) and are applied only through `orders.api`: `picking`, `packed`, `dispatched`, `at_society`, `delivered`, `undelivered`, `redelivery`, `refunded` (handbook 06 §6.2). `version` increases on every change (`If-Match` on add-ons).

```
Slot : { "id": "<uuid>", "hub_id": "<uuid>", "label": "Morning 7-9",
         "kind": "box" | "tiffin",                tiffin slots come with M8; boxes never share them
         "start": "07:00", "end": "09:00",        IST
         "capacity": 120, "booked": 37, "available": 83 }   for the requested date

GET /v1/slots?hub_id=&date=          hub_id: default the caller's household's hub (M1);
                                     date: today … today + 14 (IST), default tomorrow
  200 : { "date": "2026-11-03", "items": [ Slot ] }   active slots, by start time
  422 "orders.date_out_of_range" · 422 "orders.hub_required" (no household and no hub_id)

POST /v1/admin/slots                 hub_manager of that hub, or admin; Idempotency-Key
  request : { "hub_id", "label", "kind": "box", "start": "07:00", "end": "09:00", "capacity": 120 }
  201 : Slot (booked 0)              end > start; capacity 1 to 2000; label unique per hub
PATCH /v1/admin/slots/{id}           { "capacity"?: 150, "active"?: false }   200 Slot
                                     capacity may drop below today's bookings; new bookings
                                     then wait for the next date

OrderLine : {
  "product_id": "<uuid>", "slug": "bhindi", "name": "Bhindi",     name in Accept-Language
  "kind": "box" | "addon",
  "qty": 2, "step": { "qty": 250, "unit": "g", "label": "250 g" },   total = qty x step
  "unit_price_paise": 2000, "total_paise": 4000,                   priced when the line was made
  "reason_code": "price_swap" | "in_season" | … | null, "reason_params": { } | null,
  "substituted_from": { "product_id", "slug", "name" } | null,     set by packing (M6)
  "shortage_qty": 0 }                                              steps short at packing

OrderOut : {
  "id", "kind": "box" | "addon" | "tiffin", "status",
  "cycle_id": "<uuid>" | null, "household_id", "hub_id", "society_id",
  "delivery_date": "2026-11-03", "slot": { "id", "label", "start", "end" },
  "cutoff_at": "2026-11-02T15:30:00Z",                           21:00 IST the day before
  "lines": [ OrderLine ],
  "subtotal_paise": 69000, "fees_paise": 0, "discount_paise": 0, "total_paise": 69000,
  "version": 3, "created_at", "locked_at": "…" | null,
  "timeline": [ { "status": "approved", "at": "…", "actor": "customer" | "system" | "hub" } ] }

GET /v1/orders?status=&from=&to=&hub_id=&cursor=&limit=
  200 : Page[OrderOut without lines and timeline]   newest delivery first; limit <= 100, default 20;
                                                    hub staff must pass hub_id (their scope)
GET /v1/orders/{id}                 200 OrderOut (ETag = version) | 404 "orders.not_found"

POST /v1/orders/{id}/addons         Idempotency-Key and If-Match (the order's version)
  request : { "items": [ { "product_id": "<uuid>", "qty": 2 } ] }   qty 0 removes the add-on;
                                     1 to 20 steps; up to 20 add-on lines; box lines are
                                     edited in M3 before approval, never here
  200 : OrderOut                     priced from the hub's current list at this moment; totals
                                     recomputed; version + 1
  409 "orders.cutoff_passed" · 409 "orders.not_editable" (not approved) · 412 "request.version_mismatch"
  422 "orders.product_unknown" (with product_ids) · 422 "orders.qty_invalid" · 422 "orders.no_price"

POST /v1/orders/{id}/cancel         Idempotency-Key; while approved: 200 OrderOut (cancelled, no
                                     money moved, the slot booking freed; publishes
                                     orders.cancelled); replaying the same key answers the same;
                                     after the cut-off: 409 "orders.cutoff_passed";
                                     after locked: 409 "orders.already_locked"
```

**Cut-off handshake** (`cutoff_lock`, operator token, `scheduled_for` = T-1 21:00 IST of delivery day T):
1. For each hub, every `approved` order with `delivery_date = T`, in batches of 200, oldest first, under the job's advisory lock.
2. `billing.api.debit_for_order(household owner, order_id, total_paise, delivery_date)`: keyed by the order id, so a re-run never debits twice.
3. Paid → `locked`, `locked_at`, timeline row, event `orders.locked`.
   `InsufficientBalance` → `payment_failed`, event `orders.payment_failed` `{ order_id, household_id, shortfall_paise, grace_until }` (M11 sends the 2-hour notice).
4. `payment_grace_expiry` (T-1 23:00): still `payment_failed` → `cancelled`, event `orders.cancelled { reason: "payment" }`. A top-up during the grace period re-runs the debit for that order (consumer of `billing.wallet_topped_up`).
5. The job's summary records `locked`, `payment_failed`, `skipped_already_locked`. Target: 2,000 orders in under 60 s.

**Events** (payloads are the contract for M6/M7/M11):
- `orders.locked { order_id, household_id, hub_id, society_id, delivery_date, slot_id, total_paise, lines: [ { product_id, qty, step_qty, step_unit } ] }`
- `orders.payment_failed { order_id, household_id, shortfall_paise, grace_until }` (M5's `billing.payment_failed` is for Autopay captures, pilot phase)
- `orders.cancelled { order_id, household_id, reason: "customer" | "payment" | "skipped" }`
- Fulfilment events (`orders.packed`, `orders.dispatched`, `orders.at_society`, `orders.delivered`, `orders.delivery_failed`, `orders.shortage_substituted`) are published when M6/M7 call `orders.api.transition`.

**For other modules** (`orders/api.py`, no HTTP):
- `create_from_cycle(session, cycle: ApprovedCycle) -> Order` — M3 calls it in the approve transaction (so the customer's answer carries `order_id` and a full slot is refused on the spot; `subscriptions.box_approved` is published alongside for M11 and analytics); idempotent per `cycle_id`. **When the order already exists and is still `approved`, a higher `version` replaces its box lines and totals (add-ons are kept)**: an edit after approval re-publishes the event. `ApprovedCycle` carries `cycle_id, household_id, hub_id, society_id, delivery_date, slot_id, lines [ { product_id, qty, unit_price_paise, reason_code, reason_params } ], version`. Box line prices are frozen here; `total_paise` = Σ qty × unit price.
- `cancel_from_cycle(session, cycle_id, reason="skipped")` — a skip before the cut-off cancels the order.
- `transition(session, order_id, to, *, actor, payload=None)` — M6/M7 move fulfilment statuses; every illegal transition raises `orders.illegal_transition`; each call writes `order_events` and publishes the matching event.
- `orders_for_delivery(session, hub_id, delivery_date, statuses=("locked",)) -> list[OrderSummary]` — M6 procurement and packing, M7 routing (society, slot, lines, crates later).
- `mark_shortage(session, order_id, product_id, shortage_qty, substitute_product_id | None)` — M6 packing; a refund for the short steps goes through `billing.api.refund_to_wallet(reason="shortage")`.

**Error codes**

| Status | Code | When |
|---|---|---|
| 404 | `orders.not_found` | Unknown order, or someone else's |
| 409 | `orders.cutoff_passed` | After 21:00 IST on the day before delivery |
| 409 | `orders.not_editable` | Add-ons on an order that is not `approved` |
| 409 | `orders.already_locked` | Cancelling after the cut-off job locked it |
| 409 | `orders.illegal_transition` | A status change the state machine forbids (facade) |
| 409 | `orders.slot_full` | The slot's capacity for that date is used up (at order creation) |
| 412 | `request.version_mismatch` | `If-Match` is not the current version |
| 422 | `orders.product_unknown`, `orders.qty_invalid`, `orders.no_price` | Add-on items |
| 422 | `orders.date_out_of_range`, `orders.hub_required`, `orders.slot_invalid` | Slots |
| 403 | `auth.forbidden` | Hub staff outside their hub; a customer on another household's order |
| 400 / 422 | `request.idempotency_key_required`, `request.idempotency_mismatch`, `request.invalid` | As in M0 |

**For the mobile mocks** (Yash's cycle mock → order shapes; the Box screen keeps using M3's cycle until approval, the Orders screens use these)

| Mock today (`mocks/subscriptions.js`) | Contract |
|---|---|
| `product_id: "methi"` (slug) | `product_id` is a UUID; the slug is in `slug` |
| `qty_label: "2 judi"` | `qty` × `step.label` ("2 × 1 bunch"); format in the app |
| `price_paise` per line | `unit_price_paise` and `total_paise` |
| `swapped_from_paise`, `replaces` | `substituted_from { product_id, slug, name }` (packing-time substitutions) and `reason_params` for planner swaps |
| no status timeline | `timeline[]` with `actor`; `status` values above |
| cut-off implicit | `cutoff_at` on every order; show a countdown and disable edits after it |

### 3.2 Order issues contract (frozen on 2026-10-08)
"Report a problem" on the Orders screen, the M12 triage queue and the backend build against this. Same sign-in and review rules as §3.1. M4 records the issue; money only moves through M5's existing refund facade.

```
IssueIn : {
  "kind": "missing" | "damaged" | "quality" | "wrong_item" | "late" | "other",
  "items": [ { "product_id": "<uuid>", "qty": 1 } ],   required for missing, damaged, quality,
                                                       wrong_item; 1 to 20 lines of this order;
                                                       qty in steps, at most what was delivered
                                                       (qty − shortage_qty) minus earlier claims
  "note": "…" | null,                                  up to 500 characters
  "photo_keys": [ "order_issue_photo/<user_id>/<uuid>.jpg" ] }   0 to 3, from POST /v1/files/presign
                                                       (M0 §3.1); M4 claims them via platform.files
IssueOut : {
  "id", "order_id", "kind", "status": "open" | "credited" | "rejected",
  "items": [ { "product_id", "slug", "name", "qty", "unit_price_paise", "amount_paise" } ],
  "amount_paise": 4000,                                Σ qty × the line's paid unit price
  "credit_paise": 4000,                                0 until credited
  "note", "photo_count": 1,
  "resolution": { "by": "auto" | "hub", "note": "…" | null, "at" } | null,
  "callback_within_minutes": 120 | null,               while open: the hub calls back
  "open_reason": "photo_needed" | "over_auto_credit_limit" | "over_order_total" | "wallet_limit" | "triage" | null,
                                                       why it was not auto-credited; null unless open
  "created_at" }

POST /v1/orders/{id}/issues     Idempotency-Key required (one per report); 201 IssueOut
                                the same key replays the same answer
GET  /v1/orders/{id}/issues     200 { "items": [ IssueOut ] }   newest first; owner or hub staff
```

**When** (48 h confirmed by the owner on 2026-10-08). Line kinds (`missing`, `damaged`, `quality`, `wrong_item`) from `delivered` until **48 hours** after the `delivered` timeline row. `late` and `other` from `dispatched` until the same 48 hours. At most 3 issues per order.

**Auto-credit** (in the POST's transaction; the ₹50 rolling 7-day rule and the photo rule confirmed by the owner on 2026-10-08): an issue is credited at once when
- its kind is `missing`, or `damaged` **with at least one photo** (a valid, claimed `photo_keys` entry); `missing` needs no photo; and
- its `amount_paise`, plus everything auto-credited to the household in the **last 7 days**, stays within **₹50 (5,000 paise)**.

A `damaged` issue without a photo is still accepted (201) and is never rejected for that reason: it stays `open` with `open_reason: "photo_needed"` for console triage. M4 calls `billing.api.refund_to_wallet(session, owner_user_id, order_id, amount_paise, reason=f"issue_{issue_id.hex}")` (the existing facade, exactly once by its key `refund:<order>:issue_<hex>`), sets `status: credited`, `resolution.by: auto` and stores `refund_entry_id`. The rule credits the whole issue or nothing. If the wallet cap refuses the refund (`billing.wallet_limit`), the issue stays open (`open_reason: "wallet_limit"`). Over the ₹50 allowance it is `over_auto_credit_limit`; over what is left of the order's paid total after earlier issue credits on it, `over_order_total`; other kinds are `triage`. All credits on one order, automatic or by the hub, never add up to more than was paid for it. Everything that stays `open` goes to triage: the M12 console calls `orders.api.resolve_issue(session, issue_id, outcome, credit_paise, actor, note)`, which refunds through the same facade (credit ≤ `amount_paise`, or any amount for `late`/`other` up to the order total, and never more than is left of the order's paid total; above that, `orders.issue_credit_invalid` with `max_paise`) or rejects. Photo triage with an LLM and the trust-and-safety model (handbook 08 M-J) come later and only change who resolves.

**Photos.** Private; a hub reviewer or the owner sees them through 5-minute signed links that M4 mints on demand (the console endpoint comes with M12). Deleted 90 days after the issue is resolved (M0 §3.1).

**Events** (no note, no photo keys, no names):
- `orders.issue_reported { issue_id, order_id, household_id, hub_id, kind, amount_paise, status }` (M11 acknowledges, M12 queues the open ones)
- `orders.issue_resolved { issue_id, order_id, household_id, status, credit_paise, by }`

**Error codes** (in addition to §3.1)

| Status | Code | When |
|---|---|---|
| 404 | `orders.not_found` | Unknown order, or someone else's |
| 409 | `orders.issue_window_closed` | Before the order reached the status the kind needs, or more than 48 h after delivery |
| 409 | `orders.too_many_issues` | A fourth issue on the order |
| 422 | `orders.issue_kind_invalid` | Unknown `kind` |
| 422 | `orders.issue_items_required` | A line kind without `items` |
| 422 | `orders.issue_item_unknown` | A product that is not on the order (`product_ids`) |
| 422 | `orders.issue_qty_invalid` | More than was delivered, counting earlier claims |
| 422 | `orders.photo_key_invalid` | Not the caller's `order_issue_photo` upload, or nothing uploaded at that key |
| 400 / 422 | `request.idempotency_key_required`, `request.idempotency_mismatch`, `request.invalid` | As in M0 |

**For the mobile mocks** (`mocks/orders.js` → contract)

| Mock today | Contract |
|---|---|
| `{ kind, note? }` with `missing`, `damaged`, `late`, `other` | Same field; plus `quality`, `wrong_item`; `items` required for the line kinds; optional `photo_keys` |
| 400 `platform.idempotency_key_required` | `request.idempotency_key_required` |
| 422 `orders.invalid_issue` | `orders.issue_kind_invalid` (and the other codes above) |
| "Report a problem" on any current order | Only from `dispatched` (`late`, `other`) or `delivered` (line kinds), for 48 h |
| reply `{ id, order_id, kind, status, callback_within_minutes }` | `IssueOut`: same fields plus `items`, `amount_paise`, `credit_paise`, `resolution`, `open_reason`; `status` may already be `credited` |
| no photo step | Ask for a photo on `damaged` claims: without one the claim waits for the hub (`open_reason: "photo_needed"`) instead of being credited at once |
| no list | `GET /v1/orders/{id}/issues` |

## 4. Tables owned
| Table | Key columns |
|---|---|
| `orders.orders` | id, household_id, hub_id, society_id, kind (box, tiffin, addon), cycle_id (unique), delivery_date, slot_id, cutoff_at, status, subtotal_paise, fees_paise, discount_paise, total_paise, version, locked_at, data_source, created_at |
| `orders.order_lines` | order_id, product_id, kind (box, addon), qty, step_qty, step_unit, unit_price_paise, reason_code, reason_params jsonb, substituted_from, shortage_qty |
| `orders.order_events` | id, order_id, from_status, to_status, actor, payload jsonb, at |
| `orders.slots` | id, hub_id, label, kind (box, tiffin), start_time, end_time, capacity, active |
| `orders.slot_bookings` | slot_id, date, order_id (unique); capacity is checked under a row lock on the slot |
| `orders.order_issues` | id, order_id, household_id, hub_id, kind, status (open, credited, rejected), open_reason, note, photo_keys text[], amount_paise, credit_paise, resolved_by (auto, hub), resolver_user_id, resolution_note, resolved_at, refund_entry_id, created_by, created_at |
| `orders.order_issue_items` | issue_id, product_id, qty (steps), unit_price_paise (the line's paid price) |

## 5. Events
**Published:** `orders.locked`, `orders.payment_failed`, `orders.cancelled`, `orders.shortage_substituted`, `orders.packed`, `orders.dispatched`, `orders.at_society`, `orders.delivered`, `orders.delivery_failed`, `orders.issue_reported`, `orders.issue_resolved` (fulfilment statuses are applied by M6/M7 through `orders.api`, and M4 publishes the events). Payloads in §3.1.
**Consumed:** `subscriptions.box_approved` (→ `create_from_cycle`), `subscriptions.box_skipped` (→ `cancel_from_cycle`), `billing.wallet_topped_up` (retry a failed debit in the grace period); `billing.payment_failed` (Autopay captures, pilot phase).

## 6. Jobs
| Job | Schedule | Notes |
|---|---|---|
| `cutoff_lock` | T-1 21:00 IST (cron-job.org → `POST /v1/internal/jobs/cutoff_lock`) | Advisory lock; batches of 200; `billing.api.debit_for_order` keyed by order id; see §3.1 |
| `payment_grace_expiry` | T-1 23:00 IST (cron-job.org) | `payment_failed` → `cancelled` |
| retry after top-up | Outbox consumer of `billing.wallet_topped_up` | Re-runs the debit for a `payment_failed` order inside the grace period |

Minute-level jobs are triggered by cron-job.org (ADR-0025) against the internal job endpoints (ADR-0021); the API itself refuses edits after the cut-off, and the job uses its `scheduled_for`, not the run time.

## 7. Permissions
| Permission | Roles (scope) |
|---|---|
| `orders:read` | customer (own), packer/rider/hub_manager/franchise_owner (hub, with `X-Active-Role` and `hub_id`), admin; captains read through M7's handover endpoints |
| `orders:write` | customer (own, before lock; issues after delivery) |
| `order_issues:resolve` | hub_manager (hub), admin (M12 console, through `orders.api.resolve_issue`) |
| `orders:transition` | Internal (M6/M7 via api.py), admin |
| `slots:write` | hub_manager (hub), admin |

## 8. Screens
**Mobile:** Orders list, order detail with timeline and live tracking, add-ons. **Console:** orders board (by status/slot), cut-off monitor, slot configuration.

## 9. Tests required
- Unit: state machine transitions (all illegal transitions rejected).
- Property (hypothesis): random sequences of edits/cancels/locks never yield an order in two states or a negative total.
- Integration: cut-off idempotency (run twice → no double lock or debit).
- Load (k6): cut-off with 2,000 orders under 60 s.
- E2E: approve → lock → status stream to delivered.

## 10. Dependencies
M3 (approved cycles via events), M2 (prices via `catalog.api`), M5 (debit/capture and issue refunds via `billing.api`), M0 (issue photos via `platform.files`), M6/M7 call `orders.api` for fulfilment transitions.

## 11. Open questions and decisions
- **Decided (S2 contract):** tiffin orders use their own slots (`kind: tiffin`); boxes never share them.
- **Decided (S2 contract):** no delivery fee in S2 (`fees_paise` stays 0); the policy for non-unlocked societies is decided with M9 unlock thresholds in S9.
- Direct orders (`POST /v1/orders`) and the SSE stream arrive in P3 with tiffins and live tracking.

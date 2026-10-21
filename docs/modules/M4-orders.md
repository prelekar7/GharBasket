# M4 — Orders & Checkout

**Status:** Draft v1; §3.1 (slots, box orders, add-ons, cancel, cut-off) frozen for S2; §3.2 (order issues) frozen on 2026-10-08; §3.3–§3.7 (live timeline, paying a failed order, tiffin orders, issue triage, facade additions) drafted 2026-10-09, frozen on approval
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
| US-5 | As a customer, I want fair handling of shortages so that I'm not charged for missing items. | At packing a whole short line may become a same-or-cheaper substitute, and every short step or price difference is refunded to the wallet at once (`mark_shortage`). |
| US-6 | As a customer, I want to cancel before cut-off for free so that plans can change. | Cancel allowed before `LOCKED`; refund not needed (no capture yet). |
| US-7 | As a hub manager, I want slot capacities so that we never overbook a route. | Slot booking fails when capacity is reached. |

## 3. API endpoints
| Method | Path | Permission / role | Idempotency | Notes |
|---|---|---|---|---|
| GET | `/v1/orders` | customer (own), hub staff (hub) | — | Filters: status, from, to; cursor |
| GET | `/v1/orders/{id}` | owner, hub staff | — | Includes lines and timeline; ETag |
| GET | `/v1/orders/{id}/events` | owner, hub staff | — | SSE, or JSON for polling clients (§3.3) |
| POST | `/v1/orders` | customer | Required | Direct tiffin order (§3.5, S6); add-ons stay on the box |
| POST | `/v1/orders/{id}/pay` | owner | Required | Pay a `payment_failed` order from the wallet during the grace period (§3.4) |
| GET | `/v1/tiffin-menus?date=&meal=` | Authenticated | — | Menus to book (§3.5; data owned by M2) |
| GET | `/v1/admin/order-issues[/{id}]` | hub_manager (hub), admin | — | Triage queue and detail with photo links (§3.6) |
| POST | `/v1/admin/order-issues/{id}/resolve` | hub_manager (hub), admin | Required | Credit or reject (§3.6) |
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
  "unit_price_paise": 2000, "total_paise": 4000,                   priced when the line was made, or the
                                                                   packing substitute's price;
                                                                   total = (qty − shortage_qty) × unit price
  "reason_code": "price_swap" | "in_season" | … | null, "reason_params": { } | null,
  "substituted_from": { "product_id", "slug", "name" } | null,     set by packing (M6); product, step and
                                                                   price are then the substitute's
  "shortage_qty": 0 }                                              steps short at packing, refunded

OrderOut : {
  "id", "kind": "box" | "addon" | "tiffin", "status",
  "cycle_id": "<uuid>" | null, "household_id", "hub_id", "society_id",
  "delivery_date": "2026-11-03", "slot": { "id", "label", "start", "end" },
  "cutoff_at": "2026-11-02T15:30:00Z",                           21:00 IST the day before
  "lines": [ OrderLine ],
  "subtotal_paise": 69000, "fees_paise": 0, "discount_paise": 0, "total_paise": 69000,
                                                       Σ line totals; after packing, what was paid
                                                       net of shortage refunds
  "version": 3, "created_at", "locked_at": "…" | null,
  "timeline": [ { "status": "approved", "at": "…",
                 "actor": "customer" | "system" | "hub" | "packer" | "rider" | "captain" | "kitchen" } ] }
                                     the kind of actor only, never a name or user id (ADR-0026 §2)

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
- Fulfilment events (`orders.packed`, `orders.dispatched`, `orders.at_society`, `orders.delivered`, `orders.delivery_failed`) are published when M6/M7/M8 call `orders.api.transition`: `{ order_id, household_id, hub_id, society_id, delivery_date, slot_id, total_paise, kind: "box" | "addon" | "tiffin", actor, …the caller's payload }`.
- `orders.shortage_substituted { order_id, household_id, product_id, shortage_qty, substitute_product_id | null, refund_paise, total_paise }` is published by `mark_shortage` (`product_id` is the original; `refund_paise` may be 0 for a same-price substitute).
- **Every event published by a status change** (the three above and the fulfilment events) carries `kind` and `actor` (the kind of actor, never the person's id), so a consumer that cares about tiffins or boxes filters on `kind` (ADR-0026 §3).

**State machine per order kind** (ADR-0026 §2, §3; `orders/state.py`). M4 checks only that the move exists for the order's kind and that this kind of actor may make it; whether *this* packer, rider, captain or kitchen may act on *this* order is checked by the calling module (M6: `packer:<hub>`; M7: the active captain of the order's society through `community.api`, or the rider who owns the route; M8: the kitchen holding the assignment). `hub` is a hub manager acting from the console, an override for every fulfilment move. A move that is not in the table for the order's kind, or by an actor not listed, is 409 `orders.illegal_transition` with `kind`, `from`, `to` (and `actor` when only the actor is wrong).

| From → to | Box / add-on orders | Tiffin orders |
|---|---|---|
| approved → locked / payment_failed | system | system |
| payment_failed → locked | system, customer (`POST /v1/orders/{id}/pay`, §3.4) | system, customer |
| approved → cancelled; payment_failed → cancelled | customer, system | customer, system |
| locked → picking | packer, hub | kitchen (preparing), hub |
| picking → packed | packer, hub | kitchen (ready), hub |
| locked → packed | hub (fast path) | — |
| packed → dispatched | rider (route start), hub | rider (pickup scan at the kitchen), hub |
| redelivery → dispatched | rider, hub | — |
| dispatched → at_society | captain (handover scan), rider (only where the society has no active captain; M7 checks), hub | — |
| at_society → delivered | captain, rider (no captain), hub | — |
| at_society → undelivered | captain, rider (no captain), system (`hold_expiry`), hub | — |
| dispatched → delivered / undelivered | — | rider, captain, hub |
| undelivered → redelivery | customer, system (the free redelivery, `schedule_redeliveries`), hub | — |
| undelivered → refunded | customer, system | system (at once; a lunch is never redelivered) |
| redelivery → refunded | system, hub (a redelivery no route picked up; [ADR-0028](../adr/0028-m7-order-moves-by-hub-managers-and-orphan-redeliveries.md), accepted) | — |

Tiffins therefore never reach `at_society` or `redelivery`, and boxes and add-ons never take `dispatched → delivered`. The tiffin statuses read as handbook 06 §6.3: `approved` = scheduled, `locked` = locked (the kitchen assignment is M8 data), `picking` = preparing, `packed` = ready, `dispatched` = picked up; the apps label them by kind.

**For other modules** (`orders/api.py`, no HTTP):
- `create_from_cycle(session, cycle: ApprovedCycle) -> Order` — M3 calls it in the approve transaction (so the customer's answer carries `order_id` and a full slot is refused on the spot; `subscriptions.box_approved` is published alongside for M11 and analytics); idempotent per `cycle_id`. **When the order already exists and is still `approved`, a higher `version` replaces its box lines and totals (add-ons are kept)**: an edit after approval re-publishes the event. `ApprovedCycle` carries `cycle_id, household_id, hub_id, society_id, delivery_date, slot_id, lines [ { product_id, qty, unit_price_paise, reason_code, reason_params } ], version`. Box line prices are frozen here; `total_paise` = Σ qty × unit price.
- `cancel_from_cycle(session, cycle_id, reason="skipped")` — a skip before the cut-off cancels the order.
- `transition(session, order_id, to, *, actor, actor_user_id=None, payload=None)` — M6/M7/M8 (and the cut-off jobs) move statuses along the order kind's state machine above; `actor` is the kind of actor (`customer | system | hub | packer | rider | captain | kitchen`) and `actor_user_id` the person who made the move (none for `system`), recorded on the `order_events` row and never shown to customers or exported; every illegal transition raises `orders.illegal_transition`; each call writes `order_events` and publishes the matching event with `kind` and `actor`.
- `orders_for_delivery(session, hub_id, delivery_date, statuses=("locked",), kinds=None) -> list[OrderSummary]` — M6 procurement and packing, M7 routing (society, slot, lines, crates later); `kinds` narrows the list (say `("tiffin",)` for M8, `("box", "addon")` for hub packing), every kind by default.
- `mark_shortage(session, order_id, product_id, shortage_qty, substitute_product_id | None) -> OrderLine` — M6 packing (ADR-0026 §6), while the order is `locked`, `picking` or `packed` (else `orders.not_editable`); `shortage_qty` is 1 to the line's `qty`. A partial shortage is refunded (`shortage_qty` × the line's unit price), never partly substituted. A whole-line shortage may instead name one substitute from `catalog.api.substitute_ids` that is not already on the order and costs no more per step at the hub today: the line takes the substitute's product, step and price (same `qty`, planner reason cleared), `substituted_from` keeps the original, and the difference is refunded; otherwise `orders.substitute_invalid` with `reason` (`partial`, `on_order`, `not_a_substitute`, `no_price`, `pricier`), or `orders.product_unknown`. The refund goes through `billing.api.refund_to_wallet(owner, order_id, amount, reason=f"short_{product_id.hex}")` in the caller's transaction (key `refund:<order>:short_<hex>`), and the order's totals are recomputed from its lines. One shortage per line: the same call again returns the line and moves no money; a different one is `orders.shortage_recorded`. If the wallet cap refuses the refund, `billing.wallet_limit` is raised as is and nothing is recorded (a shortage is never kept without its refund). A household that chose "never substitute, refund" (M1 §3.4, read through `identity.api.shortage_preferences` when the shortage is recorded) is refused any substitute with `reason: "refund_preferred"`. `orders.shortage_substituted` carries `household_id` (added 2026-10-09 so M11 knows whom to tell).

**Error codes**

| Status | Code | When |
|---|---|---|
| 404 | `orders.not_found` | Unknown order, or someone else's |
| 409 | `orders.cutoff_passed` | After 21:00 IST on the day before delivery |
| 409 | `orders.not_editable` | Add-ons on an order that is not `approved`; a shortage on an order that is not `locked`, `picking` or `packed` |
| 409 | `orders.shortage_recorded` | A different shortage for a line that already has one (facade) |
| 409 | `orders.already_locked` | Cancelling after the cut-off job locked it |
| 409 | `orders.illegal_transition` | A status change the state machine forbids (facade) |
| 409 | `orders.slot_full` | The slot's capacity for that date is used up (at order creation) |
| 412 | `request.version_mismatch` | `If-Match` is not the current version |
| 422 | `orders.product_unknown`, `orders.qty_invalid`, `orders.no_price` | Add-on items; `product_unknown` and `qty_invalid` also for shortages |
| 422 | `orders.substitute_invalid` | A packing substitute that may not replace the line, with `reason` (facade) |
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

A `damaged` issue without a photo is still accepted (201) and is never rejected for that reason: it stays `open` with `open_reason: "photo_needed"` for console triage. M4 calls `billing.api.refund_to_wallet(session, owner_user_id, order_id, amount_paise, reason=f"issue_{issue_id.hex}")` (the existing facade, exactly once by its key `refund:<order>:issue_<hex>`), sets `status: credited`, `resolution.by: auto` and stores `refund_entry_id`. The rule credits the whole issue or nothing. If the wallet cap refuses the refund (`billing.wallet_limit`), the issue stays open (`open_reason: "wallet_limit"`). Over the ₹50 allowance it is `over_auto_credit_limit`; over what is left of the order's paid total (net of shortage refunds at packing) after earlier issue credits on it, `over_order_total`; other kinds are `triage`. All credits on one order, automatic or by the hub, never add up to more than was paid for it. Everything that stays `open` goes to triage: the M12 console calls `orders.api.resolve_issue(session, issue_id, outcome, credit_paise, actor, note)`, which refunds through the same facade (credit ≤ `amount_paise`, or any amount for `late`/`other` up to the order total, and never more than is left of the order's paid total; above that, `orders.issue_credit_invalid` with `max_paise`) or rejects. Photo triage with an LLM and the trust-and-safety model (handbook 08 M-J) come later and only change who resolves.

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

### 3.3 Live order timeline contract (drafted 2026-10-09, frozen on approval)
This is the SSE stream named in §3 and handbook 10. The mobile order detail (the live tracking card) and the console's orders board build against it. It is not built yet, and no mock calls it: the mobile `mocks/orders.js` puts a timeline inline as `events[{ to_status, at }]`. Same sign-in and review rules as §3.1.

```
TimelineEntry : { "id": "<uuidv7>",                 the order_events row; also the SSE event id
                  "status": "dispatched", "at": "…",
                  "actor": "customer" | "system" | "hub" | "packer" | "rider" | "captain" | "kitchen" }
                                                    (ADR-0026 §2; never a name or user id)

GET /v1/orders/{id}/events      owner or hub staff of the order's hub
  Accept: application/json  → 200 { "items": [TimelineEntry], "status", "version", "eta": null }
                              oldest first; for clients that poll (the S2 behaviour, every 30 s)
  Accept: text/event-stream → 200, Cache-Control: no-store, X-Accel-Buffering: no
      event: snapshot   data { "order_id", "status", "version", "timeline": [TimelineEntry], "eta": null }
      event: status     data TimelineEntry & { "version" }        one per new order_events row
      event: eta        data { "eta_at": "…", "stops_before": 3 }  (arrives with M7 live tracking)
      event: end        data { "status" }                         the order reached a final status
      : ping                                                       a comment every 20 s
  Reconnect with Last-Event-ID: <TimelineEntry.id> → rows after it are sent before live ones, and
  no snapshot is sent again
  404 "orders.not_found" · 429 "orders.too_many_streams" (more than 3 open streams per user)
```

**Rules**
- **How it is served.** The stream reads `orders.order_events` for that order every 3 seconds; there is no pub/sub in v1. On the free tier this is cheaper than a Valkey subscriber per connection, and it is enough at demo scale. The stream closes itself after 15 minutes, or at once with `end` when the order is `delivered`, `cancelled` or `refunded`; the client reconnects while the screen is open.
- **Who may stream.** The same rules as `GET /v1/orders/{id}`: a customer streams only their household's orders, hub staff only their hub's orders, with `X-Active-Role`. A captain or rider follows their own route through M7's `/v1/routes/{id}/live`, not this stream.
- **Clients.**
  - The browser `EventSource` cannot send a Bearer header. The console uses cookies, so `EventSource` works there.
  - The mobile app (Bearer on native) reads the stream with `fetch` and a stream reader (the `X-GB-Client` CSRF rule does not apply to a GET).
  - Any client that cannot stream polls the JSON form.
- **`eta`.** It stays `null` until M7 publishes route progress; M7's contract defines how it is computed.

| Mock today (`mocks/orders.js`) | Contract |
|---|---|
| `events: [{ to_status: "AT_SOCIETY", at }]` on list items | `GET /v1/orders` stays without the timeline (§3.1); the current order's card calls `GET /v1/orders/{id}` (`timeline`) or this stream. `status` values are lower case (`at_society`) |
| `eta_by: "10:00"` | `eta.eta_at` from the stream, `null` until M7 |
| Statuses `ASSIGNED`, `PREPARING`, `READY`, `PICKED_UP` for tiffins | A tiffin uses the box statuses (`locked`, `picking`, `packed`, `dispatched`, ADR-0026 §3); the app labels them by `kind` |

### 3.4 Paying a failed order contract (drafted 2026-10-09, frozen on approval)
The Week screen's `payment_failed` state ("Top up ₹280", then the box is paid) builds against this. Today it calls `POST /v1/cycles/{id}/pay` in `mocks/subscriptions.js` (`feat/mobile-week-states`). The money and the order are M4's, so the action is on the order. M3 shows the amount due on the cycle (M3 §3.3).

```
POST /v1/orders/{id}/pay        owner; Idempotency-Key required (it moves money)
  no body. While the order is payment_failed and before grace_until (T-1 23:00 IST):
  billing.api.debit_for_order(owner, order_id, total_paise, delivery_date), the same call and key
  as the cut-off (box:<order id>), so it can never debit twice
  200 OrderOut (status locked, timeline row actor "customer"); publishes orders.locked as the
      cut-off does. An order that is already locked (the top-up consumer got there first) also
      answers 200 with the order unchanged
  409 "billing.insufficient_balance"  { balance_paise, needed_paise }; the order stays payment_failed
  409 "orders.not_payable"            the order is approved (not yet due), cancelled, or any other
                                      status apart from payment_failed and locked
  409 "orders.payment_grace_over"     after grace_until (payment_grace_expiry cancels it at 23:00)

POST /v1/orders/{id}/cancel     (§3.1) also accepted for a payment_failed order until grace_until:
                                → cancelled, reason "customer" (the state machine already allows it);
                                M3's skip of a payment_failed cycle calls this through orders.api
```

| Mock today | Contract |
|---|---|
| `POST /v1/cycles/{id}/pay` with `If-Match` → full Cycle | `POST /v1/orders/{order_id}/pay` (the cycle carries `payment_due.order_id`, M3 §3.3) → `OrderOut`; then refetch the cycle |
| 402 `billing.insufficient_balance` | 409 `billing.insufficient_balance` with `balance_paise` and `needed_paise` |
| 409 `orders.invalid_transition` | 409 `orders.not_payable` |
| The top-up's `onPaid` calls pay | Keep it: after the top-up is `paid`, `pay` answers 200 whether it or the `billing.wallet_topped_up` consumer locked the order |

### 3.5 Direct tiffin orders contract (drafted 2026-10-09, frozen on approval)
`POST /v1/orders` (§3, "P3") books a home kitchen's tiffin for one meal. The Week day sheet's "Book" button (`useAddTiffin`, `mocks/orders.js` on `feat/mobile-week-day`) builds against this. It ships with M8's kitchen menus in S6. **Add-ons never use this endpoint**: they are lines on the box, through M3's line edits before approval and `POST /v1/orders/{id}/addons` after it (M3 §11).

```
GET /v1/tiffin-menus?date=&meal=      Authenticated (M2 owns catalog.tiffin_menus; contract here
                                      because only this flow reads it)
  200 { "items": [ { "id", "date", "meal": "lunch" | "dinner", "hub_id",
                     "dishes": [ { "recipe_id", "name" } ], "price_paise": 11000,   per portion
                     "kitchen": { "partner_id", "display_name", "photo_url": null },  the menu's
                                                          cook; photo_url null for now (M8 §3.5)
                     "order_by": "…" } ] }               the cut-off of that date (T-1 21:00 IST)
                                      approved menus of the caller's hub, date tomorrow … +7

POST /v1/orders                       customer; Idempotency-Key required (one per date and meal)
  request : { "kind": "tiffin", "menu_id": "<uuid>", "portions": 2 }      portions 1 to 12,
                                      default the household size
  201 : OrderOut & { "tiffin": { "menu_id", "meal", "dishes": [ { "recipe_id", "name" } ] } }
        kind "tiffin", status "approved", one line (the menu's tiffin product × portions at the
        menu price), slot = the hub's active tiffin slot for that meal, cutoff_at as for boxes,
        "kitchen": null until M8 assigns one (ADR-0026 §3)
  publishes orders.tiffin_booked { order_id, household_id, delivery_date, meal, portions }
```

**Rules**
- **Paid like a box.** The tiffin is paid at the 21:00 cut-off with the same `cutoff_lock` handshake (§3.1); it is `approved` until then and can be cancelled for free (`POST /v1/orders/{id}/cancel`). One tiffin per household per date and meal: a second one gets 409 `orders.tiffin_already_booked` with `order_id`.
- **The kitchen.** A tiffin menu is one kitchen partner's offer for a date and meal (`catalog.tiffin_menus`, M2 §4, shown to her hub's customers once approved), so the menu names the cook the customer saw. M4 copies it to the order as `preferred_partner_id` at booking. That cook is a preference, not a hard choice (owner, 2026-10-09): the fair assignment (M8 `assign_kitchens`, T-1 21:30, M8 §3.4) gives the order to her while she is eligible and has capacity left, and otherwise moves it to another eligible kitchen (one with the skills of the menu's tiffin product); the customer is not asked again. If no kitchen takes it by the meal's `ready_by` minus 2 hours, M8's `refund_unassigned_tiffins` calls `transition(…, "refunded", actor="system", payload={"reason": "no_kitchen"})`: the tiffin-only move `locked → refunded` of ADR-0027, which refunds the whole paid total at once through `billing.api.refund_to_wallet`, and M11 tells the customer (`billing.refund_issued`). The order shows the kitchen that cooks it after `set_kitchen` (ADR-0026 §3).
- **Slots.** Tiffin slots are `orders.slots` with `kind: tiffin`. They gain a `meal` column (`lunch`, `dinner`; null for box slots), so the server picks the slot; the client never sends one. A full slot is 409 `orders.slot_full`.
- **The Week screen.** M3 consumes `orders.tiffin_booked` and, while the cycle can still be edited, sets that meal to `tiffin` exactly as `PUT /v1/cycles/{id}/meals` with `set: "tiffin"` does (M3 §3.2: the meal's ingredients leave the box). A cancelled tiffin (`orders.cancelled` with `kind: tiffin`) sets the meal back to `planned`. After the cycle's cut-off the box does not change.
- **`kind: "addon"` (or `box`)** is 422 `orders.kind_unavailable`. Add-on-only orders are refused (owner, 2026-10-09): add-ons ride only on a box, so a household that skipped the week un-skips it first (M3 §3.3).

| Mock today | Contract |
|---|---|
| `{ kind: "tiffin", delivery_date, slot: "dinner", kitchen_id }` | `{ kind: "tiffin", menu_id, portions }`: the menu fixes date, meal, price and the cook |
| `cycle.meal_plan.days[].tiffin_offer { kitchen_id, slot, cook, dish, price_paise, order_by }` | `GET /v1/tiffin-menus?date=` (the cycle does not carry offers) |
| 409 `orders.already_booked` | 409 `orders.tiffin_already_booked` with `order_id` |
| 422 `orders.invalid_order` | 422 `orders.menu_unknown` (not approved, other hub, past `order_by`), `orders.kind_unavailable`, `orders.qty_invalid` (portions) |
| 201 `{ id, delivery_date, slot, kitchen_id }` | 201 `OrderOut` with `tiffin` |

**As built (tiffin menus).**
- `catalog.tiffin_menus` is M2's table (M2 §4); M2 reads it through `catalog.api.tiffin_menus(hub_id, first, last, meal)` (approved only) and `catalog.api.tiffin_menu(id)` (any status). M2 has no endpoints to write or approve menus yet (M2 lists only the `tiffin_menus:write` permission), so menus are written by seed or tests until that flow is specified.
- `GET /v1/tiffin-menus`: `date` is optional (default: every date from tomorrow to tomorrow + 6) and must lie in that window (else 422 `orders.date_out_of_range`); `meal` filters. Menus past their `order_by` are left out. A caller without a household gets 422 `orders.hub_required`. The kitchen's `display_name` comes from `partners.api.profile_card` (empty when the partner record is missing).

**As built (direct tiffin orders).**
- `POST /v1/orders` answers 422 `orders.kind_unavailable` for any kind but `tiffin`; a missing, unknown, unapproved, other hub's or closed menu is 422 `orders.menu_unknown`; a caller without a household is 422 `orders.hub_required`; a hub without an active tiffin slot for the meal is 422 `orders.slot_invalid`. One tiffin per household, date and meal is checked under an advisory lock on the household.
- The tiffin's line has `kind: "box"` (the order's main line; `OrderLine.kind` stays `box | addon`). Default portions are the household size, capped at 12.
- Slots: `POST /v1/admin/slots` and `Slot` gain `meal` (required for `kind: tiffin`, absent for `box`, else 422 `orders.slot_invalid`). The migration sets the meal of existing tiffin slots from their start time (before 16:00: lunch).
- `orders.tiffin_booked` is published. M3 consumes it (`subscriptions.service.mark_tiffin_meal`): the week whose Monday is within 6 days before the tiffin's date, while editable, gets that meal set to `tiffin` exactly as `PUT /v1/cycles/{id}/meals` does; a cancelled tiffin (`orders.cancelled`, `kind: tiffin`, its meal from `orders.api.slot_ref(…).meal`) sets it back to `planned` if the customer had not changed it since. Past the cut-off or over the Budget Lock the box stays as it is. M3's other order consumers act only on box orders.

**As built (the kitchen, and the no-kitchen refund; facades for M8).**
- `orders.api.set_kitchen(session, order_id, partner_id | None) -> Order` records the kitchen on a tiffin (`orders.orders.kitchen_partner_id`) and clears it with `None`; any other kind is 409 `orders.not_editable`; a change bumps `version`, the same kitchen again changes nothing. `OrderOut` gains `"kitchen": { "partner_id" } | null` (ADR-0026 §3).
- `orders_for_delivery` returns each order's `preferred_partner_id` on the `Order` it carries (`summary.order.preferred_partner_id`), with its lines and `slot_id`.
- The tiffin move `locked → refunded` (actor `system`) is in the state table (ADR-0027 §1). **Every** move to `refunded` now refunds through `billing.api.refund_to_wallet`: `payload.refund_paise` (default: the paid total less the order's issue credits), keyed `refund:<order>:<payload.reason>` (default reason `undelivered`); the order event's payload records `reason` and `refund_paise`. `billing.wallet_limit` is raised as is and nothing moves.

### 3.6 Issue triage contract (drafted 2026-10-09, frozen on approval)
This is the console's queue for issues that stay `open` (§3.2), resolved through the existing `orders.api.resolve_issue`. M4 serves the endpoints because it owns the issues; the page sits in the console's Hub section. No console page or mock exists yet. Permission `order_issues:resolve` (§7).

```
IssueTriage : IssueOut (§3.2) & {
  "household_id", "hub_id", "society_id", "delivery_date",
  "order_total_paise": 69000,
  "creditable_paise": 52000,        what is left of the order's paid total after shortage
                                    refunds and earlier credits
  "max_credit_paise": 4000,         what resolve_issue would accept now (amount_paise, or the
                                    order total for late/other, capped by creditable_paise)
  "auto_credited_7d_paise": 1500,   the household's automatic credits in the last 7 days
  "callback_due_at": "…" | null }   created_at + callback_within_minutes, while open

GET  /v1/admin/order-issues?hub_id=&status=open&kind=&cursor=&limit=
     Page[IssueTriage], oldest open first (the callback promise); hub_id must be in the caller's
     scope (admins may leave it out)
GET  /v1/admin/order-issues/{id}
     200 IssueTriage & { "photos": [ { "url": "<5-minute signed GET>", "expires_at" } ],
                         "contact": { "name", "phone_e164" } }
     each photo link minted and each contact read is audit-logged; contact comes from
     identity.api.contact_for_household (the hub calls the customer back)
POST /v1/admin/order-issues/{id}/resolve      Idempotency-Key required (it may move money)
  request : { "outcome": "credit" | "reject", "credit_paise": 4000,      credit only
              "note": "…" }                                              required for reject; ≤ 500
  200 IssueTriage                    resolution { by: "hub", note, at }; publishes orders.issue_resolved
  409 "orders.issue_not_open" · 422 "orders.issue_credit_invalid" { max_paise }
  422 "billing.wallet_limit" { room_paise }   the issue stays open; try a smaller credit
  404 "orders.issue_not_found" · 403 "auth.forbidden" (another hub)
```

### 3.7 Facade additions for the leftovers (drafted 2026-10-09)
- `has_open_orders(session, household_id) -> bool` and `has_open_issues(session, household_id) -> bool`: preconditions of M1's purge (M1 §3.5).
- `anonymise_for_user(session, user_id, household_id) -> dict[str, int]` (M1 §3.5):
  - deletes the household's issue photos (`platform.files`) and clears `photo_keys` and `note`;
  - sets `order_events.actor_user_id` to null where it is this user;
  - keeps orders, lines, events and issue amounts.
- `mark_shortage` refuses a substitute for a household whose M1 `shortage_preference` is `refund`: 422 `orders.substitute_invalid` with `reason: "refund_preferred"` (M1 §3.4).
- **Fulfilment events gain `actor` and `actor_user_id`** (internal only, never shown to customers), so that M8 can attribute earnings without reading M4's tables (M8 §3.6, M5 §3.5). This is in addition to `kind` (ADR-0026 §3).
- `orders_for_delivery` returns, for tiffins, the tiffin lines, `slot_id` and `preferred_partner_id` (M8 §3.4).
- **From [ADR-0027](../adr/0027-operations-contract-amendments.md)** (made in the M7 and M8 PRs that need them, reviewed by both owners): the tiffin move `locked → refunded` (actor `system`, reason `no_kitchen`); `system` on `undelivered → redelivery`; `dispatched → undelivered` for box and add-on orders (`rider`, `system`, `hub`); `redelivery → refunded` (`system`, `hub`: ADR-0028); `rider` on the last-50-m moves of a stop a hub manager switched to rider mode; `transition(…, "refunded")` takes `payload.refund_paise` and `payload.reason`; `orders.delivery_failed` carries `reason`; and `carry_over(session, order_id, lines)` for a box missed twice, with its 14-day wait (M7 §3.4, §10). The carry-over is specified in §3.8.

### 3.8 Carry-over contract (drafted 2026-10-20, frozen on approval)
After a box's second miss, M7 refunds its perishable lines minus ₹20, and its non-perishable lines ride free on the household's next box ([ADR-0027](../adr/0027-operations-contract-amendments.md) §2, §6; M7 §3.4). M6 keeps the goods on a shelf and packs them (M6 §3.5). This section is M4's side.

- **Carried items are not order lines.** They live in M4's own table, `orders.carry_overs`, one row per (missed order, product). An order shows them as `carried`, apart from `lines`. So a carried item never collides with a paid line of the same product (the line key is `(order_id, product_id)`), and it never counts towards totals, debits, shortages, add-on edits, a re-approval's box lines, procurement demand or `sold_lines`.
- **`carry_over(session, order_id, lines: [ { product_id, steps, paid_paise } ]) -> list[CarryOver]`.** M7's `schedule_redeliveries` calls it in the second-miss transaction, after `undelivered → refunded`. M7 keeps publishing `logistics.carry_over_pending` as its record.
  - The order must be a `refunded` box or add-on order (else `ValueError`: a programming error, not a problem response).
  - Each line becomes a `pending` row with `steps`, the order line's `step_qty` and `step_unit`, `paid_paise` (what those steps cost), `missed_on` (the order's delivery date) and `expires_on` = `missed_on` + 14 days.
  - It is idempotent per (order, product): a second call returns the existing rows and changes nothing.
  - It then attaches at once (below) if the household already has a box that fits.
- **Attaching.** A pending row is attached to the household's next box or add-on order whose delivery date is after `missed_on` and that is `locked` (paid, not yet picking). The row gets `to_order_id` and becomes `attached`, and `orders.carry_over_attached` is published once per target order.
  - Attaching happens in `carry_over` (a box already locked), and whenever a box or add-on order of the household moves to `locked`: `cutoff_lock`, a top-up during the grace period, or `POST /v1/orders/{id}/pay`. All rows still pending are attached, oldest `missed_on` first.
  - An `approved` order is never used: a skip or a re-approval could drop the items, and the money for the box is not yet taken.
- **After attaching**, the row follows its target order:
  - the target moves to `packed` → `packed` (in its bag; while the target is `locked` or `picking` the hub manager can still report a kept item lost, M6 §3.5);
  - the target is `delivered` → `delivered` (final);
  - the target is missed twice and moves to `refunded` → the row goes back to `pending`. `to_order_id` is cleared, `returned_in_order_id` is set to that order, and `expires_on` is kept. Its first redelivery changes nothing. This is any move of the target to `refunded` (the second miss, a triage refund, `redelivery → refunded`). If the household has another box or add-on order that is already `locked` and delivers after `missed_on`, the rows attach to it at once.
- **`carried_lines(session, order_id) -> list[CarriedLine]`** (M6's returned-bag scan, M6 §3.5): the items that are in that order's bag. These are the rows carried *from* it, plus the rows that rode *in* it and came back (`returned_in_order_id`). Each has `carry_id`, `product_id`, `steps`, `step_qty` and `step_unit`.
- **`OrderSummary.carried`** (from `orders_for_delivery`, `get` and `summaries`): the rows attached to that order, each with `carry_id`, `carried_from_order_id`, `product_id`, `steps`, `step_qty` and `step_unit`. M6 copies them into the packing task as `"carried": true` lines.
- **The customer's order** (`GET /v1/orders/{id}`, additive) gains `carried: [ { product_id, name, steps, step_qty, step_unit, carried_from_order_id } ]`, so the app can show "Kept from your box of 14 Oct: 1 kg toor dal". A missed order gains `carry_over: { status, expires_on, to_order_id } | null` (null when nothing was carried). The status is that of its oldest unsettled row: `pending` (waiting for the next box), `attached`, `packed`, `delivered` or `refunded`.
- **Expiry job `expire_carry_overs`** (daily at 02:40 IST, the run's `scheduled_for` gives the day).
  - Every `pending` row whose `expires_on` is before that day is refunded at its `paid_paise`, **without** the ₹20 fee: `billing.api.refund_to_wallet(owner, from_order_id, paid_paise, reason=f"carry_{product_id.hex}")`, in a savepoint per row. The refund is keyed per row, so a repeat run moves no money.
  - The row becomes `refunded` with `settled_at`, and `orders.carry_over_expired` is published once per missed order with the rows refunded in that run.
  - An `attached` or `packed` row is not refunded: it is already on a paid box, and rides with it.
  - A refund the wallet cannot take (`billing.wallet_limit`) leaves the row `pending`; the next run tries again, and the job counts it as `wallet_limit`. M5's per-order refund to the payment source (a follow-up) will take these.
  - A row whose household has no owner left (a deleted account) is skipped and counted as `no_owner`; the others are refunded all the same. Each refund is at most what is left to refund on the missed order (`refundable_paise`, so earlier credits count), and 0 settles the row.
  - It returns `{ "date", "refunded", "refunded_paise", "wallet_limit", "no_owner" }`.
- **A lost kept item** (the open question of M6 §10). `refund_carry(session, carry_id, actor_user_id) -> CarryOver` refunds one `pending` or `attached` row at once, at its `paid_paise` without the fee, with the same per-row key. M6's hub-manager action `POST /v1/hubs/{id}/kept-items/{item_id}/lost` calls it (M6 §3.5, audited there). A `packed` or `delivered` row is 409 `orders.carry_not_refundable` with `current_status` (RFC 9457 keeps `status` for the HTTP code); an already `refunded` row returns as is. Rows become `packed` only when the target reaches `packed`, so while the target is being picked M6 also takes the item off the task.
- **Money.** `refunded_paise` and `refundable_paise` count carry refunds of the missed order. So, with its perishables refunded and its carried items refunded or delivered, an order never returns more than was paid.
- **Account deletion.** `has_open_orders` is true while the household has a `pending`, `attached` or `packed` row, so M1's purge waits for the carry-over to settle: at most 14 days, or less if the hub refunds it. `export_for_user` lists the rows.
- **Events.**
  - `orders.carry_over_attached { order_id, household_id, hub_id, lines: [ { carry_id, carried_from_order_id, product_id, steps } ] }`, for M11's "your kept items ride on Thursday's box" (a later template).
  - `orders.carry_over_expired { order_id, household_id, hub_id, lines: [ { carry_id, product_id, steps, paid_paise } ], reason: "carry_over_expired" }`, for M6 (release the kept items, M6 §3.5) and M11.
- **No backfill.** The `logistics.carry_over_pending` events published before this ships were dev and test data. The outbox does not replay an event that had no consumer.

## 4. Tables owned
| Table | Key columns |
|---|---|
| `orders.orders` | id, household_id, hub_id, society_id, kind (box, tiffin, addon), cycle_id (unique among orders that are not cancelled), delivery_date, slot_id, cutoff_at, status, subtotal_paise, fees_paise, discount_paise, total_paise, version, locked_at, data_source, created_at |
| `orders.order_lines` | order_id, product_id, kind (box, addon), qty, step_qty, step_unit, unit_price_paise, reason_code, reason_params jsonb, substituted_from, shortage_qty |
| `orders.order_events` | id, order_id, from_status, to_status, actor (customer, system, hub, packer, rider, captain, kitchen), actor_user_id (nullable; the person, never shown to customers), payload jsonb, at |
| `orders.slots` | id, hub_id, label, kind (box, tiffin), start_time, end_time, capacity, active |
| `orders.slot_bookings` | slot_id, date, order_id (unique); capacity is checked under a row lock on the slot |
| `orders.order_issues` | id, order_id, household_id, hub_id, kind, status (open, credited, rejected), open_reason, note, photo_keys text[], amount_paise, credit_paise, resolved_by (auto, hub), resolver_user_id, resolution_note, resolved_at, refund_entry_id, created_by, created_at |
| `orders.order_issue_items` | issue_id, product_id, qty (steps), unit_price_paise (the line's paid price) |
| `orders.carry_overs` (§3.8) | id, from_order_id, household_id, hub_id, society_id, product_id, steps, step_qty, step_unit, paid_paise, missed_on, expires_on, status (pending, attached, packed, delivered, refunded), to_order_id, returned_in_order_id, refunded_by (expiry, hub), settled_at, created_at; unique (from_order_id, product_id); index (household_id, status) |

Changes from §3.3–§3.7: `orders.slots.meal` (lunch, dinner; tiffin slots only); `orders.orders.tiffin_menu_id`, `preferred_partner_id` (the menu's kitchen) and `portions` (tiffin only); `orders.orders.cycle_id` stays unique only among orders that are not `cancelled` (a partial unique index), so a cycle that is skipped and then un-skipped (M3 §3.3) can get a new order.

## 5. Events
**Published:** `orders.locked`, `orders.payment_failed`, `orders.cancelled` (gains `kind`), `orders.tiffin_booked` (§3.5), `orders.shortage_substituted`, `orders.packed`, `orders.dispatched`, `orders.at_society`, `orders.delivered`, `orders.delivery_failed`, `orders.issue_reported`, `orders.issue_resolved`, `orders.carry_over_attached` and `orders.carry_over_expired` (§3.8) (fulfilment statuses are applied by M6/M7 through `orders.api`, and M4 publishes the events). Payloads in §3.1.
**Consumed:** `subscriptions.box_approved` (→ `create_from_cycle`), `subscriptions.box_skipped` (→ `cancel_from_cycle`), `billing.wallet_topped_up` (retry a failed debit in the grace period); `billing.payment_failed` (Autopay captures, pilot phase).

## 6. Jobs
| Job | Schedule | Notes |
|---|---|---|
| `cutoff_lock` | T-1 21:00 IST (cron-job.org → `POST /v1/internal/jobs/cutoff_lock`) | Advisory lock; batches of 200; `billing.api.debit_for_order` keyed by order id; see §3.1 |
| `payment_grace_expiry` | T-1 23:00 IST (cron-job.org) | `payment_failed` → `cancelled` |
| retry after top-up | Outbox consumer of `billing.wallet_topped_up` | Re-runs the debit for a `payment_failed` order inside the grace period |
| `expire_carry_overs` | Daily 02:40 IST (cron-job.org) | Refunds carried items still pending 14 days after the miss, without the fee (§3.8) |

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
- Unit: state machine transitions per order kind and actor (every move in the table allowed for exactly its actors, all others rejected).
- Property (hypothesis): random sequences of edits/cancels/locks never yield an order in two states or a negative total.
- Integration: cut-off idempotency (run twice → no double lock or debit).
- Load (k6): cut-off with 2,000 orders under 60 s.
- E2E: approve → lock → status stream to delivered.

- Leftovers (§3.3–§3.7): the stream sends a snapshot, then one `status` per new event, and `end` at a final status; Last-Event-ID replays only later rows; a fourth stream gets 429; JSON and SSE forms agree. `pay` debits once under replays and races with the top-up consumer (one `box:` entry), answers 200 for an already locked order, 409 after the grace period. Tiffin: one per date and meal, refused after `order_by`, paid at the cut-off, M3 marks the meal; `kind: addon` 422. Triage: hub scope enforced, credit above `max_credit_paise` 422, a replayed resolve moves money once, photo links and contact reads are audit-logged.
- Carry-over (§3.8): a second miss → `pending` rows, idempotent per (order, product); attached at the next lock (cut-off, grace top-up, pay) and at once to a box already locked, never to an `approved` one; `packed` when the target is packed, `delivered` at delivery; back to `pending` after the target's own second miss, with `carried_lines` of that order listing them; carried items never change totals, debits, procurement, `sold_lines` or shortages; the expiry job refunds the paid price without the fee once under repeats, skips attached rows and leaves a row the wallet cannot take; `refund_carry` refunds once and refuses a packed row; `refundable_paise` adds up; the purge waits while a row is open.
## 10. Dependencies
M3 (approved cycles via events), M2 (prices via `catalog.api`), M5 (debit/capture, shortage and issue refunds via `billing.api`), M0 (issue photos via `platform.files`), M6/M7/M8 call `orders.api` for fulfilment transitions (ADR-0026).

## 11. Open questions and decisions
- **Decided (S2 contract):** tiffin orders use their own slots (`kind: tiffin`); boxes never share them.
- ~~**Decided (S2 contract):** no delivery fee in S2 (`fees_paise` stays 0); the policy for non-unlocked societies is decided with M9 unlock thresholds in S9.~~ **Superseded (owner, 2026-10-09; built 2026-10-10):** a box order for a society with no `live_at` pays the hub's `pre_live_box_fee_paise` (M6, default ₹30) as `fees_paise`, set by `create_from_cycle` from `orders.api.box_fee(session, hub_id, society_id)` and dropped to 0 at the lock if the society went live meanwhile (never raised). `total_paise = subtotal_paise + fees_paise − discount_paise` is what the cut-off debits; shortage refunds and issue credits never return the fee. Add-on and tiffin orders pay none. The rules are M9 §3.6 and its M4 follow-up in §11.
- Direct orders (`POST /v1/orders`) and the SSE stream arrive in P3 with tiffins and live tracking.
- **Decided by the owner (2026-10-09):**
  - ~~Leftovers (§3.5): add-on-only orders outside a box (e.g. a week the household skipped): refuse them, or let an add-on-only order un-skip the week?~~ Decided: add-on-only orders are refused (`orders.kind_unavailable`); M4 does not depend on M3's delivery days.
  - ~~Leftovers (§3.5): may the customer choose the kitchen, or does the fair assignment (M10/M8) always decide?~~ Decided: the cook on a tiffin menu is a preference, not a hard choice; the assignment may move the order to another eligible kitchen when hers cannot take it (M8 §3.4).

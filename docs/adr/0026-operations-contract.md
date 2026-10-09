# ADR-0026 — Operations contract: units, order actors, tiffin states, captains and hubs

- **Status:** Accepted (2026-10-09, by the owner; open questions answered the same day)
- **Date:** 2026-10-09 (the captain decision was taken by the owner on 2026-10-08)
- **Decision ID:** D26

## Context
P3 (S4–S6) builds the operations side: M6 Hub Ops, M7 Logistics and M8 Partners, ending with the MS-B demo (procurement plan → receiving → packing → OR-Tools routes → captain handover → delivered, a tiffin day, and one partner completing KYC). Before the first M6 PR, the documents and the code disagree in five places that every one of these modules depends on:

1. **Units.** The code (`catalog.models.UNITS`, `orders.models.UNITS`) uses `g, kg, ml, l, piece, bunch, pack`; handbook 05 §5.2 and TDD §5.1 still say `kg, g, pc, bunch, ml, l`. Nothing says which unit procurement, inventory and waste use.
2. **Who moves an order.** `orders.models.ACTORS` is `customer | system | hub`, and every fulfilment transition in `orders/state.py` is allowed to `hub` only. Handbook 06 names Packer, Rider, Captain and Kitchen partner as triggers, and the timeline (`OrderOut.timeline[].actor`) is part of the frozen M4 contract.
3. **Tiffin states.** Handbook 06 §6.3 draws a tiffin machine (SCHEDULED, ASSIGNED, PREPARING, READY, PICKED_UP, FAILED) but tiffins share `orders.orders` (`kind = 'tiffin'`), and the code has no tiffin path at all. The same section already emits `orders.packed` for READY and `orders.dispatched` for PICKED_UP.
4. **Who says which captain serves a society.** M8 has `partners.partners.society_id`; M9 has `community.captains (society_id, partner_id, since)` and consumes `partners.verified` "for captain assignment"; M7 needs one answer at handover; M3's `CycleOut.captain` shows `{ name, flat }`, against M9's rule that a captain's flat is never returned. The owner decided on 2026-10-08 that M9 is the source of truth.
5. **Hubs do not exist.** `hub_ops` is an empty package. `identity.households.hub_id`, `community.societies.hub_id`, `catalog.price_lists.hub_id`, `orders.orders.hub_id`, `orders.slots.hub_id`, `subscriptions.weekly_cycles.hub_id` and the hub-scoped role grants are plain UUIDs with comments saying "no foreign key until M6 owns hubs". The seed uses one fixed id for the Kharghar hub (`SEED_HUB_ID = 0199a000-0000-7000-8000-00000000a001`, `tools/seed.py`).

Reading the M6/M7/M8 drafts against the code and the S-3 routing spike also shows smaller gaps that block those specs: the packing queue is "sorted by route stop" at 05:00 but routes are solved at 07:00 while vans leave at 06:30 in S-3; crate tracking, the captain's hold window and the vehicle type are open; `orders.api.mark_shortage` records a shortage but nobody refunds it, and it sets `substituted_from` without changing the line's product.

## Decision

### 1. One set of units
- **`g, kg, ml, l, piece, bunch, pack`** everywhere: catalog steps, order lines, recipes (which use the subset `g, ml, piece, bunch`), and operations. `pc` is retired; handbook 05 §5.2 and TDD §5.1 change `pc` → `piece` and add `pack`.
- **Customer-facing quantities stay in steps** (`qty` × `step { qty, unit }`, M2/M4 contracts), unchanged.
- **Operations quantities** (procurement lines, purchase orders, goods receipts, inventory lots, waste logs) are `NUMERIC(10,3)` in the product's **operations unit**: `kg` for a product sold in `g`/`kg`, `l` for `ml`/`l`, otherwise the step unit itself (`piece`, `bunch`, `pack`). Steps convert as `steps × step_qty` (÷ 1000 for `g`/`ml`). The M10 forecast (M10 §3.6) is in steps; M6 converts it.

### 2. M4 order actors
`ACTORS` becomes **`customer | system | hub | packer | rider | captain | kitchen`**. `hub` now means a hub manager acting from the console (an override that can make every fulfilment move); the others are the field roles. Who may make each move:

| From → to | Box / add-on orders | Tiffin orders (§3) |
|---|---|---|
| approved → locked / payment_failed; payment_failed → locked / cancelled | system (unchanged) | system |
| approved → cancelled | customer, system (unchanged) | customer, system |
| locked → picking | **packer**, hub | **kitchen** (preparing), hub |
| picking → packed | **packer**, hub | **kitchen** (ready), hub |
| locked → packed | hub (unchanged fast path) | — |
| packed → dispatched | **rider** (route start), hub | **rider** (pickup scan at the kitchen), hub |
| redelivery → dispatched | **rider**, hub | — |
| dispatched → at_society | **captain** (handover scan); **rider** only where the society has no active captain; hub | — |
| at_society → delivered | **captain**; **rider** where there is no captain; hub | — |
| at_society → undelivered | **captain**; rider where there is no captain; **system** (`hold_expiry`, §6); hub | — |
| dispatched → delivered / undelivered | — | **rider**, **captain**, hub |
| undelivered → redelivery | customer, hub (unchanged) | — |
| undelivered → refunded | customer, system (unchanged) | system (at once) |

- `orders.api.transition(session, order_id, to, *, actor, actor_user_id=None, payload=None)`: the facade **records** the actor kind and, for people, their user id (a new nullable `orders.order_events.actor_user_id`, never shown to customers); it checks only the state machine.
- **Permission checks stay in the calling module**: M6 checks that a packer holds `packer:<hub>` for the order's hub, M7 that a captain is the active captain of the order's society (through `community.api`) or that a rider owns the route, M8 that the kitchen holds the order's assignment. M4 trusts the actor its caller names.
- The customer timeline shows the actor kind only (`"actor": "captain"`), never a name or user id.

### 3. Tiffin orders reuse the order statuses and events
Tiffin orders stay in `orders.orders` with `kind = 'tiffin'` and take a **shorter path through the existing statuses**; no new statuses and no `orders.tiffin_*` events:

| Handbook 06 §6.3 | M4 status | Event | Moved by |
|---|---|---|---|
| SCHEDULED | `approved` | — | customer (`POST /v1/orders`, P3) or the tiffin subscription |
| LOCKED | `locked` | `orders.locked` | system (cut-off) |
| ASSIGNED | `locked` (the assignment is M8 data, `partners.assignments`) | `partners.assignment_created` | M8 `assign_kitchens` |
| PREPARING | `picking` | — | kitchen |
| READY | `packed` | `orders.packed` | kitchen |
| PICKED_UP | `dispatched` | `orders.dispatched` | rider |
| DELIVERED | `delivered` | `orders.delivered` | rider or captain |
| FAILED | `undelivered` | `orders.delivery_failed` | rider or captain |
| REFUNDED | `refunded` | `billing.refund_issued` | system, at once (a lunch is never redelivered) |

- The state machine becomes **per kind**: tiffins skip `at_society` and `redelivery`; boxes and add-ons never take `dispatched → delivered`.
- **Every fulfilment event payload gains `kind`** (`box | addon | tiffin`), so a consumer that cares filters on it. The apps label statuses by kind ("Being cooked" for a tiffin in `picking`, "Ready at the kitchen" for `packed`).
- For the "know your cook" card (M8 US-8), M8 records the kitchen on the order through a new facade call `orders.api.set_kitchen(session, order_id, partner_id | None)` (a nullable `orders.orders.kitchen_partner_id`, tiffin only); `OrderOut` gains `"kitchen": { "partner_id" } | null` and the app reads the public card from M8.

### 4. Society Captains: M9 is the source of truth
- **`community.captains` is the assignment**: which person serves which society, from when to when. Shape: `society_id, user_id, partner_id, status (active, ended), since, until`; **at most one active captain per society** (a partial unique index). M9 owns the endpoints to appoint and end an assignment (hub manager of the society's hub) and publishes `community.captain_assigned` / `community.captain_ended { society_id, user_id, partner_id }`.
- **M8 verifies the person, not the society**: application, KYC, training and `partners.verified { partner_id, user_id, kind, hub_id, requested_society_id }`. The society on a captain application (`scope_id`, as the mobile partner screens already send) is a request, not an assignment. M8 does **not** store `society_id` on `partners.partners`.
- **M9 appoints** on `partners.verified` (kind `captain`) when the requested society has no active captain; otherwise the request waits for a hub manager. On `partners.suspended` M9 ends the assignment.
- **M1 grants and revokes the `captain:<society>` role** on `community.captain_assigned` / `community.captain_ended` (instead of on `partners.verified`, which keeps granting the hub-scoped roles: `kitchen_partner`, `rider`, `packer`).
- **M7 and M3 read the captain through `community.api`** (`captain_for_society(society_id) -> CaptainRef { user_id, partner_id, display_name } | None`). The captain card everywhere is `{ display_name, photo_url }`: M3's `CycleOut.captain { name, flat }` changes to that shape (it is `null` today, so no client breaks).

### 5. Hubs: M6 owns `hub_ops.hubs`
- `hub_ops.hubs`: id, slug, name, address, location (point), service area (polygon, nullable until drawn), status (`active`, `paused`), the hub's day timings (receiving, packing, dispatch, surplus), the procurement buffer, `data_source`. Contract in M6 §3.1.
- **Hub ids elsewhere stay plain UUIDs, without cross-schema foreign keys.** M6 validates hub ids it receives; other modules accept a hub id only from a caller whose scope already names that hub (a hub manager's grant) or from an admin picking it from `GET /v1/hubs`. Hubs are never deleted, only paused. The code comments "no foreign key until M6 owns hubs" become "no foreign key: hub ids are checked through `hub_ops.api` (ADR-0026)", and handbook 05 §5.2 rule 5 drops `hubs` from the list of foreign-key targets.
- M6 publishes **`hub_ops.hub_created { hub_id, slug, name }`** and `hub_ops.hub_updated { hub_id, changed: [...] }`; nobody has to consume them yet.
- **The seed hub is the first hub.** `just seed` upserts `hub_ops.hubs` with `SEED_HUB_ID` (Kharghar), so every hub id already in seeded data and staging points at a real row. A test asserts that every hub id in the seed world exists in `hub_ops.hubs`.
- **The cut-off stays one global rule** (T-1 21:00 IST, M4 §3.1). A hub does not override it in v1; a per-hub cut-off would need an M4 change.

### 6. Smaller operations decisions
- **Crates are counted, not tracked.** Bags are grouped into crates per society and numbered "crate 2 of 5" on a printed label; there is no crate identity, crate QR or crate inventory. The captain confirms the crate count and the bag count at handover (plus OTP or photo), and return-leg pickups count empties per society. Each **bag** carries a QR with its order (`GB1:<order_id>`), which the captain scans at the door.
- **Packing does not wait for routes.** Packing tasks at T 05:00 are sorted by the published route's stop order when one exists, otherwise by slot start, society, tower and flat. Decided (2026-10-09): M7 solves routes at **T-1 23:15**, after `payment_grace_expiry` fixes the set of locked orders, so routes exist before packing and riders see them the night before; the 07:00 solve becomes a re-solve on demand.
- **Shortages at packing.** A whole-line shortage may be replaced by one substitute from `catalog.api.substitute_ids` (not already on the order, at a current price no higher than the line's unit price, any difference refunded); a partial shortage is refunded, never partly substituted. **M4's `mark_shortage` refunds** in the same transaction through `billing.api.refund_to_wallet(reason=f"short_{product_id.hex}")` and, on a substitution, sets the line's product to the substitute and `substituted_from` to the original. Households that chose "never substitute, refund" (an M1 preference, decided 2026-10-09) are always refunded instead.
- **Captain hold window (recommendation).** A bag the captain cannot hand over is held until **20:00 IST** the same day; the customer may collect it from her until then. At 20:30 the `hold_expiry` job (M7, actor `system`) moves what is still `at_society` to `undelivered`.
- **Vehicle (recommendation for the prototype).** Box routes use an electric cargo three-wheeler (400 kg, 24 crates, as assumed in spike S-3); tiffin routes use a two-wheeler with an insulated bag. These are `logistics.vehicles` parameters, to be confirmed against real vehicles and GPS traces before the pilot.
- **Bag labels show tower and flat.** Packers and captains need them; M1 adds `identity.api.delivery_labels(session, household_ids) -> {household_id: {tower_id, flat}}` (no names, no phones); M6 takes the tower's label from `community.api`.

## Alternatives considered
- **Units: keep `pc`, or store operations quantities in steps.** `pc` would mean a migration of every catalog and order check constraint for a spelling. Steps are meaningless to a vendor (a "250 g step" of onions), and buying is in kilograms.
- **Actors: one `hub` actor for everyone (status quo).** Simple, but the timeline cannot say who handed the bag over, commissions (captain per `at_society`, kitchen per `delivered`) cannot be attributed from the order history, and the handbook's triggers are lost. **Checking roles inside M4**: rejected; M4 would need to know packing assignments, routes and captains, which belong to M6, M7 and M9.
- **Tiffins: add `assigned`, `preparing`, `ready` (and `orders.tiffin_*` events).** Reads more naturally, but every consumer would need to learn the new values: the M11 event map, the issue windows (§3.2), the forecast's list of paid statuses, the `PAID` set, the mobile status labels and the M12 boards. Handbook 06 §6.3 already maps READY → `orders.packed` and PICKED_UP → `orders.dispatched`. `assigned` as an order status would also duplicate M8's assignment, which can be declined and re-solved without the order changing. **A separate `tiffin_orders` table**: rejected; slots, payment at cut-off, issues and the ledger are already shared.
- **Captains: M8 as the source (`partners.society_id`).** M8 is about verifying and paying a person; the society page, the unlock bar and the captain card are M9's, and M7 would then need M8 only for this. Having both store it is how they would drift. **The identity role grant as the only record**: it says who *may* act, not who is on duty, has no history and no single-captain rule.
- **Hubs: cross-schema foreign keys to `hub_ops.hubs`.** Handbook 05 allows foreign keys to stable ids, and `households.society_id` has one. Here the M6 migration would have to alter tables in five other schemas, every other module's tests would have to create a hub first, and hubs are never deleted, so the constraint protects against little. Rejected for hubs; existing foreign keys stay.
- **Crates by QR.** Accountable per crate, but needs crate inventory, printing and loss handling, which the prototype does not need to show the handover.
- **Packing waits for 07:00 routes.** The vans would leave late: packing takes about 2.5 h from 05:00.

## Consequences
- (+) One vocabulary for M6, M7 and M8 before their contracts freeze; the MS-B flow can be traced through `order_events` with real actors.
- (+) Tiffins need no new status, so M11, issues, forecasting and the apps work for them unchanged.
- (+) One place answers "who is the captain here", with history and a single active captain per society.
- (−) The state machine becomes per kind, and every fulfilment move needs a test per actor and kind.
- (−) Hub ids outside M6 are not enforced by the database; a wrong id can only come from an admin.
- (−) Several frozen contracts change, each in the PR that implements it, reviewed by both owners:
  - **M4** (in the M6 packing PR): `ACTORS`, the transition table per kind, `actor_user_id`, `kind` in fulfilment events, `mark_shortage` refunds and substitutes as in §6, a `kinds` filter on `orders_for_delivery`, the `system` actor on `at_society → undelivered`, `set_kitchen` and `kitchen` on `OrderOut` (with M8), `transition(…, "refunded")` refunding through `billing.api.refund_to_wallet(reason="undelivered")`.
  - **M8** (at its PR): drop `partners.society_id`; `applications.scope_id` is the requested society or hub; `partners.verified` carries `kind`, `hub_id`, `requested_society_id`; kitchens call `orders.api.transition(actor="kitchen")` and `set_kitchen`.
  - **M9** (captains part, before M7): the `community.captains` shape, the appoint/end endpoints, the two events, `captain_for_society`, consumers of `partners.verified` and `partners.suspended`.
  - **M7** (at its PR): captain via `community.api`, rider as last-50-m fallback, crate counts, hold window, vehicle kinds, route solve time.
  - **M1**: grant `captain:<society>` on M9's events; `delivery_labels`. **M3**: `CycleOut.captain` becomes `{ display_name, photo_url }`.
  - **Docs**: handbook 05 §5.2 (units, foreign-key rule) and §5.3 (schema names `hub_ops`, `notifications`), TDD §5.1 (units), handbook 06 §6.2/§6.3 (actors, the tiffin mapping), handbook 07 W4/W5 (packing order, crates by count).
- **Decided by the owner on 2026-10-09** (were open questions in the draft):
  1. **No-show after the hold window (M7):** one free redelivery the next morning (`undelivered → redelivery → dispatched`); if missed again, the perishables are refunded to the wallet minus a small fee set in M7's contract, and the box goes to the surplus cascade.
  2. **One active captain per society** (a backup captain may come later).
  3. **Routes are solved at T-1 23:15**, after `payment_grace_expiry`; packing at 05:00 follows route order, vans leave at 06:30; the 07:00 solve becomes a re-solve on demand.
  4. **Substitute by default, with an opt-out:** same-or-cheaper substitutes as in §6, any difference refunded; households can choose "never substitute, refund" in settings (an M1 household preference that M6 packing reads through `identity.api`).

## Related
[ADR-0001](0001-headline-usps-autopilot-and-society-captain.md) · [ADR-0019](0019-repository-layout-frontend-backend.md) · [ADR-0021](0021-jobs-without-a-broker.md) · [ADR-0025](0025-ml-runtime-spaces-actions-r2.md) · [M4 spec](../modules/M4-orders.md) · [M6 spec](../modules/M6-hub-ops.md) · [M7 spec](../modules/M7-logistics.md) · [M8 spec](../modules/M8-partners.md) · [M9 spec](../modules/M9-community.md) · Handbook [Chapter 05](../handbook/05-data-architecture.md), [Chapter 06](../handbook/06-domain-lifecycles.md), [Chapter 07](../handbook/07-workflows.md) · [Spike S-3](../spikes/S-3-routing.md)

# ADR-0027 — Operations contract amendments: unassigned tiffins, boxes missed twice, captain pay and M7's order moves

- **Status:** Accepted (2026-10-09, from the owner's answers on the M7 and M8 contracts; items 6 and 7 are defaults the owner may change)
- **Date:** 2026-10-09
- **Decision ID:** D27
- **Amends:** [ADR-0026](0026-operations-contract.md) §2 (the transition table), §3 (the tiffin table), its decided item 1 (a no-show after the hold window) and its note on commissions; ADR-0026's other decisions are unchanged

## Context
ADR-0026 fixed the operations vocabulary before M6, M7 and M8 froze their contracts. Writing the M7 Logistics contract ([M7 spec](../modules/M7-logistics.md), PR #143) and the M8 Partner Network contract ([M8 spec](../modules/M8-partners.md), PR #144) and the owner's answers of 9 October 2026 showed six places where ADR-0026 is silent or now says something else:

1. **A tiffin no kitchen accepts.** ADR-0026 §3 has no path for a locked tiffin that is still unassigned after the last assignment round. The customer has paid, and a lunch cannot wait for tomorrow.
2. **A box missed twice.** ADR-0026's decided item 1 sends the whole box to the surplus cascade after the second miss. The owner decided that only the perishables are refunded (minus the fee) and that the non-perishables (atta, oil, dals) go to the household with its next delivery.
3. **Where a held bag spends the night.** ADR-0026 §6 holds the bag with the captain until 20:00 but does not say where it goes before the free redelivery the next morning.
4. **When a captain is paid.** ADR-0026's alternatives speak of a commission "per `at_society`". The owner decided that a captain earns ₹6 per bag when it is **delivered** (M8 §3.6), and M8 pays every partner from M4's fulfilment events, not from M7's.
5. **Order moves M7 needs.** M7's handover, rider-mode and redelivery rules need moves and payload fields that ADR-0026 §2's table does not list (M7 §10).
6. **How long carried-over items wait.** The carry-over in item 2 needs a limit for a household that has no next delivery soon.

The ADR rules (`docs/adr/README.md`, `CLAUDE.md`) say an accepted ADR is changed only by a new ADR. This one records the changes so that ADR-0026 and the M4, M7 and M8 contracts agree.

## Decision

### 1. A tiffin no kitchen accepts is refunded at once
- A new move, **tiffin only: `locked → refunded`**, actor `system`, `payload.reason = "no_kitchen"`. It refunds the order's whole paid total through `billing.api.refund_to_wallet`, and the customer is told by `billing.refund_issued` (M11).
- M8's job `refund_unassigned_tiffins` makes the move for every locked tiffin that still has no live assignment when manual assignment closes: the meal's `ready_by` minus 2 hours (T 09:30 for lunch; M8 §3.4, §6).
- **The row to read into ADR-0026's tables:**
  - §2, the transition table: `locked → refunded` | Box / add-on orders: — | Tiffin orders: **system** (`no_kitchen`).
  - §3, the tiffin table: REFUNDED is also reached from LOCKED (`locked`, no kitchen took the order), moved by the system.

### 2. A box missed twice: perishables refunded, non-perishables carried over
This replaces "and the box goes to the surplus cascade" in ADR-0026's decided item 1.
- **Perishable lines** (catalog shelf life ≤ 96 h) are refunded for the steps delivered (`qty − shortage_qty`) at the line's paid price, **minus a ₹20 fee** (2,000 paise), never below 0. The move is `undelivered → refunded`, actor `system` (M7's `schedule_redeliveries`).
- **Non-perishable lines** are neither refunded nor surplus. M7 calls a new **`orders.api.carry_over(session, order_id, lines)`**; M4 adds those lines, at no charge, to the household's next box or add-on order (`carried_from_order_id` on the line), and M6 packs them from the items it kept aside.
- Only the perishables of the returned bag go to the surplus cascade (M6).

### 3. A held bag stays overnight with the captain
- A bag held for `not_home` stays with the captain overnight. Her society gets a stop on the next morning's route even when the van brings nothing else for it.
- The order takes ADR-0026's path unchanged: `undelivered → redelivery` (the evening job), `redelivery → dispatched` at route start (actor `rider`), then `at_society` at the stop's handover, then `delivered` at the door. The captain delivers a bag she kept only after that handover.
- A bag that came back with the van (`returned_to_hub`) stays at the hub and rides on the next morning's van.

### 4. Who is paid when
- **Captains earn ₹6 per bag at `orders.delivered`** (box and add-on orders) when the event's actor is `captain`. They earn nothing at `at_society`, and nothing for a delivery made by a rider in rider mode. This replaces "captain per `at_society`" in ADR-0026's alternatives.
- **M8 decides what earns and M5 records it.** M8 consumes M4's fulfilment events, which carry `kind`, `actor` and `actor_user_id`. It posts each earning through `billing.api.accrue_earning`, keyed per partner, basis and order. The bases are kitchen ₹60 a portion (`orders.delivered`, tiffin), rider ₹8 an order (`orders.dispatched`, actor `rider`) and packer ₹4 an order (`orders.packed`, actor `packer`), editable per hub on M8's rate card (M8 §3.6, M5 §3.5).
- M7's own events (`logistics.handover_completed`, `logistics.route_completed`) pay nobody.

### 5. M4 moves and payloads M7 needs, beyond ADR-0026 §2
These rows extend ADR-0026 §2's table (box and add-on orders unless stated):

| From → to | Who may make it, in addition to ADR-0026 §2 | Why |
|---|---|---|
| undelivered → redelivery | **system** | The automatic free redelivery (`schedule_redeliveries`, T 20:45) |
| dispatched → undelivered | **rider**, **system**, hub | Bags that ride back to the hub: a skipped stop, a count mismatch, `close_routes` |
| dispatched → at_society; at_society → delivered / undelivered | **rider** also where a hub manager switched the stop to rider mode for an absent captain | M7 checks the switch (audited); ADR-0026 allowed the rider only where the society has no active captain |
| locked → refunded | tiffin: **system** | §1 above |

- `orders.api.transition(…, "refunded", payload={"refund_paise", "reason"})`: `refund_paise` defaults to what is left of the paid total after earlier credits; the refund goes through `billing.api.refund_to_wallet` with that reason.
- `orders.delivery_failed` carries `reason` (M7's reasons: `not_home`, `refused`, `wrong_address`, `damaged`, `missing`, `other`, `returned_to_hub`, `not_confirmed`).
- `orders.api.orders_for_delivery` accepts `statuses=("locked", "packed")`, and returns `preferred_partner_id` for tiffins (§7).
- M4 makes these changes in the M7 and M8 PRs that need them, reviewed by both owners (M4 §3.7).

### 6. Carried-over items wait 14 days (a default the owner may change)
- Carried lines go on the household's next box or add-on order whose delivery date is after the missed attempt and that is not yet packed.
- If no such order is packed within **14 days** of the missed attempt, M4 refunds the carried lines at their paid price, **without** the ₹20 fee (reason `carry_over_expired`), and M6 sends the kept items to the surplus cascade. A daily M4 job does this.
- Why 14 days: it covers a household that skips one weekly box, and dry staples keep that long.

### 7. Tiffin menus and the preferred cook (defaults the owner may change)
- **Where tiffin menus live.** A tiffin menu is one kitchen partner's offer for a date and meal (`catalog.tiffin_menus`, M2 §4, with its tiffin `product_id`), shown to the customers of her hub once a hub manager approves it. M4 copies the menu's kitchen to the order as `preferred_partner_id` at booking (M4 §3.5).
- **The cook is a preference, not a hard choice** (owner, 2026-10-09). M8's assignment round first gives each order to its preferred kitchen while she is eligible and has room. Only the orders left over go to M10's fair assignment or M8's greedy fallback, with her capacity and rolling earnings updated (M8 §3.4).
- M10's `POST /assign/kitchens` request is unchanged: it refuses extra fields, so the preference is applied in M8.
- **A captain earns nothing for a tiffin she delivers** in the prototype. ADR-0026 §2 lets her move a tiffin `dispatched → delivered`, but `bag_delivered` counts box and add-on bags only, and the rider is paid for the pickup. A tiffin basis can be added to the rate card later.

## Alternatives considered
- **Edit ADR-0026 in place.** Rejected: the ADR rules allow changes to an accepted ADR's decision only through a new ADR.
- **An unassigned tiffin goes to `cancelled`.** In M4 `cancelled` means no money moved (before the cut-off, or payment failed), so it would hide a paid order. **Keep it `locked` for a hub manager**: a lunch cannot be redelivered, and the money would wait on a person.
- **A box missed twice goes wholly to surplus (ADR-0026 as drafted), or is wholly refunded.** The owner rejected both: dry staples are still good for the family, and refunding them costs money for goods that are not lost.
- **Carry-over with no limit.** The household's goods would sit at the hub without an end, and the order would never close. **7 days** would refund too often when a household skips one week.
- **Pay captains at `at_society` (the handover).** This pays for bags that may never reach a door. The owner chose `delivered`.
- **Pay captains a tiffin basis now.** Possible through the rate card, but there are no tiffin volumes yet to price it. Left as an option.
- **Put the preferred cook into the solver** (a per-portion bonus in M10's objective). This would let fairness outweigh the customer's choice, but it changes M10's request and the eval harness's baseline. Left as a later M10 option.

## Consequences
- (+) The M4, M7 and M8 contracts and the ADRs agree on every order move, refund and earning before S5.
- (+) Every paid order has an ending: a tiffin no kitchen accepts, a box missed twice and carried-over items all end in a refund or a delivery.
- (−) M4's per-kind state machine gains four moves and a refund payload, plus `carry_over`, a carry-over table and its daily expiry job. Each needs tests per actor and kind.
- (−) M6 keeps the non-perishables of a returned bag aside per household, packs carried lines from them, and sends them to surplus when a carry-over expires.
- (−) **Fairness reaches only the orders left over.** When every tiffin comes from a menu with a cook, M10's fair assignment sees only the orders left over after the preferred kitchens are full. The paper and the eval harness report fairness for that remainder, which is the default's main cost.
- **Follow-ups:**
  - **M4** (in the M7 and M8 PRs): §5 and §6 above, `preferred_partner_id`;
  - **M6** (in the M7 PR): the kept items and the surplus;
  - **M2**: `product_id` on `catalog.tiffin_menus`;
  - **handbook 06** §6.2 and §6.3: the new moves;
  - **handbook 07** W5: the held bag overnight, then refund and carry-over;
  - **M7 and M8**: their contracts already follow this ADR.

## As built (M7 PRs, October 2026)
- **A hub manager tapping for a rider.** When a hub manager starts or completes a route for its rider, M7 moves the orders with actor `rider` and the route's rider's user id, so the rider's per-order pay (M8 §3.6) is unchanged; the manager is recorded on M7's field event. A route with no rider moves its orders as `hub`.
- **A box missed twice, until `carry_over` exists.** M7 refunds the perishable lines minus the ₹20 fee (never below 0) and the non-perishable lines in full without the fee, and records the non-perishable lines on its field event, so no paid goods are left without an ending. When M4's `carry_over` lands, the non-perishables go back to being carried over as decided in §2.

## Related
[ADR-0026](0026-operations-contract.md) · [ADR-0021](0021-jobs-without-a-broker.md) · [M2 spec](../modules/M2-catalog.md) · [M4 spec](../modules/M4-orders.md) · [M5 spec](../modules/M5-billing.md) · [M6 spec](../modules/M6-hub-ops.md) · [M7 spec](../modules/M7-logistics.md) · [M8 spec](../modules/M8-partners.md) · [M10 spec](../modules/M10-intelligence.md) · Handbook [Chapter 06](../handbook/06-domain-lifecycles.md), [Chapter 07](../handbook/07-workflows.md)

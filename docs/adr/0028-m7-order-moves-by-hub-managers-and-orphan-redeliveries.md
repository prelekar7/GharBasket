# ADR-0028 — M7 order moves: a hub manager tapping for a rider, and a redelivery no route picked up

- **Status:** Accepted (2026-10-20, by the owner, Prathamesh)
- **Date:** 2026-10-20
- **Decision ID:** D28
- **Amends:** [ADR-0027](0027-operations-contract-amendments.md) §5 (the order moves M7 needs); ADR-0026 §2 and ADR-0027 are otherwise unchanged

## Context
Two details of the M7 build (PRs #321–#330) were first written into ADR-0027 in place. The ADR rules (`docs/adr/README.md`) allow an accepted ADR to change only through a new ADR, so the in-place text was removed again and the two details are proposed here for the owner to approve:

1. **A hub manager can tap for a rider.** M7 lets a hub manager start, complete or close a route for its rider (`POST /v1/routes/{id}/start` and `/complete`, and the `close_routes` job for a route nobody completed). ADR-0026 §2 and ADR-0027 §4 say the rider earns ₹8 per order on `orders.dispatched` with actor `rider`, so the actor on the order's move decides who is paid. If the manager's tap were recorded as `hub`, the rider would lose the pay for work he did.
2. **A redelivery that no route picks up.** A free redelivery (`undelivered → redelivery`, attempt 2) is booked for the next morning and added to that day's 23:15 solve. When it is booked on a day whose plan was already published without a stop for it, or its day has passed, no van takes the bag and the order sits in `redelivery`. The hub manager must be able to book it again or give the customer the money back, but the state machine has no way out of `redelivery` except `dispatched`.

## Decision

### 1. A hub manager's tap for a rider moves the orders as `rider`
- When a hub manager starts or completes a route that has a rider, M7 makes the order moves (`packed → dispatched`, `redelivery → dispatched`, `dispatched → undelivered` with reason `returned_to_hub`) with actor **`rider`** and `actor_user_id` = the **route's rider**, not the manager.
- The manager is recorded on M7's own `logistics.field_events` row (`user_id`), which is the audit trail of who tapped.
- A route with **no rider** is moved with actor `hub` and the manager's user id. The `close_routes` job (T 16:00) moves a route's bags as `system`.
- Handovers and door moves keep their own actor rules (`captain`, `rider` in rider mode, `hub`), as in ADR-0027 §5.

### 2. A new box and add-on move: `redelivery → refunded` by `system` and `hub`
- A redelivery no route picked up is resolved by a hub manager in M7's triage (`POST /v1/deliveries/{order_id}/resolve`): `redeliver` books it again on the next day whose plan is not solved yet (no order move: the order stays in `redelivery`), or `refund` moves the order **`redelivery → refunded`**, actor `system` (naming the manager in `actor_user_id`), paying the wallet through M4's existing refund step (`refund_paise` up to what is left of the paid total, reason `triage`).
- Actor `hub` is allowed on the move too, for a console override.
- A left-behind bag that is still `packed` (the route started without it) has no refund path: it can only ride again.

## Alternatives considered
- **Record a manager's tap as `hub`.** Simple, but the rider's per-order pay (M8 §3.6) is read from the actor, so the manager would need to pay by hand.
- **Move an orphan `redelivery → undelivered → refunded`.** It would reopen the first-miss path (`undelivered → redelivery`) and publish a second `orders.delivery_failed` for a bag nobody tried to deliver.
- **Cancel the order.** In M4 `cancelled` means no money moved, so it would hide a paid order.
- **Leave orphans to support.** The exceptions board already lists them (`orphans`); without a move the hub manager has no action.

## Consequences
- (+) The rider's pay does not depend on who tapped; every orphan redelivery ends in a booked van or a refund.
- (−) M4's per-kind state machine gains one move (box and add-on only), with a test per actor; M4 §3.7 and the table in M4 list it.
- (−) The field event, not the order timeline, shows that a manager tapped for a rider.
- **Not changed:** the owner's decision of 9 October 2026 on a box missed twice (ADR-0027 §2). M7 refunds the perishable lines minus the ₹20 fee and does not refund the non-perishables, which ride on the household's next delivery. M4's `carry_over` and M6's kept items do not exist yet; until they do M7 only records the lines (`logistics.carry_over_pending`, and the `resolve` field event). The carry-over of the non-perishables will be built next as an M4 follow-up, contract first (M4 `carry_over` spec and M6 kept items), before MS-B.

## Related
[ADR-0026](0026-operations-contract.md) · [ADR-0027](0027-operations-contract-amendments.md) · [M4 spec](../modules/M4-orders.md) · [M7 spec](../modules/M7-logistics.md) · [M8 spec](../modules/M8-partners.md)

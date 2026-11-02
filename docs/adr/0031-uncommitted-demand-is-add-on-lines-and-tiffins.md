# ADR-0031 — Uncommitted demand is add-on lines and tiffin orders

- **Status:** Accepted (2026-11-02, by the owner, Prathamesh)
- **Date:** 2026-11-02
- **Decision ID:** D31
- **Amends:** M10 §3.6 (the forecast's series), M6 §3.3 (procurement's `placed_addons`) and the M4 facade `sold_lines`. The owner's rule of 2026-10-09 that refuses add-on-only orders (M4 §3.5, M3 §10) stays as it is.

## Context
The demand forecast (M-E, M10 §3.6) predicts **uncommitted** demand: what is not planned in a box. M10 classified a sold line by its **order's** kind. It counted lines of `addon` and `tiffin` orders as uncommitted and every line of a `box` order as committed, "so an add-on inside a box is committed". M6 procurement (#472) and the drift monitor used the same rule.

On 2026-10-09 the owner decided that add-on-only orders are refused (`orders.kind_unavailable`). Add-ons ride only as lines on a box order (`POST /v1/orders/{id}/addons`, `OrderLine.kind = "addon"`). Nothing in M4 creates an `addon`-kind order, so the forecast's add-on series were always empty. Tiffin orders exist in M4, but no API writes tiffin menus yet (M2 §3.4), and the seed has no tiffin products or slots. As a result, the forecast had no data, in production or in the simulator, while the eval harness (RQ3) derived `addon`-kind orders that the product cannot create.

## Decision
- **An add-on line is uncommitted demand wherever it rides.** A sold line counts as `addon` when `OrderLine.kind = "addon"`, whether its order is a box or an add-on order. Otherwise it counts as its order's kind: `tiffin` for a tiffin's line, `box` for a box's own lines. Uncommitted demand = add-on lines + tiffin orders; committed demand = the other lines of box orders.
- **One definition:** `orders.api.demand_kind(order_kind, line_kind)` and `orders.api.UNCOMMITTED_KINDS`. `SoldLine` gains `line_kind` and `demand_kind`. The forecast job (`jobs/forecast.py` `order_lines`), M10's `monitor_drift` and M6's procurement all classify with it.
- **M6 counts the placed add-ons once.** Procurement's `placed_addons` (#472's rule) now includes add-on lines on box orders. They stay in `committed_qty` and, in a plan built before the cut-off, are taken off the forecast buffer once (`max(0, q_tau − placed_addons)`). They are never dropped and never added on top. After the cut-off the plan uses the rule buffer (Consequences, the owner's follow-up decision).
- **The eval harness follows production.** RQ3 derives add-ons only as production can produce them: on a box the household ordered and funded (they are lost in a skipped or unfunded week), delivered on the box's day, never for a product already on that box (M4 refuses those; the household edits the box line instead), and within M4's add-on limits.

## Alternatives considered
- **Allow `kind: addon` orders again** (for example, add-ons for a week that already has a box). This reverses the owner's 2026-10-09 decision and needs new order creation, a slot, the cut-off and payment in M4, M6 packing and M7 routing. Rejected for now. If it is ever allowed, `demand_kind` already counts such orders as `addon`.
- **Keep the order-kind rule and forecast tiffins only.** Tiffins have no menus or products in any environment yet, so the forecast would stay without data until M2's menu writes and M8's kitchens are live.
- **Write `addon`-kind orders directly in the simulator.** Rejected: the simulator goes through the modules' public paths only, and the data would describe orders production cannot have.

## Consequences
- (+) The forecast learns from data the product really produces: add-ons on boxes in production, and the same rows from the simulator (`--write-db` places them through `set_addons`).
- (+) Eval and production share one definition, so RQ3 scores the demand production would forecast.
- (−) Add-ons close at the same cut-off as the box (M4 refuses them after it), so "uncommitted" here means "not planned in a box", not "unknown at the cut-off". **Owner's follow-up decision (2 Nov 2026, from the review of #483):** M6 builds day T's plan at T-1 21:10, after T's cut-off, when every add-on for T is placed. So a plan built after its date's cut-off buys the placed add-ons (in `committed`) with the rule buffer, not the forecast; the forecast is the buffer, less the add-ons placed so far, only for a plan built ahead of the cut-off. The forecast serves lead-time planning (plans built ahead, vendor lead times, the T+1…T+7 outlook) and tiffins once kitchens are supplied (M6 §3.3).
- (−) RQ3's numbers from before this change (28 Oct and 2 Nov) used the old derivation. They are re-run, and the change is recorded in eval protocol §7 (the third M-E entry of 2026-11-02), because the derivation changed after those results were seen.
- `orders/api.py`, `hub_ops/procurement.py`, `intel_gateway/jobs.py`, training `jobs/forecast.py` and `eval/forecast_ab.py` implement this. M4 §facades, M6 §3.3 and M10 §3.6 are updated.

## Open question
- **One consistent rule for what the household added.** A customer can also add or swap lines in the box itself before approval (M3 line ops, `reason_code` `added_by_you` / `swapped_by_you`). Those are as unplanned as an add-on but count as committed today. A later decision may count them as uncommitted too, so the line between "planned" and "added by the household" is the same in the box and beside it. M4's order lines already copy the box line's `reason_code` at approval, so it would be a change to `demand_kind` (and the readers that pass it the line), plus the eval harness's derivation.

## Related
[ADR-0008](0008-simulator-first-data-source-tagging.md) · M4 §3.5 · M6 §3.3 · M10 §3.6 · [Eval protocol](../research/eval-protocol.md) §7 · Handbook [Chapter 08](../handbook/08-ai-and-ml.md)

import { mulDivRound } from "./money.js";

/**
 * Unit economics of one box (M12 US-2, section 3.4):
 *   contribution per box = AOV × margin + delivery fee − (delivery + packing + captain + payment fee + CAC)
 * The delivery fee is what a customer pays per box until their society goes live: revenue, 0 for live societies.
 * Pure integer arithmetic: money in paise, percentages in basis points (24% = 2400), every percentage
 * step rounded half up to a whole paisa. The page, the P&L and the tests all use this one function.
 *
 * @typedef {object} Assumptions
 * @property {number} aov_paise average box value
 * @property {number} margin_bp margin on produce, after vendor cost
 * @property {number} delivery_paise
 * @property {number} packing_paise
 * @property {number} captain_paise Society Captain commission per box
 * @property {number} payment_fee_bp share of the box value paid to the payment provider
 * @property {number} cac_paise customer acquisition cost spread per box
 *
 * @typedef {object} BoxEconomics
 * @property {number} margin_paise AOV × margin
 * @property {number} delivery_fee_paise revenue: the pre-live fee per box
 * @property {number} delivery_paise
 * @property {number} packing_paise
 * @property {number} captain_paise
 * @property {number} fee_paise
 * @property {number} cac_paise
 * @property {number} contribution_paise may be negative
 */

/**
 * @param {Assumptions} a
 * @param {number} [deliveryFee] the pre-live delivery fee per box, paise (revenue)
 * @returns {BoxEconomics}
 */
export function boxEconomics(a, deliveryFee = 0) {
  const margin = mulDivRound(a.aov_paise, a.margin_bp, 10_000);
  const fee = mulDivRound(a.aov_paise, a.payment_fee_bp, 10_000);
  return {
    margin_paise: margin,
    delivery_fee_paise: deliveryFee,
    delivery_paise: a.delivery_paise,
    packing_paise: a.packing_paise,
    captain_paise: a.captain_paise,
    fee_paise: fee,
    cac_paise: a.cac_paise,
    contribution_paise:
      margin +
      deliveryFee -
      a.delivery_paise -
      a.packing_paise -
      a.captain_paise -
      fee -
      a.cac_paise,
  };
}

/**
 * Change in contribution per box when one assumption moves and the rest stay: the "what moves it most"
 * table. The steps are fixed steps (margin +2 points, AOV +₹60, delivery −₹10, CAC +₹7), so the
 * rows keep the same meaning when the inputs change.
 * @param {Assumptions} a
 * @param {number} [deliveryFee] the pre-live delivery fee per box, paise
 * @returns {{ key: string, from: number, to: number, change_paise: number }[]}
 */
export function sensitivities(a, deliveryFee = 0) {
  const base = boxEconomics(a, deliveryFee).contribution_paise;
  /** @type {[string, keyof Assumptions, number][]} */
  const moves = [
    ["margin", "margin_bp", a.margin_bp + 200],
    ["aov", "aov_paise", a.aov_paise + 6000],
    ["delivery", "delivery_paise", Math.max(0, a.delivery_paise - 1000)],
    ["cac", "cac_paise", a.cac_paise + 700],
  ];
  return moves.map(([key, field, to]) => ({
    key,
    from: a[field],
    to,
    change_paise: boxEconomics({ ...a, [field]: to }, deliveryFee).contribution_paise - base,
  }));
}

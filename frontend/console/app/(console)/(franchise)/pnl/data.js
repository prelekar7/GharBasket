import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";

/**
 * A line of the P&L. `basis` says how the amount was worked out, so the view words it:
 * `times` "702 boxes × ₹620 average", `share` "76% of box revenue". Costs are negative paise,
 * rounded to the whole rupee.
 * @typedef {{ kind: "times", qty: number, unit: string, rate_paise: number, note?: string }
 *   | { kind: "share", bp: number, of: string }} Basis
 * @typedef {{ key: string, label: string, basis: Basis, amount_paise: number }} PnlLine
 *
 * GET /v1/analytics/pnl?hub_id=&period= (proposed, M12): the month's P&L from the ledger.
 * @typedef {{
 *   hub_id: string, period: string, period_label: string,
 *   periods: { value: string, label: string }[],
 *   boxes: number, tiffins: number, revenue_paise: number, contribution_paise: number,
 *   previous_contribution_paise: number | null, per_box_paise: number,
 *   captains_paise: number, kitchens_paise: number,
 *   lines: PnlLine[], weeks: { label: string, contribution_paise: number }[],
 * }} Pnl
 */

/**
 * @param {string | undefined} period "2026-09"; the latest month when left out
 * @returns {Promise<Pnl>}
 */
export async function loadPnl(period) {
  const hub = await currentHub();
  return apiGet("/v1/analytics/pnl", { searchParams: { hub_id: hub.id, period } });
}

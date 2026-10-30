import { apiGet } from "@/lib/api.js";
import { apiSource, parseSource } from "@/lib/data-source.js";
import { currentHub } from "@/lib/hub.js";
import { isOffered, monthOptions } from "@/lib/months.js";
import { orNotShipped } from "@/lib/proposal.js";

/**
 * A line of the P&L. `basis` says how the amount was worked out, so the view words it:
 * `times` "702 boxes × ₹641.50 average", `share` "76% of box revenue". Costs are negative, in exact paise
 * (the page rounds for display); `source` says whether the amount was measured or comes from an assumption.
 * @typedef {{ kind: "times", qty: number, unit: string, rate_paise: number }
 *   | { kind: "share", bp: number, of: string }} Basis
 * @typedef {"box_revenue" | "delivery_fee_revenue" | "tiffin_revenue" | "produce" | "kitchen" | "delivery"
 *   | "packing" | "captain" | "fees" | "acquisition"} LineKey
 * @typedef {{ key: LineKey, amount_paise: number, source: "measured" | "assumed", basis: Basis }} PnlLine
 *
 * GET /v1/analytics/pnl?hub_id=&period=&data_source= (M12 section 3.4): one month's statement.
 * @typedef {{
 *   hub_id: string, period: string, as_of: string,
 *   boxes: number, tiffins: number, revenue_paise: number, contribution_paise: number,
 *   previous_contribution_paise: number | null, per_box_paise: number,
 *   partners_paise: { captains: number, kitchens: number, riders: number, packers: number },
 *   lines: PnlLine[], weeks: { week: string, contribution_paise: number }[],
 * }} Pnl
 */

/**
 * The month list is the console's (any of the last 24 months); the statement defaults to the last full month.
 * @param {string | undefined} period "2026-09"
 * @param {string | undefined} source "simulator" (default) or "pilot" (from the URL)
 * @returns {Promise<(Pnl & { periods: { value: string, label: string }[] }) | null>} null while GET /v1/analytics/pnl has not shipped
 */
export async function loadPnl(period, source) {
  const hub = await currentHub();
  const periods = monthOptions(Date.now());
  const chosen = isOffered(period, periods) ? /** @type {string} */ (period) : periods[1].value;
  const pnl = await orNotShipped(
    apiGet("/v1/analytics/pnl", {
      searchParams: { hub_id: hub.id, period: chosen, data_source: apiSource(parseSource(source)) },
    }),
  );
  if (!pnl) return null;
  return { ...pnl, periods };
}

import { apiGet } from "@/lib/api.js";
import { apiSource, parseSource } from "@/lib/data-source.js";
import { currentHub } from "@/lib/hub.js";

/**
 * GET /v1/analytics/unit-economics?hub_id=&from=&to=&data_source= (M12 section 3.4): the hub's saved
 * assumptions, what the pilot (or the simulator) measured of them, the values in use and where each came from,
 * the pre-live delivery fee (revenue), and the box they work out to. History and the version for If-Match
 * come from GET /v1/analytics/hubs/{id}/assumptions.
 * @typedef {import("@/lib/unit-economics.js").Assumptions} Assumptions
 * @typedef {keyof Assumptions} AssumptionKey
 * @typedef {{
 *   hub_id: string, data_source: "real" | "synthetic", from: string, to: string, as_of: string, boxes: number,
 *   assumptions: Assumptions, measured: Record<AssumptionKey, number | null>, used: Assumptions,
 *   sources: Record<AssumptionKey, "measured" | "assumed" | "override">,
 *   delivery_fee_paise: number, per_box: import("@/lib/unit-economics.js").BoxEconomics,
 *   weeks: { week: string, boxes: number, contribution_paise: number }[],
 * }} Economics
 * @typedef {{ at: string, by: { name: string }, changes: { key: string, from: number, to: number }[] }[]} History
 * @typedef {Economics & { version: number, history: History }} UnitEconomics
 */

/** How far back the measured inputs look. */
const WINDOW_DAYS = 28;

/**
 * @param {string | undefined} source "simulator" (default) or "pilot" (from the URL)
 * @returns {Promise<UnitEconomics>}
 */
export async function loadUnitEconomics(source) {
  const hub = await currentHub();
  const to = new Date(Date.now() + 5.5 * 3_600_000);
  const from = new Date(to.getTime() - WINDOW_DAYS * 86_400_000);
  const [economics, saved] = await Promise.all([
    apiGet("/v1/analytics/unit-economics", {
      searchParams: {
        hub_id: hub.id,
        data_source: apiSource(parseSource(source)),
        from: from.toISOString().slice(0, 10),
        to: to.toISOString().slice(0, 10),
      },
    }),
    apiGet(`/v1/analytics/hubs/${hub.id}/assumptions`),
  ]);
  return { ...economics, version: saved.version, history: saved.history };
}

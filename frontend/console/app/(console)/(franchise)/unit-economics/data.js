import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";

/**
 * GET /v1/analytics/unit-economics?hub_id= (M12 section 3): the hub's saved assumptions, the box they
 * work out to and the log of who changed what.
 * @typedef {import("@/lib/unit-economics.js").Assumptions} Assumptions
 * @typedef {{
 *   hub_id: string, assumptions: Assumptions,
 *   per_box: import("@/lib/unit-economics.js").BoxEconomics,
 *   history: { at: string, by: string, change: string }[],
 * }} UnitEconomics
 */

/** @returns {Promise<UnitEconomics>} */
export async function loadUnitEconomics() {
  const hub = await currentHub();
  return apiGet("/v1/analytics/unit-economics", { searchParams: { hub_id: hub.id } });
}

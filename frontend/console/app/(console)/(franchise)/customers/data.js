import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";

/**
 * GET /v1/analytics/customer-summary?hub_id= (proposed): the hub's households as counts per society.
 * Aggregates only; a franchise owner sees how the families are doing, not who they are (M12 open
 * question, proposed answer). Rates are basis points; societies still unlocking have nulls.
 * @typedef {{ id: string, name: string, status: "live" | "unlocking" | "prospect",
 *   active_households: number | null, paused_households: number | null,
 *   approved_on_time_bp: number | null, average_box_paise: number | null }} SocietyRow
 * @typedef {{ hub_id: string, active_households: number, active_households_change: number,
 *   paused_households: number, approved_on_time_bp: number, joining_societies: string[],
 *   societies: SocietyRow[] }} CustomerSummary
 */

/** @returns {Promise<CustomerSummary>} */
export async function loadCustomers() {
  const hub = await currentHub();
  return apiGet("/v1/analytics/customer-summary", { searchParams: { hub_id: hub.id } });
}

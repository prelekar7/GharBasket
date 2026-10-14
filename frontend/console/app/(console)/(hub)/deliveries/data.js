import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";

/**
 * Shapes of GET /v1/deliveries?hub_id= (a proposal: docs/modules/M7-logistics.md lists only the per-order proof and
 * undelivered writes). One row per bag; the payload never carries a customer name or phone number.
 * @typedef {"door" | "lobby" | "with_captain" | "issue"} BagState
 *
 * @typedef {object} Delivery
 * @property {string} bag  bag label, e.g. "B-218"
 * @property {string} flat  e.g. "B-1104"
 * @property {string} society_id
 * @property {string} society
 * @property {BagState} state
 * @property {string} state_text  the state in words, e.g. "Eggs broken"
 * @property {string} proof  how the handover was proven, in words
 * @property {string} at  ISO time of the last scan
 * @property {{ text: string, href?: string } | null} next  what happens next, if anything
 *
 * @typedef {object} DeliverySummary
 * @property {number} at_door
 * @property {number} in_lobby
 * @property {number} problems
 * @property {string} lobby_collect_by  ISO time the lobby bags go back to the hub
 * @property {{ society_id: string, society: string, bags: number, handed_over_at: string }[]} with_captain  handed over, not yet at doors
 */

/**
 * Today's bags at the selected hub.
 * @returns {Promise<{ date: string, as_of: string, summary: DeliverySummary, items: Delivery[] }>}
 */
export async function getDeliveries() {
  const hub = await currentHub();
  return apiGet("/v1/deliveries", { searchParams: { hub_id: hub.id } });
}

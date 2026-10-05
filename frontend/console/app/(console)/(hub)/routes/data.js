import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";

/**
 * Shapes of GET /v1/routes (docs/modules/M7-logistics.md §3 path; the field names are our proposal until the OpenAPI lands).
 * @typedef {object} RouteStop
 * @property {number} seq
 * @property {"drop" | "pickup"} kind
 * @property {string} society_id
 * @property {string} society
 * @property {string} where  gate and locality
 * @property {string | null} captain  first name and "tai"/"bhai", never a phone number
 * @property {string} eta  ISO
 * @property {string | null} handed_over_at  ISO, once the captain has taken the crates
 * @property {number} crates
 * @property {number} bags
 * @property {"todo" | "current" | "done" | "issue"} status
 * @property {number} lat
 * @property {number} lng
 *
 * @typedef {object} SolverStats
 * @property {string} solver  "ortools" or "fallback" (yesterday's sequence)
 * @property {number} distance_m
 * @property {number} drive_min
 * @property {number} crates
 * @property {number} capacity_crates
 * @property {boolean} empty_crates_on_return
 * @property {number} donation_pickups
 *
 * @typedef {object} VanPosition
 * @property {number} lat
 * @property {number} lng
 * @property {string} at  ISO
 *
 * @typedef {object} RouteRecord
 * @property {string} id
 * @property {string} hub_id
 * @property {string} date  ISO date
 * @property {string} slot_id
 * @property {"published" | "in_progress" | "completed"} status
 * @property {string} published_at  ISO
 * @property {{ name: string, lat: number, lng: number }} hub
 * @property {{ id: string, name: string, registration: string }} vehicle
 * @property {{ id: string, name: string }} rider
 * @property {VanPosition | null} van  last known position, null before the route starts
 * @property {RouteStop[]} stops
 * @property {SolverStats} solver_stats
 */

/**
 * The routes of one slot at the selected hub, today.
 * @param {string} slot
 * @returns {Promise<{ date: string, items: RouteRecord[] }>}
 */
export async function getRoutes(slot) {
  const hub = await currentHub();
  return apiGet("/v1/routes", { searchParams: { hub_id: hub.id, slot_id: slot } });
}

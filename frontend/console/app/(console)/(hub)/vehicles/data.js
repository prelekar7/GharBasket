import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";

/**
 * Shapes of GET and POST /v1/hubs/{id}/vehicles (docs/modules/M7-logistics.md §3 and §4; `registration`, `status`
 * and `assignment` are our additions to the spec's columns).
 * @typedef {"van" | "ev3w" | "bike"} VehicleKind
 *
 * @typedef {object} Vehicle
 * @property {string} id
 * @property {string} hub_id
 * @property {VehicleKind} kind
 * @property {string} name  numbered by the API, e.g. "Van 1"
 * @property {string} registration  e.g. "MH-46 BX 2041"
 * @property {number} capacity_kg
 * @property {number} capacity_crates
 * @property {"on_route" | "available" | "retired"} status
 * @property {{ rider: string, route_id: string, route_label: string } | null} assignment  today's rider and route
 */

/**
 * The selected hub's vehicles, retired ones included (they keep their history).
 * @returns {Promise<{ hubId: string, items: Vehicle[] }>}
 */
export async function getVehicles() {
  const hub = await currentHub();
  const { items } = await apiGet(`/v1/hubs/${hub.id}/vehicles`);
  return { hubId: hub.id, items };
}

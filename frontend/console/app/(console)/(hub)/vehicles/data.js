import { apiGet } from "@/lib/api.js";
import { apiAll } from "@/lib/api-pages.js";
import { currentHub } from "@/lib/hub.js";
import { istDate } from "@/lib/payout-weeks.js";

/**
 * Shapes of the vehicle endpoints (docs/modules/M7-logistics.md §3.1).
 * @typedef {object} Vehicle
 * @property {string} id
 * @property {string} hub_id
 * @property {string} label  unique per hub, 1 to 20 characters, e.g. "EV-1"
 * @property {"cargo_3w" | "two_wheeler"} kind  boxes and add-ons ride cargo_3w, tiffins two_wheeler
 * @property {string | null} registration
 * @property {number} capacity_kg
 * @property {number} capacity_crates
 * @property {number} capacity_tiffins
 * @property {{ user_id: string, name: string } | null} default_rider  set on new routes
 * @property {boolean} active  vehicles are deactivated, never deleted
 * @property {number} version
 *
 * @typedef {object} TodayRoute  what a vehicle does today, from the hub's routes
 * @property {string} vehicleId
 * @property {number} trip
 * @property {string} status
 * @property {string | null} rider
 */

/**
 * The selected hub's vehicles, retired ones included (they keep their history), what each does today, and the hub's
 * active riders for the "default rider" choice.
 * @returns {Promise<{ hubId: string, items: Vehicle[], today: TodayRoute[], riders: { user_id: string, name: string }[] }>}
 */
export async function getVehicles() {
  const hub = await currentHub();
  const base = `/v1/hubs/${encodeURIComponent(hub.id)}`;
  const [vehicles, routes, riders] = await Promise.all([
    apiGet(`${base}/vehicles`),
    apiGet(`${base}/routes`, { searchParams: { date: istDate(Date.now()) } }),
    apiAll(`${base}/partners`, { kind: "rider", status: "active" }),
  ]);
  return {
    hubId: hub.id,
    items: vehicles.items,
    today: routes.items
      .filter((/** @type {{ status: string }} */ r) => r.status !== "cancelled")
      .map(
        (
          /** @type {{ vehicle: { id: string }, trip: number, status: string, rider: { name: string } | null }} */ r,
        ) => ({
          vehicleId: r.vehicle.id,
          trip: r.trip,
          status: r.status,
          rider: r.rider?.name ?? null,
        }),
      ),
    riders: riders.items.map(
      (/** @type {{ user_id: string, name: string | null, display_name: string }} */ p) => ({
        user_id: p.user_id,
        name: p.name ?? p.display_name,
      }),
    ),
  };
}

import { apiGet } from "@/lib/api.js";
import { getRole } from "@/lib/auth.js";
import { currentHub } from "@/lib/hub.js";
import { istDate } from "@/lib/payout-weeks.js";
import { orNotShipped } from "@/lib/proposal.js";

/**
 * Shapes of the route endpoints (docs/modules/M7-logistics.md §3.2 and §3.3).
 * @typedef {object} RouteStop
 * @property {string} id
 * @property {number} seq
 * @property {"drop" | "bank" | "kitchen"} kind
 * @property {{ id: string, name: string, location: { lat: number, lng: number } } | null} society  null for a food bank or a kitchen
 * @property {{ id: string, label: string, start: string, end: string } | null} slot
 * @property {string} eta  IST "HH:MM", planned
 * @property {string | null} eta_now  the planned time plus the route's current delay
 * @property {"pending" | "arrived" | "handed_over" | "skipped"} status
 * @property {number} crates
 * @property {number} bags
 * @property {{ display_name: string, photo_url: string | null } | null} captain  null: no active captain (rider mode)
 * @property {boolean} rider_mode
 * @property {string} navigate_url
 * @property {string | null} handed_over_at  ISO
 *
 * @typedef {object} RouteRecord  `RouteDetail`: the summary and the stops
 * @property {string} id
 * @property {string} plan_id
 * @property {string} delivery_date
 * @property {number} trip
 * @property {"draft" | "published" | "in_progress" | "completed" | "cancelled"} status
 * @property {{ id: string, label: string, kind: string }} vehicle
 * @property {{ user_id: string, name: string } | null} rider
 * @property {string} depart  IST "HH:MM"
 * @property {string} return_by
 * @property {number} crates
 * @property {number} bags
 * @property {number} km
 * @property {RouteStop[]} stops
 *
 * @typedef {object} RoutePlan
 * @property {string} id
 * @property {"queued" | "solving" | "solved" | "published" | "failed" | "superseded"} status
 * @property {"job" | "manual"} trigger
 * @property {"ml" | "fallback_previous" | "fallback_nearest" | null} source
 * @property {"osrm" | "haversine" | null} distance_source
 * @property {{ stops: number, orders: number, vehicles: number, routes: number, km: number, cost_paise: number, on_time: number, late_min: number, problems: string[] } | null} summary
 * @property {string | null} error
 * @property {string | null} published_at  ISO
 *
 * @typedef {object} VanPosition
 * @property {number} lat
 * @property {number} lng
 * @property {string} at  ISO
 */

/** @typedef {"today" | "tomorrow"} Day */

/**
 * The hub's box routes for today or tomorrow (riders see tomorrow's the night before), the day's newest plan, the hub's
 * position for the map and each vehicle's capacity.
 * @param {Day} day
 */
export async function getRoutes(day) {
  const [hub, role] = await Promise.all([currentHub(), getRole()]);
  const now = Date.now(); // after the cookies are read, as cacheComponents requires
  const date = istDate(day === "tomorrow" ? now + 86_400_000 : now);
  const base = `/v1/hubs/${encodeURIComponent(hub.id)}`;
  const [summaries, plans, hubDetail, vehicles] = await Promise.all([
    apiGet(`${base}/routes`, { searchParams: { date, kind: "box" } }),
    apiGet(`${base}/route-plans`, { searchParams: { date, kind: "box" } }),
    orNotShipped(apiGet(base)),
    apiGet(`${base}/vehicles`),
  ]);
  /** @type {RouteRecord[]} */
  const routes = await Promise.all(
    summaries.items.map((/** @type {{ id: string }} */ r) =>
      apiGet(`/v1/routes/${encodeURIComponent(r.id)}`),
    ),
  );
  /** @type {RoutePlan[]} */
  const list = plans.items;
  return {
    date,
    day,
    routes,
    plan: list.find((p) => p.status !== "superseded") ?? null,
    hub: {
      name: hub.name,
      lat: /** @type {number | null} */ (hubDetail?.location?.lat ?? null),
      lng: /** @type {number | null} */ (hubDetail?.location?.lng ?? null),
    },
    capacity: Object.fromEntries(
      vehicles.items.map((/** @type {{ id: string, capacity_crates: number }} */ v) => [
        v.id,
        v.capacity_crates,
      ]),
    ),
    canPlan: role === "hub_manager",
  };
}

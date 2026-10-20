// Hub mocks (M6 hub_ops): the real `GET /v1/hubs` (HubsOut: id, slug, name, status of the hubs the
// caller works in; admin: every hub) and `GET /v1/hubs/{hub_id}` (HubOut: address, location, service
// area, timings, version, ...). The pilot has one hub, the seed hub Kharghar. Ids are uuids like the real
// API's (the seed hub's id is the backend's SEED_HUB_ID); the readable name is `slug`. The detail store
// (`hubDetails`) is also what mocks/hubadmin.js writes to.
import { HttpResponse, http } from "msw";
import { polygon } from "./adminShared.js";
import { mockStore, problem, roleOf } from "./util.js";

/** The backend seed's hub, Kharghar (tools/seed.py `SEED_HUB_ID`). */
export const KHARGHAR_HUB_ID = "0199a000-0000-7000-8000-00000000a001";

/** A made-up second hub, so the KPI Hub filter has something to compare. Not in the backend seed. */
export const TALOJA_HUB_ID = "0199a000-0000-7000-8000-00000000a002";

/** @type {{ id: string, slug: string, name: string, status: "active" | "paused", location: { lat: number, lng: number } }[]} */
export const HUBS = [
  {
    id: KHARGHAR_HUB_ID,
    slug: "kharghar",
    name: "Kharghar hub",
    status: "active",
    location: { lat: 19.033, lng: 73.066 },
  },
  // Only admins see the second hub (see visibleHubs).
  {
    id: TALOJA_HUB_ID,
    slug: "taloja",
    name: "Taloja hub",
    status: "active",
    location: { lat: 19.07, lng: 73.1 },
  },
];

/**
 * Admins see every hub; hub managers and franchise owners see theirs (the seed gives them Kharghar).
 * @param {Request} request
 */
export function visibleHubs(request) {
  return roleOf(request) === "admin" ? HUBS : HUBS.filter((h) => h.id === KHARGHAR_HUB_ID);
}

/**
 * HubOut, the full record of a hub.
 * @typedef {{ id: string, slug: string, name: string, status: "active" | "paused", address: string,
 *   location: { lat: number, lng: number },
 *   service_area: { type: "Polygon", coordinates: number[][][] } | null,
 *   timings: { receiving: string, packing: string, dispatch: string, surplus: string },
 *   procurement_buffer_pct: number, pre_live_box_fee_paise: number,
 *   version: number, created_at: string, updated_at: string }} HubDetail
 */

const TIMINGS = { receiving: "04:00", packing: "05:00", dispatch: "06:30", surplus: "12:30" };

/** @type {() => HubDetail[]} */
const seed = () => [
  {
    ...HUBS[0],
    address: "Sector 10, Kharghar",
    // Kharghar to New Panvel; Old Panvel (18.988 N) lies just outside.
    service_area: polygon([
      [73.05, 19.045],
      [73.1, 19.058],
      [73.125, 19.03],
      [73.118, 19.003],
      [73.062, 19.006],
      [73.045, 19.03],
    ]),
    timings: TIMINGS,
    procurement_buffer_pct: 10,
    pre_live_box_fee_paise: 3000,
    version: 1,
    created_at: "2026-09-01T08:00:00Z",
    updated_at: "2026-09-01T08:00:00Z",
  },
  {
    ...HUBS[1],
    address: "Sector 3, Taloja",
    service_area: null,
    timings: TIMINGS,
    procurement_buffer_pct: 10,
    pre_live_box_fee_paise: 3000,
    version: 1,
    created_at: "2026-10-01T08:00:00Z",
    updated_at: "2026-10-01T08:00:00Z",
  },
];

/** Every hub's full record, new hubs included (the admin adds them in mocks/hubadmin.js). */
export const hubDetails = () => mockStore("hubs.details", seed);

/** @param {HubDetail} h */
const summary = (h) => ({ id: h.id, slug: h.slug, name: h.name, status: h.status });

/** @param {string | null} role hubs:read is not held by ngo_coordinator or catalog_editor (hub_ops/permissions.py). */
const cannotReadHubs = (role) => role === "ngo_coordinator" || role === "catalog_editor";

/**
 * The hubs this request may read: admins every hub, others the seed hub (see visibleHubs).
 * @param {Request} request
 */
function readable(request) {
  const ids = new Set(visibleHubs(request).map((h) => h.id));
  return roleOf(request) === "admin" ? hubDetails() : hubDetails().filter((h) => ids.has(h.id));
}

export const hubsHandlers = [
  // GET /v1/hubs → HubsOut, the hubs this person works in (admin: every hub).
  http.get("*/v1/hubs", ({ request }) => {
    const role = roleOf(request);
    if (cannotReadHubs(role))
      return problem(403, "auth.forbidden", "You don't have permission to do this");
    return HttpResponse.json({ items: readable(request).map(summary) });
  }),

  // GET /v1/hubs/{hub_id} → HubOut; 404 hub_ops.hub_not_found, 403 for a hub the caller does not work in.
  http.get("*/v1/hubs/:id", ({ request, params }) => {
    if (cannotReadHubs(roleOf(request)))
      return problem(403, "auth.forbidden", "You don't have permission to do this");
    const hub = hubDetails().find((h) => h.id === params.id);
    if (!hub) return problem(404, "hub_ops.hub_not_found", "No such hub");
    if (!readable(request).some((h) => h.id === hub.id))
      return problem(403, "auth.forbidden", "You don't have permission to do this");
    return HttpResponse.json(hub, { headers: { etag: `"${hub.version}"` } });
  }),
];

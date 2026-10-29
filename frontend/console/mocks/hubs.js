// Hub mocks (M0 hub registry / M6): the pilot has one hub. The seed hub is Kharghar. Shapes are our
// proposal until the hubs endpoints land. Ids are uuids like the real API's (the seed hub's id is the
// backend's SEED_HUB_ID); the readable name is `slug`.
import { HttpResponse, http } from "msw";
import { roleOf } from "./util.js";

/** The backend seed's hub, Kharghar (tools/seed.py `SEED_HUB_ID`). */
export const KHARGHAR_HUB_ID = "0199a000-0000-7000-8000-00000000a001";

/** A made-up second hub, so the KPI Hub filter has something to compare. Not in the backend seed. */
export const TALOJA_HUB_ID = "0199a000-0000-7000-8000-00000000a002";

/** @type {{ id: string, slug: string, name: string, city: string, status: "active" | "planned", location: { lat: number, lng: number } }[]} */
export const HUBS = [
  {
    id: KHARGHAR_HUB_ID,
    slug: "kharghar",
    name: "Kharghar hub",
    city: "Navi Mumbai",
    status: "active",
    location: { lat: 19.033, lng: 73.066 },
  },
  // Only admins see the second hub (see visibleHubs).
  {
    id: TALOJA_HUB_ID,
    slug: "taloja",
    name: "Taloja hub",
    city: "Navi Mumbai",
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

export const hubsHandlers = [
  // GET /v1/hubs → the hubs this person may switch between.
  http.get("*/v1/hubs", ({ request }) =>
    HttpResponse.json({ items: visibleHubs(request), next_cursor: null }),
  ),
  // GET /v1/hubs/{id} (the hub with its timings and ETag) is in hubadmin.js, beside the hub's PATCH.
];

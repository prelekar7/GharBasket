// Legacy Routes mocks (proposals the M7 spec never had), kept only until the Routes page moves to the M7 contract; the
// commit that does so deletes this file. GET /v1/routes?hub_id=&slot_id= and GET /v1/routes/{id}/position.
import { HttpResponse, http } from "msw";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { mockStore, problem } from "./util.js";

const OLD_DAY = "2026-10-06";
/** @param {string} hhmm */
const oldAt = (hhmm) => `${OLD_DAY}T${hhmm}:00+05:30`;

const OLD_HUB = { id: KHARGHAR_HUB_ID, name: "Kharghar hub", lat: 19.033, lng: 73.066 };
const OLD_SUNRISE = { lat: 19.052, lng: 73.078 };
const OLD_KRISHNA = { lat: 19.009, lng: 73.105 };

/** The van leaves Sunrise Towers at 8:07 and is on its way to Krishna Kunj; each poll moves it a little closer. */
const OLD_VAN_START = 0.55;
const oldVanState = () => mockStore("logistics-van-legacy", () => ({ polls: 0 }));

/** @param {number} polls */
function oldVanAt(polls) {
  const t = Math.min(OLD_VAN_START + polls * 0.04, 0.97);
  return {
    lat: OLD_SUNRISE.lat + (OLD_KRISHNA.lat - OLD_SUNRISE.lat) * t,
    lng: OLD_SUNRISE.lng + (OLD_KRISHNA.lng - OLD_SUNRISE.lng) * t,
    at: new Date(Date.parse(oldAt("08:22")) + polls * 15_000).toISOString(),
  };
}

/** @param {{ polls: number }} state */
function oldRouteOne(state) {
  return {
    id: "route-1",
    hub_id: OLD_HUB.id,
    date: OLD_DAY,
    slot_id: "am",
    status: "in_progress",
    published_at: oldAt("07:00"),
    hub: OLD_HUB,
    vehicle: { id: "veh-van-1", name: "Van 1", registration: "MH-46 BX 2041" },
    rider: { id: "p-arjun", name: "Arjun Pawar" },
    van: oldVanAt(state.polls),
    stops: [
      {
        seq: 1,
        kind: "drop",
        society_id: "sunrise-towers",
        society: "Sunrise Towers",
        where: "Gate 2 · Sector 20, Kharghar",
        captain: "Meena tai",
        eta: oldAt("08:05"),
        handed_over_at: oldAt("08:07"),
        crates: 3,
        bags: 46,
        status: "done",
        ...OLD_SUNRISE,
      },
      {
        seq: 2,
        kind: "drop",
        society_id: "krishna-kunj",
        society: "Krishna Kunj",
        where: "Sector 17, New Panvel",
        captain: null,
        eta: oldAt("08:40"),
        handed_over_at: null,
        crates: 1,
        bags: 12,
        status: "current",
        ...OLD_KRISHNA,
      },
    ],
    solver_stats: {
      solver: "ortools",
      distance_m: 18600,
      drive_min: 52,
      crates: 4,
      capacity_crates: 20,
      empty_crates_on_return: true,
      donation_pickups: 2,
    },
  };
}

export const legacyRouteHandlers = [
  // GET /v1/routes?hub_id=&date=&slot_id= (M7 §3). Only the morning slot of Kharghar has a route.
  http.get("*/v1/routes", ({ request }) => {
    const url = new URL(request.url);
    const hub = url.searchParams.get("hub_id") ?? OLD_HUB.id;
    const slot = url.searchParams.get("slot_id") ?? "am";
    const items = hub === OLD_HUB.id && slot === "am" ? [oldRouteOne(oldVanState())] : [];
    return HttpResponse.json({ date: OLD_DAY, items, next_cursor: null });
  }),

  // GET /v1/routes/{id}/position (proposal): one JSON snapshot of the van. The spec's GET /v1/routes/{id}/live is
  // SSE for customers; the console polls this every 15 s through a Server Action instead of holding a stream open.
  http.get("*/v1/routes/:id/position", ({ params }) => {
    if (params.id !== "route-1") return problem(404, "logistics.route_not_found", "No such route");
    const state = oldVanState();
    state.polls += 1;
    return HttpResponse.json(oldVanAt(state.polls));
  }),
];

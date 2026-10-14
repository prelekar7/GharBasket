// Logistics mocks (M7): routes with live van position, deliveries and vehicles for the Kharghar hub.
// Paths follow docs/modules/M7-logistics.md; the ones the spec lacks are marked "proposal" below.
// The story is Tue 6 Oct 2026: van MH-46 BX 2041, rider Arjun Pawar, Sunrise Towers (gate 2, 3 crates, 46 bags,
// captain Meena tai) then Krishna Kunj (1 crate, 12 bags). Coordinates are made up around Kharghar.
import { HttpResponse, http } from "msw";
import { mockStore, problem } from "./util.js";

const DAY = "2026-10-06";
/** @param {string} hhmm */
const at = (hhmm) => `${DAY}T${hhmm}:00+05:30`;

const HUB = { id: "kharghar", name: "Kharghar hub", lat: 19.033, lng: 73.066 };
const SUNRISE = { lat: 19.052, lng: 73.078 };
const KRISHNA = { lat: 19.009, lng: 73.105 };

/** The van leaves Sunrise Towers at 8:07 and is on its way to Krishna Kunj; each poll moves it a little closer. */
const VAN_START = 0.55;
const vanState = () => mockStore("logistics-van", () => ({ polls: 0 }));

/** @param {number} polls */
function vanAt(polls) {
  const t = Math.min(VAN_START + polls * 0.04, 0.97);
  return {
    lat: SUNRISE.lat + (KRISHNA.lat - SUNRISE.lat) * t,
    lng: SUNRISE.lng + (KRISHNA.lng - SUNRISE.lng) * t,
    at: new Date(Date.parse(at("08:22")) + polls * 15_000).toISOString(),
  };
}

/** @param {{ polls: number }} state */
function routeOne(state) {
  return {
    id: "route-1",
    hub_id: HUB.id,
    date: DAY,
    slot_id: "am",
    status: "in_progress",
    published_at: at("07:00"),
    hub: HUB,
    vehicle: { id: "veh-van-1", name: "Van 1", registration: "MH-46 BX 2041" },
    rider: { id: "p-arjun", name: "Arjun Pawar" },
    van: vanAt(state.polls),
    stops: [
      {
        seq: 1,
        kind: "drop",
        society_id: "sunrise-towers",
        society: "Sunrise Towers",
        where: "Gate 2 · Sector 20, Kharghar",
        captain: "Meena tai",
        eta: at("08:05"),
        handed_over_at: at("08:07"),
        crates: 3,
        bags: 46,
        status: "done",
        ...SUNRISE,
      },
      {
        seq: 2,
        kind: "drop",
        society_id: "krishna-kunj",
        society: "Krishna Kunj",
        where: "Sector 17, New Panvel",
        captain: null,
        eta: at("08:40"),
        handed_over_at: null,
        crates: 1,
        bags: 12,
        status: "current",
        ...KRISHNA,
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

export const logisticsHandlers = [
  // GET /v1/routes?hub_id=&date=&slot_id= (M7 §3). Only the morning slot of Kharghar has a route.
  http.get("*/v1/routes", ({ request }) => {
    const url = new URL(request.url);
    const hub = url.searchParams.get("hub_id") ?? HUB.id;
    const slot = url.searchParams.get("slot_id") ?? "am";
    const items = hub === HUB.id && slot === "am" ? [routeOne(vanState())] : [];
    return HttpResponse.json({ date: DAY, items, next_cursor: null });
  }),

  // GET /v1/routes/{id}/position (proposal): one JSON snapshot of the van. The spec's GET /v1/routes/{id}/live is
  // SSE for customers; the console polls this every 15 s through a Server Action instead of holding a stream open.
  http.get("*/v1/routes/:id/position", ({ params }) => {
    if (params.id !== "route-1") return problem(404, "logistics.route_not_found", "No such route");
    const state = vanState();
    state.polls += 1;
    return HttpResponse.json(vanAt(state.polls));
  }),
];

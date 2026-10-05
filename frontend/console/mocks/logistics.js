// Logistics mocks (M7): routes with live van position, deliveries and vehicles for the Kharghar hub.
// Paths follow docs/modules/M7-logistics.md; the ones the spec lacks are marked "proposal" below.
// The story is Tue 6 Oct 2026: van MH-46 BX 2041, rider Arjun Pawar, Sunrise Towers (gate 2, 3 crates, 46 bags,
// captain Meena tai) then Krishna Kunj (1 crate, 12 bags). Coordinates are made up around Kharghar.
import { HttpResponse, http } from "msw";
import { idempotent, mockStore, problem } from "./util.js";

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

// ---- Deliveries: every bag of the day, from handover to the door ------------------------------------------------

/** @typedef {"door" | "lobby" | "with_captain" | "issue"} BagState */

/**
 * @param {string} code @param {string} flat @param {string} society_id @param {string} society
 * @param {BagState} state @param {string} state_text @param {string} proof @param {string} time
 * @param {{ text: string, href?: string } | null} [next]
 */
const bagRow = (code, flat, society_id, society, state, state_text, proof, time, next = null) => ({
  bag: code,
  flat,
  society_id,
  society,
  state,
  state_text,
  proof,
  at: at(time),
  next,
});

/** Flats of Sunrise Towers' three towers, skipping the ones the story names. */
function* flats(/** @type {Set<string>} */ skip) {
  for (let unit = 1; unit <= 3; unit++) {
    for (let floor = 1; floor <= 12; floor++) {
      for (const tower of ["A", "B", "C"]) {
        const flat = `${tower}-${floor}${String(unit).padStart(2, "0")}`;
        if (!skip.has(flat)) yield flat;
      }
    }
  }
}

function buildDeliveries() {
  const named = [
    bagRow(
      "B-218",
      "B-1104",
      "sunrise-towers",
      "Sunrise Towers",
      "issue",
      "Eggs broken",
      "Photo by Meena tai",
      "08:31",
      {
        text: "Approve refund",
        href: "/refunds",
      },
    ),
    bagRow(
      "B-230",
      "C-302",
      "sunrise-towers",
      "Sunrise Towers",
      "lobby",
      "In the lobby",
      "No one home, rang twice",
      "08:44",
      {
        text: "Back to the hub at 7 PM if not collected",
      },
    ),
    bagRow(
      "B-221",
      "B-603",
      "sunrise-towers",
      "Sunrise Towers",
      "lobby",
      "In the lobby",
      "Family chose lobby pickup",
      "08:15",
    ),
    bagRow(
      "B-224",
      "A-905",
      "sunrise-towers",
      "Sunrise Towers",
      "lobby",
      "In the lobby",
      "Family chose lobby pickup",
      "08:16",
    ),
    bagRow(
      "B-227",
      "A-407",
      "sunrise-towers",
      "Sunrise Towers",
      "lobby",
      "In the lobby",
      "Family chose lobby pickup",
      "08:18",
    ),
    bagRow(
      "B-233",
      "C-1002",
      "sunrise-towers",
      "Sunrise Towers",
      "lobby",
      "In the lobby",
      "Family chose lobby pickup",
      "08:21",
    ),
  ];
  const taken = new Set(named.map((b) => b.flat));
  const free = [...flats(taken)];
  const taken_codes = new Set(named.map((b) => b.bag));
  /** @type {ReturnType<typeof bagRow>[]} */
  const door = [];
  for (let n = 201; door.length < 40; n++) {
    const code = `B-${n}`;
    if (taken_codes.has(code)) continue;
    const minute = 9 + Math.floor(door.length * 0.55);
    door.push(
      bagRow(
        code,
        /** @type {string} */ (free[door.length]),
        "sunrise-towers",
        "Sunrise Towers",
        "door",
        "At the door",
        "Scanned by Meena tai",
        `08:${String(minute).padStart(2, "0")}`,
      ),
    );
  }
  const krishnaFlats = [
    "B-205",
    "A-101",
    "A-203",
    "A-306",
    "B-102",
    "B-301",
    "B-404",
    "C-105",
    "C-207",
    "C-308",
    "D-110",
    "D-212",
  ];
  const krishna = krishnaFlats.map((flat, i) =>
    bagRow(
      `K-${101 + i}`,
      flat,
      "krishna-kunj",
      "Krishna Kunj",
      "with_captain",
      "With the captain",
      i === 4 ? "Methi refunded ₹20 to wallet" : "Crate K-1 handed over",
      "08:42",
      { text: "Door scan pending" },
    ),
  );
  return [...named, ...door, ...krishna];
}

const deliveries = () => mockStore("logistics-deliveries", buildDeliveries);

/** @param {ReturnType<typeof bagRow>[]} items */
function summarise(items) {
  const count = (/** @type {BagState} */ s) => items.filter((b) => b.state === s).length;
  return {
    at_door: count("door"),
    in_lobby: count("lobby"),
    problems: count("issue"),
    lobby_collect_by: at("19:00"),
    with_captain: [
      {
        society_id: "krishna-kunj",
        society: "Krishna Kunj",
        bags: count("with_captain"),
        handed_over_at: at("08:42"),
      },
    ],
  };
}

// ---- Vehicles ------------------------------------------------------------------------------------------------------

/** @typedef {"van" | "ev3w" | "bike"} VehicleKind */
const KIND_NAME = { van: "Van", ev3w: "Three-wheeler", bike: "Two-wheeler" };

/** Placeholder fleet: the hub's real vehicles replace these when they are entered. */
const vehicles = () =>
  mockStore("logistics-vehicles", () => [
    {
      id: "veh-van-1",
      hub_id: HUB.id,
      kind: /** @type {VehicleKind} */ ("van"),
      name: "Van 1",
      registration: "MH-46 BX 2041",
      capacity_kg: 600,
      capacity_crates: 20,
      status: /** @type {"on_route" | "available" | "retired"} */ ("on_route"),
      assignment: /** @type {{ rider: string, route_id: string, route_label: string } | null} */ ({
        rider: "Arjun Pawar",
        route_id: "route-1",
        route_label: "route 1",
      }),
    },
    {
      id: "veh-ev3w-1",
      hub_id: HUB.id,
      kind: /** @type {VehicleKind} */ ("ev3w"),
      name: "Three-wheeler 1",
      registration: "MH-46 CD 7310",
      capacity_kg: 350,
      capacity_crates: 12,
      status: "available",
      assignment: null,
    },
  ]);

const REGISTRATION = /^[A-Z]{2}-\d{2} [A-Z]{1,3} \d{4}$/;

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
  // GET /v1/deliveries?hub_id=&date= (proposal; M7 only lists the per-order write endpoints). One row per bag with its
  // proof. Customer phone numbers are never part of it.
  http.get("*/v1/deliveries", ({ request }) => {
    const hub = new URL(request.url).searchParams.get("hub_id") ?? HUB.id;
    const items = hub === HUB.id ? deliveries() : [];
    return HttpResponse.json({
      date: DAY,
      as_of: at("09:40"),
      summary: summarise(items),
      items,
      next_cursor: null,
    });
  }),

  // GET and POST /v1/hubs/{id}/vehicles (M7 §3). `registration` is our addition to the spec's columns.
  http.get("*/v1/hubs/:hub/vehicles", ({ params }) =>
    HttpResponse.json({ items: params.hub === HUB.id ? vehicles() : [], next_cursor: null }),
  ),
  http.post("*/v1/hubs/:hub/vehicles", ({ request, params }) =>
    idempotent(request, (body) => {
      if (params.hub !== HUB.id) return problem(404, "hubs.not_found", "No such hub");
      const registration = String(body?.registration ?? "")
        .trim()
        .toUpperCase()
        .replace(/\s+/g, " ");
      const kg = Number(body?.capacity_kg);
      const crates = Number(body?.capacity_crates);
      /** @type {{ field: string, message: string }[]} */
      const errors = [];
      if (!(body?.kind in KIND_NAME)) errors.push({ field: "kind", message: "Pick a kind" });
      if (!REGISTRATION.test(registration))
        errors.push({ field: "registration", message: "Use the plate format, like MH-46 BX 2041" });
      if (!Number.isInteger(kg) || kg < 1 || kg > 5000)
        errors.push({ field: "capacity_kg", message: "Enter whole kilograms, 1 to 5000" });
      if (!Number.isInteger(crates) || crates < 1 || crates > 100)
        errors.push({ field: "capacity_crates", message: "Enter whole crates, 1 to 100" });
      if (errors.length)
        return problem(422, "request.invalid", "Check the vehicle details", { errors });
      if (vehicles().some((v) => v.registration === registration)) {
        return problem(
          409,
          "logistics.vehicle_exists",
          "A vehicle with this registration already exists",
        );
      }
      const n = vehicles().filter((v) => v.kind === body.kind).length + 1;
      const vehicle = {
        id: `veh-${body.kind}-${n}`,
        hub_id: HUB.id,
        kind: /** @type {VehicleKind} */ (body.kind),
        name: `${KIND_NAME[/** @type {VehicleKind} */ (body.kind)]} ${n}`,
        registration,
        capacity_kg: kg,
        capacity_crates: crates,
        status: /** @type {const} */ ("available"),
        assignment: null,
      };
      vehicles().push(vehicle);
      return HttpResponse.json(vehicle, { status: 201 });
    }),
  ),
];

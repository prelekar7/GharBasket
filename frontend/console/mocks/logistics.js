// Logistics mocks: the real M7 contract (docs/modules/M7-logistics.md §3.1 to §3.4) for the console's Vehicles, Routes and
// Deliveries pages. Nothing in M7 is built yet, so these follow the spec text: paths, fields, status codes and problem codes.
//   GET  /v1/hubs/{id}/vehicles?active=&kind=              { items: [Vehicle] }, by label                    (§3.1)
//   POST /v1/hubs/{id}/vehicles                            201 Vehicle; Idempotency-Key                      (§3.1)
//   GET  /v1/hubs/{id}/route-plans?date=&kind=             { items: [RoutePlan without routes] }, newest first (§3.2)
//   POST /v1/hubs/{id}/route-plans { date, kind }          202 RoutePlan (queued); 409 logistics.solve_running  (§3.2)
//   GET  /v1/route-plans/{id}                              RoutePlan with routes; a queued plan moves on as it is polled
//   POST /v1/route-plans/{id}/publish { accept_problems? } 200 RoutePlan (published); 409 plan_has_problems       (§3.2)
//   GET  /v1/hubs/{id}/routes?date=&kind=&status=          { items: [RouteSummary] }, by depart, label          (§3.3)
//   GET  /v1/routes/{id}                                   RouteDetail, ETag = version                          (§3.3)
//   GET  /v1/routes/{id}/live                              text/event-stream: a `snapshot` event first           (§3.3)
//   GET  /v1/hubs/{id}/deliveries?date=&status=&cursor=    Page[Delivery], the exceptions board                  (§3.4)
//   POST /v1/deliveries/{order_id}/resolve                 hub manager triage: redeliver or refund               (§3.4)
// The story is today in Kharghar: van EV-1 with rider Arjun Pawar is out; Sunrise Towers (gate 2, 3 crates, 46 bags,
// captain Meena tai) was handed over at 8:07, Krishna Kunj (1 crate, 12 bags) is next. Tomorrow's plan is solved and waits
// to be published. Coordinates are made up around Kharghar. The spec has no field for the hub's location or the flat of a
// delivery; the console reads the hub from GET /v1/hubs/{id} and names societies from GET /v1/societies.
import { HttpResponse, http } from "msw";
import { deny, istDay } from "./adminShared.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { SLOT_ID, SOCIETY_ID } from "./ids.js";
import { fieldError, idempotent, mockStore, mockUuid, problem } from "./util.js";

const HUB_ID = KHARGHAR_HUB_ID;
const READERS = ["admin", "hub_manager", "franchise_owner"];
const WRITERS = ["admin", "hub_manager"];
/** Arjun Pawar's user id, the rider the partner mock verifies. */
const ARJUN = { user_id: mockUuid("b310", 2), name: "Arjun Pawar" };

const today = () => istDay(0, 0, 0).slice(0, 10);
const tomorrow = () => istDay(1, 0, 0).slice(0, 10);
/** @param {string} date @param {string} hhmm */
const at = (date, hhmm) => `${date}T${hhmm}:00+05:30`;

const PLACES = {
  sunrise: { id: SOCIETY_ID["sunrise-towers"], name: "Sunrise Towers", lat: 19.052, lng: 73.078 },
  krishna: { id: SOCIETY_ID["krishna-kunj"], name: "Krishna Kunj", lat: 19.009, lng: 73.105 },
  ganesh: {
    id: SOCIETY_ID["shree-ganesh-chs"],
    name: "Shree Ganesh CHS",
    lat: 19.024,
    lng: 73.058,
  },
  panchavati: {
    id: SOCIETY_ID["panchavati-residency"],
    name: "Panchavati Residency",
    lat: 19.041,
    lng: 73.092,
  },
};
const MORNING = { id: SLOT_ID.am, label: "8 to 10 AM", start: "08:00", end: "10:00" };

// ---- Vehicles (§3.1) ------------------------------------------------------------------------------------------------

/** @typedef {"cargo_3w" | "two_wheeler"} VehicleKind */
/**
 * @typedef {{ id: string, hub_id: string, label: string, kind: VehicleKind, registration: string | null,
 *   capacity_kg: number, capacity_crates: number, capacity_tiffins: number,
 *   default_rider: { user_id: string, name: string } | null, active: boolean, version: number, created_at: string }} Vehicle
 */
const DEFAULTS = /** @type {const} */ ({
  cargo_3w: { capacity_kg: 400, capacity_crates: 24, capacity_tiffins: 0 },
  two_wheeler: { capacity_kg: 40, capacity_crates: 0, capacity_tiffins: 30 },
});

const vehicles = () =>
  mockStore("logistics-vehicles-v2", () => {
    /** @type {Vehicle[]} */
    const list = [
      {
        id: mockUuid("b400", 1),
        hub_id: HUB_ID,
        label: "EV-1",
        kind: "cargo_3w",
        registration: "MH46AB1234",
        ...DEFAULTS.cargo_3w,
        default_rider: ARJUN,
        active: true,
        version: 1,
        created_at: istDay(-30, 9, 0),
      },
      {
        id: mockUuid("b400", 2),
        hub_id: HUB_ID,
        label: "EV-2",
        kind: "cargo_3w",
        registration: "MH46CD7310",
        capacity_kg: 350,
        capacity_crates: 20,
        capacity_tiffins: 0,
        default_rider: null,
        active: true,
        version: 1,
        created_at: istDay(-30, 9, 5),
      },
      {
        id: mockUuid("b400", 3),
        hub_id: HUB_ID,
        label: "TW-1",
        kind: "two_wheeler",
        registration: null,
        ...DEFAULTS.two_wheeler,
        default_rider: null,
        active: false,
        version: 2,
        created_at: istDay(-20, 9, 0),
      },
    ];
    return list;
  });

// ---- Route plans, routes and stops (§3.2, §3.3) ---------------------------------------------------------------------

/** @typedef {"pending" | "arrived" | "handed_over" | "skipped"} StopStatus */
/**
 * @typedef {{ id: string, seq: number, place: keyof typeof PLACES, eta: string, status: StopStatus,
 *   crates: number, bags: number, captain: string | null, arrived_at: string | null, handed_over_at: string | null,
 *   eta_now: string | null }} StopRec
 * @typedef {{ id: string, plan_id: string, trip: number, status: "draft" | "published" | "in_progress" | "completed" | "cancelled",
 *   vehicle_label: string, rider: { user_id: string, name: string } | null, depart: string, return_by: string, km: number,
 *   stops: StopRec[], started_at: string | null, completed_at: string | null, version: number }} RouteRec
 * @typedef {{ id: string, delivery_date: string, status: "queued" | "solving" | "solved" | "published" | "failed" | "superseded",
 *   trigger: "job" | "manual", source: "ml" | "fallback_previous" | "fallback_nearest" | null,
 *   problems: string[], created_at: string, published_at: string | null, routes: RouteRec[] }} PlanRec
 */

/**
 * @param {number} n @param {number} seq @param {keyof typeof PLACES} place @param {string} eta
 * @param {number} crates @param {number} bags @param {Partial<StopRec>} [rest]
 * @returns {StopRec}
 */
const stop = (n, seq, place, eta, crates, bags, rest = {}) => ({
  id: mockUuid("b430", n),
  seq,
  place,
  eta,
  status: "pending",
  crates,
  bags,
  captain: place === "krishna" ? null : "Meena tai",
  arrived_at: null,
  handed_over_at: null,
  eta_now: null,
  ...rest,
});

const plans = () =>
  mockStore("logistics-plans", () => {
    const d0 = today();
    const d1 = tomorrow();
    /** @type {PlanRec[]} */
    const list = [
      {
        id: mockUuid("b410", 1),
        delivery_date: d0,
        status: "published",
        trigger: "job",
        source: "ml",
        problems: [],
        created_at: istDay(-1, 23, 15),
        published_at: istDay(-1, 23, 16),
        routes: [
          {
            id: mockUuid("b420", 1),
            plan_id: mockUuid("b410", 1),
            trip: 1,
            status: "in_progress",
            vehicle_label: "EV-1",
            rider: ARJUN,
            depart: "06:30",
            return_by: "11:30",
            km: 18.6,
            started_at: at(d0, "06:32"),
            completed_at: null,
            version: 4,
            stops: [
              stop(1, 1, "sunrise", "08:05", 3, 46, {
                status: "handed_over",
                arrived_at: at(d0, "08:02"),
                handed_over_at: at(d0, "08:07"),
              }),
              stop(2, 2, "krishna", "08:40", 1, 12, { eta_now: "08:47" }),
            ],
          },
        ],
      },
      {
        id: mockUuid("b410", 2),
        delivery_date: d1,
        status: "solved",
        trigger: "job",
        source: "fallback_previous",
        problems: ["van 2: no rider set"],
        created_at: istDay(0, 7, 0),
        published_at: null,
        routes: [
          {
            id: mockUuid("b420", 2),
            plan_id: mockUuid("b410", 2),
            trip: 1,
            status: "draft",
            vehicle_label: "EV-1",
            rider: ARJUN,
            depart: "06:30",
            return_by: "11:30",
            km: 17.9,
            started_at: null,
            completed_at: null,
            version: 1,
            stops: [stop(3, 1, "sunrise", "08:05", 3, 44), stop(4, 2, "krishna", "08:40", 1, 11)],
          },
          {
            id: mockUuid("b420", 3),
            plan_id: mockUuid("b410", 2),
            trip: 1,
            status: "draft",
            vehicle_label: "EV-2",
            rider: null,
            depart: "06:30",
            return_by: "11:30",
            km: 9.4,
            started_at: null,
            completed_at: null,
            version: 1,
            stops: [stop(5, 1, "ganesh", "08:20", 2, 21), stop(6, 2, "panchavati", "08:45", 2, 19)],
          },
        ],
      },
    ];
    return list;
  });

/** @param {RouteRec} r @param {string} date */
const routeSummary = (r, date) => {
  const v = vehicles().find((x) => x.label === r.vehicle_label);
  return {
    id: r.id,
    plan_id: r.plan_id,
    hub_id: HUB_ID,
    delivery_date: date,
    kind: "box",
    trip: r.trip,
    status: r.status,
    vehicle: { id: v?.id ?? "", label: r.vehicle_label, kind: v?.kind ?? "cargo_3w" },
    rider: r.rider,
    depart: r.depart,
    return_by: r.return_by,
    stops_total: r.stops.length,
    stops_done: r.stops.filter((s) => s.status === "handed_over" || s.status === "skipped").length,
    crates: r.stops.reduce((n, s) => n + s.crates, 0),
    bags: r.stops.reduce((n, s) => n + s.bags, 0),
    km: r.km,
    started_at: r.started_at,
    completed_at: r.completed_at,
    version: r.version,
  };
};

/** @param {StopRec} s */
const stopOut = (s) => {
  const p = PLACES[s.place];
  return {
    id: s.id,
    seq: s.seq,
    kind: "drop",
    society: { id: p.id, name: p.name, location: { lat: p.lat, lng: p.lng } },
    slot: MORNING,
    eta: s.eta,
    late_min: 0,
    eta_now: s.eta_now,
    status: s.status,
    crates: s.crates,
    bags: s.bags,
    redelivery_bags: 0,
    empties_expected: s.place === "sunrise" ? 2 : 0,
    captain: s.captain ? { display_name: s.captain, photo_url: null } : null,
    rider_mode: !s.captain,
    handover_code: null,
    navigate_url: `https://www.google.com/maps/dir/?api=1&destination=${p.lat},${p.lng}`,
    arrived_at: s.arrived_at,
    handed_over_at: s.handed_over_at,
  };
};

/** @param {PlanRec} plan @param {boolean} withRoutes */
function planOut(plan, withRoutes) {
  const solved = plan.status !== "queued" && plan.status !== "solving";
  return {
    id: plan.id,
    hub_id: HUB_ID,
    delivery_date: plan.delivery_date,
    kind: "box",
    status: plan.status,
    trigger: plan.trigger,
    source: solved ? plan.source : null,
    distance_source: solved ? (plan.source === "ml" ? "osrm" : "haversine") : null,
    model: solved && plan.source === "ml" ? "cvrptw-waves-v0" : null,
    summary: solved
      ? {
          stops: plan.routes.reduce((n, r) => n + r.stops.length, 0),
          orders: plan.routes.reduce((n, r) => n + r.stops.reduce((m, s) => m + s.bags, 0), 0),
          vehicles: new Set(plan.routes.map((r) => r.vehicle_label)).size,
          routes: plan.routes.length,
          km: Math.round(plan.routes.reduce((n, r) => n + r.km, 0) * 10) / 10,
          cost_paise: 412_000,
          on_time: 1,
          late_min: 0,
          problems: plan.problems,
        }
      : null,
    seconds: solved ? 7.9 : null,
    error: null,
    routes: withRoutes ? plan.routes.map((r) => routeSummary(r, plan.delivery_date)) : undefined,
    requested_by:
      plan.trigger === "manual" ? { user_id: mockUuid("b310", 90), name: "Vikram Desai" } : null,
    created_at: plan.created_at,
    published_at: plan.published_at,
  };
}

/** A queued plan moves on each time it is read: queued, then solving, then solved. @param {PlanRec} plan */
function advance(plan) {
  if (plan.status === "queued") plan.status = "solving";
  else if (plan.status === "solving") plan.status = "solved";
}

/** The routes a date shows: those of its newest plan that is solved or published. @param {string} date */
function routesOf(date) {
  const plan = plans()
    .filter((p) => p.delivery_date === date && (p.status === "solved" || p.status === "published"))
    .at(-1);
  return plan ? plan.routes : [];
}

/** @param {string} id */
function findRoute(id) {
  for (const plan of plans()) {
    const route = plan.routes.find((x) => x.id === id);
    if (route) return { plan, route };
  }
  return null;
}

// ---- Van position and live stream ------------------------------------------------------------------------------------

const vanState = () => mockStore("logistics-van", () => ({ polls: 0 }));
/** The van leaves Sunrise Towers at 8:07 and drives to Krishna Kunj; every snapshot moves it closer. @param {number} polls */
function vanAt(polls) {
  const t = Math.min(0.55 + polls * 0.04, 0.97);
  const a = PLACES.sunrise;
  const b = PLACES.krishna;
  return {
    at: new Date(Date.parse(at(today(), "08:22")) + polls * 15_000).toISOString(),
    lat: a.lat + (b.lat - a.lat) * t,
    lng: a.lng + (b.lng - a.lng) * t,
    heading: 160,
    age_s: 8,
  };
}

// ---- Deliveries (§3.4) -----------------------------------------------------------------------------------------------

/**
 * @typedef {{ order_id: string, attempt: 1 | 2, delivery_date: string, society_id: string,
 *   status: "scheduled" | "on_route" | "with_captain" | "held" | "delivered" | "undelivered" | "redelivery" | "refunded" | "triage",
 *   reason: string | null, hold_until: string | null, proof: "scan" | "otp" | "photo" | null,
 *   by: "captain" | "rider" | "hub" | "system" | null, at: string | null }} Delivery
 */

/** What each order paid, for the refund bound of a triage decision (not part of the Delivery). */
const paid = () =>
  mockStore("logistics-paid", () => /** @type {Map<string, number>} */ (new Map()));

function buildDeliveries() {
  const d = today();
  /** @type {Delivery[]} */
  const list = [];
  let n = 0;
  /** @param {Partial<Delivery> & { society: keyof typeof PLACES }} row */
  const add = ({ society, ...rest }) => {
    n += 1;
    const order_id = mockUuid("b440", n);
    paid().set(order_id, 60_000 + (n % 5) * 5_000);
    list.push({
      order_id,
      attempt: 1,
      delivery_date: d,
      society_id: PLACES[society].id,
      status: "delivered",
      reason: null,
      hold_until: null,
      proof: "scan",
      by: "captain",
      at: at(d, "08:10"),
      ...rest,
    });
  };
  add({
    society: "sunrise",
    status: "refunded",
    reason: "damaged",
    by: "system",
    proof: null,
    at: at(d, "08:31"),
  });
  add({
    society: "sunrise",
    status: "triage",
    reason: "wrong_address",
    proof: null,
    at: at(d, "08:36"),
  });
  add({
    society: "sunrise",
    status: "held",
    reason: "not_home",
    hold_until: "20:00",
    proof: null,
    at: at(d, "08:44"),
  });
  for (let i = 0; i < 40; i++) {
    const minute = 9 + Math.floor(i * 0.55);
    add({ society: "sunrise", at: at(d, `08:${String(minute).padStart(2, "0")}`) });
  }
  for (let i = 0; i < 12; i++) {
    add({ society: "krishna", status: "with_captain", proof: null, by: null, at: at(d, "08:42") });
  }
  return list;
}

const deliveries = () => mockStore("logistics-deliveries-v2", buildDeliveries);

// ---- Handlers --------------------------------------------------------------------------------------------------------

/** @param {unknown} v @param {number} min @param {number} max */
const whole = (v, min, max) => Number.isInteger(v) && Number(v) >= min && Number(v) <= max;

export const logisticsHandlers = [
  // ---- Vehicles
  http.get("*/v1/hubs/:hub/vehicles", ({ request, params }) => {
    const refused = deny(request, READERS);
    if (refused) return refused;
    const url = new URL(request.url);
    const active = url.searchParams.get("active");
    const kind = url.searchParams.get("kind");
    const items =
      params.hub === HUB_ID
        ? vehicles()
            .filter(
              (v) => (active === null || String(v.active) === active) && (!kind || v.kind === kind),
            )
            .toSorted((a, b) => a.label.localeCompare(b.label, "en", { numeric: true }))
        : [];
    return HttpResponse.json({ items });
  }),

  http.post(
    "*/v1/hubs/:hub/vehicles",
    ({ request, params }) =>
      deny(request, WRITERS) ??
      idempotent(request, (body) => {
        if (params.hub !== HUB_ID) return problem(404, "hubs.not_found", "No such hub");
        const label = String(body?.label ?? "").trim();
        /** @type {ReturnType<typeof fieldError>[]} */
        const errors = [];
        if (label.length < 1 || label.length > 20) {
          errors.push(fieldError("label", "Use 1 to 20 characters", "string_length"));
        }
        if (!(body?.kind in DEFAULTS)) errors.push(fieldError("kind", "Pick a kind", "enum"));
        if (body?.capacity_kg != null && !whole(body.capacity_kg, 10, 2000)) {
          errors.push(fieldError("capacity_kg", "Enter whole kilograms, 10 to 2000"));
        }
        if (body?.capacity_crates != null && !whole(body.capacity_crates, 0, 100)) {
          errors.push(fieldError("capacity_crates", "Enter whole crates, 0 to 100"));
        }
        if (body?.capacity_tiffins != null && !whole(body.capacity_tiffins, 0, 100)) {
          errors.push(fieldError("capacity_tiffins", "Enter whole tiffins, 0 to 100"));
        }
        if (errors.length)
          return problem(422, "request.invalid", "Check the vehicle details", { errors });
        if (vehicles().some((v) => v.label.toLowerCase() === label.toLowerCase())) {
          return problem(
            409,
            "logistics.vehicle_label_taken",
            "A vehicle with this label already exists",
          );
        }
        /** @type {Vehicle["default_rider"]} */
        let default_rider = null;
        if (body?.default_rider_user_id) {
          if (body.default_rider_user_id !== ARJUN.user_id) {
            return problem(
              422,
              "logistics.rider_invalid",
              "That person is not a rider of this hub",
            );
          }
          default_rider = ARJUN;
        }
        const kind = /** @type {VehicleKind} */ (body.kind);
        /** @type {Vehicle} */
        const vehicle = {
          id: mockUuid("b400", vehicles().length + 1),
          hub_id: HUB_ID,
          label,
          kind,
          registration: String(body?.registration ?? "").trim() || null,
          capacity_kg: body.capacity_kg ?? DEFAULTS[kind].capacity_kg,
          capacity_crates: body.capacity_crates ?? DEFAULTS[kind].capacity_crates,
          capacity_tiffins: body.capacity_tiffins ?? DEFAULTS[kind].capacity_tiffins,
          default_rider,
          active: true,
          version: 1,
          created_at: new Date().toISOString(),
        };
        vehicles().push(vehicle);
        return HttpResponse.json(vehicle, { status: 201 });
      }),
  ),

  // ---- Route plans
  http.get("*/v1/hubs/:hub/route-plans", ({ request, params }) => {
    const refused = deny(request, READERS);
    if (refused) return refused;
    const url = new URL(request.url);
    const date = url.searchParams.get("date") ?? today();
    const kind = url.searchParams.get("kind");
    const items =
      params.hub === HUB_ID && (!kind || kind === "box")
        ? plans()
            .filter((p) => p.delivery_date === date)
            .toReversed()
            .map((p) => planOut(p, false))
        : [];
    return HttpResponse.json({ items });
  }),

  http.post(
    "*/v1/hubs/:hub/route-plans",
    ({ request, params }) =>
      deny(request, ["hub_manager"]) ??
      idempotent(request, (body) => {
        if (params.hub !== HUB_ID) return problem(404, "hubs.not_found", "No such hub");
        if (body?.kind !== "box") {
          return problem(422, "request.invalid", "Pick a kind", {
            errors: [fieldError("kind", "Only box plans can be solved for now", "enum")],
          });
        }
        if (body?.date !== today() && body?.date !== tomorrow()) {
          return problem(422, "logistics.date_out_of_range", "Solve for today or tomorrow");
        }
        const running = plans().some(
          (p) => p.delivery_date === body.date && (p.status === "queued" || p.status === "solving"),
        );
        if (running) {
          return problem(
            409,
            "logistics.solve_running",
            "A solve is already running for this date",
          );
        }
        // The mock solve is the previous-day fallback: the routes of the newest plan for that date, as a new draft.
        const base = plans()
          .filter((p) => p.delivery_date === body.date && p.routes.length)
          .at(-1);
        const n = plans().length + 1;
        /** @type {PlanRec} */
        const plan = {
          id: mockUuid("b410", n),
          delivery_date: body.date,
          status: "queued",
          trigger: "manual",
          source: "fallback_previous",
          problems: [],
          created_at: new Date().toISOString(),
          published_at: null,
          routes: (base?.routes ?? []).map((r, i) => ({
            ...structuredClone(r),
            id: mockUuid("b420", n * 10 + i),
            plan_id: mockUuid("b410", n),
            status: /** @type {const} */ ("draft"),
            started_at: null,
            version: 1,
          })),
        };
        plans().push(plan);
        return HttpResponse.json(planOut(plan, true), { status: 202 });
      }),
  ),

  http.get("*/v1/route-plans/:id", ({ request, params }) => {
    const refused = deny(request, READERS);
    if (refused) return refused;
    const plan = plans().find((p) => p.id === params.id);
    if (!plan) return problem(404, "logistics.plan_not_found", "No such plan");
    advance(plan);
    return HttpResponse.json(planOut(plan, true));
  }),

  http.post(
    "*/v1/route-plans/:id/publish",
    ({ request, params }) =>
      deny(request, ["hub_manager"]) ??
      idempotent(request, (body) => {
        const plan = plans().find((p) => p.id === params.id);
        if (!plan) return problem(404, "logistics.plan_not_found", "No such plan");
        if (plan.status !== "solved") {
          return problem(409, "logistics.plan_not_solved", "Only a solved plan can be published");
        }
        if (plan.problems.length && body?.accept_problems !== true) {
          return problem(
            409,
            "logistics.plan_has_problems",
            "This plan has problems to accept first",
            {
              problems: plan.problems,
            },
          );
        }
        const old = plans().find(
          (p) => p.delivery_date === plan.delivery_date && p.status === "published",
        );
        if (old?.routes.some((r) => r.status === "in_progress" || r.status === "completed")) {
          return problem(
            409,
            "logistics.routes_started",
            "A route of the published plan has started",
          );
        }
        if (old) {
          old.status = "superseded";
          for (const r of old.routes) r.status = "cancelled";
        }
        plan.status = "published";
        plan.published_at = new Date().toISOString();
        for (const r of plan.routes) r.status = "published";
        return HttpResponse.json(planOut(plan, true));
      }),
  ),

  // ---- Routes
  http.get("*/v1/hubs/:hub/routes", ({ request, params }) => {
    const refused = deny(request, READERS);
    if (refused) return refused;
    const url = new URL(request.url);
    const date = url.searchParams.get("date") ?? today();
    const status = url.searchParams.get("status");
    const items =
      params.hub === HUB_ID && (url.searchParams.get("kind") ?? "box") === "box"
        ? routesOf(date)
            .filter((r) => !status || r.status === status)
            .map((r) => routeSummary(r, date))
            .toSorted(
              (a, b) =>
                a.depart.localeCompare(b.depart) || a.vehicle.label.localeCompare(b.vehicle.label),
            )
        : [];
    return HttpResponse.json({ items });
  }),

  http.get("*/v1/routes/:id", ({ request, params }) => {
    const refused = deny(request, READERS);
    if (refused) return refused;
    const found = findRoute(String(params.id));
    if (!found) return problem(404, "logistics.route_not_found", "No such route");
    const { plan, route } = found;
    return HttpResponse.json(
      {
        ...routeSummary(route, plan.delivery_date),
        stops: route.stops.map(stopOut),
        source: plan.source,
        left_behind: [],
      },
      { headers: { etag: `"${route.version}"` } },
    );
  }),

  // GET /v1/routes/{id}/live (§3.3): SSE. The mock sends the `snapshot` event and ends the stream, which is all the
  // console reads (it asks again every 15 seconds); the real stream stays open and sends `position` and `stop` events.
  http.get("*/v1/routes/:id/live", ({ request, params }) => {
    const refused = deny(request, READERS);
    if (refused) return refused;
    const found = findRoute(String(params.id));
    if (!found) return problem(404, "logistics.route_not_found", "No such route");
    const { route } = found;
    const running = route.status === "in_progress";
    if (running) vanState().polls += 1;
    const snapshot = {
      route_id: route.id,
      status: route.status,
      position: running ? vanAt(vanState().polls) : null,
      stops: route.stops.map((s) => ({ seq: s.seq, status: s.status, eta_now: s.eta_now })),
    };
    return new HttpResponse(`event: snapshot\ndata: ${JSON.stringify(snapshot)}\n\n`, {
      headers: { "content-type": "text/event-stream", "cache-control": "no-store" },
    });
  }),

  // ---- Deliveries
  http.get("*/v1/hubs/:hub/deliveries", ({ request, params }) => {
    const refused = deny(request, READERS);
    if (refused) return refused;
    const url = new URL(request.url);
    const date = url.searchParams.get("date") ?? today();
    const status = url.searchParams.get("status");
    const items =
      params.hub === HUB_ID
        ? deliveries().filter((x) => x.delivery_date === date && (!status || x.status === status))
        : [];
    return HttpResponse.json({ items, next_cursor: null });
  }),

  http.post(
    "*/v1/deliveries/:order_id/resolve",
    ({ request, params }) =>
      deny(request, WRITERS) ??
      idempotent(request, (body) => {
        const d = deliveries().find((x) => x.order_id === params.order_id);
        if (!d) return problem(404, "logistics.delivery_not_found", "No such delivery");
        /** @type {ReturnType<typeof fieldError>[]} */
        const errors = [];
        if (body?.outcome !== "redeliver" && body?.outcome !== "refund") {
          errors.push(fieldError("outcome", "Pick redeliver or refund", "enum"));
        }
        if (!String(body?.note ?? "").trim())
          errors.push(fieldError("note", "Say why", "string_too_short"));
        if (errors.length) return problem(422, "request.invalid", "Check the decision", { errors });
        if (d.status !== "undelivered" && d.status !== "triage") {
          return problem(
            409,
            "logistics.not_undelivered",
            "This order is not waiting for a decision",
          );
        }
        if (body.outcome === "redeliver") {
          if (d.attempt !== 1) {
            return problem(422, "request.invalid", "The free redelivery was already used", {
              errors: [fieldError("outcome", "A second miss can only be refunded", "value_error")],
            });
          }
          Object.assign(d, {
            status: "redelivery",
            attempt: 2,
            by: "hub",
            at: new Date().toISOString(),
          });
        } else {
          const max = paid().get(d.order_id) ?? 0;
          const amount = body.refund_paise ?? max;
          if (!whole(amount, 0, max)) {
            return problem(422, "logistics.refund_invalid", "That is more than is left to refund", {
              max_paise: max,
            });
          }
          Object.assign(d, { status: "refunded", by: "hub", at: new Date().toISOString() });
        }
        return HttpResponse.json(d);
      }),
  ),
];

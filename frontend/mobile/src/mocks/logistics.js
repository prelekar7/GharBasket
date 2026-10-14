// M7 logistics mocks for the rider, in the real shapes of docs/modules/M7-logistics.md §3.3 and
// §3.4 (frozen; ADR-0026): the rider's routes for a day, the route with its stops, start, arrive,
// skip and complete, the rider-mode handover and its doors (deliveries, proof, undelivered), return-leg
// pickups (§3.6 is an outline: the request follows it, the answer is ours) and the GPS pings, plus M8
// earnings. Ids are UUIDs shared with mocks/packer.js; the captain's handover is mocks/captain.js.
// Every write needs an Idempotency-Key and replays the first answer, as the offline outbox relies on.
import { HttpResponse, http } from "msw";
import { todayIst } from "@/features/subscriptions/format.js";
import { caller } from "./handlers.js";
import { HUB_ID } from "./packer.js";
import { mockPdfUrl } from "./pdf.js";
import { ROUTE_ID } from "./tracking.js";

// Endpoints shared with other roles (handover, doors, earnings) answer here only for a rider.
const otherRole = (/** @type {Request} */ request) => {
  const role = request.headers.get("x-active-role");
  return !!role && !role.startsWith("rider");
};

const KEY = "gb-mock-logistics-v2";
/** Mock-only switch for the "route didn't load" state: localStorage[FAIL_KEY] = "1". */
export const FAIL_KEY = "gb-mock-routes-fail";
/** The rider at hub-thane-1 (98000 00003) has no route today: the empty state. */
const NO_ROUTE_RIDER = "+919800000003";

/** Ids are UUIDs: the namespace picks the kind, the number the row (as in mocks/packer.js). @param {string} ns @param {number} n */
const uid = (ns, n) => `0199${ns}000-0000-7000-8000-${n.toString(16).padStart(12, "0")}`;
const RIDER = { user_id: uid("9", 6), name: "Arjun Pawar" };
const SLOT = { id: uid("f", 2), label: "8-10 am", start: "08:00", end: "10:00" };
const SUNRISE = uid("e", 1);
const KUNJ = uid("e", 2);
/** Krishna Kunj has no captain: the rider hands over to himself and does the doors (rider mode). */
const KUNJ_FLATS = ["A-102", "A-201", "A-203", "A-302", "A-101", "B-101"].concat([
  "B-104",
  "B-202",
  "B-301",
  "B-303",
  "B-402",
  "B-404",
]);
/** Order ids of Krishna Kunj's bags: the packer's tasks 47 to 58 (mocks/packer.js). */
const KUNJ_ORDERS = KUNJ_FLATS.map((_, i) => uid("b", 47 + i));
/** How long after "arrived" the captain's phone takes the handover, standing in for her typing the code. */
const CAPTAIN_MS = 12_000;

/** @typedef {{ en: string, hi: string, mr: string }} Words */
/** @param {string} en @param {string} hi @param {string} [mr] @returns {Words} */
const w = (en, hi, mr = hi) => ({ en, hi, mr });

/**
 * The designs' route (field-role canvas) on the demo day all field roles share (Tue 6 Oct 2026,
 * Kharghar hub). Counts follow M6: three bags to a crate.
 * @param {string} date
 */
const demoRoute = (date) => ({
  id: ROUTE_ID,
  plan_id: uid("4", 2),
  hub_id: HUB_ID,
  delivery_date: date,
  kind: "box",
  trip: 1,
  status: "published",
  vehicle: { id: uid("3", 1), label: "EV-1", kind: "cargo_3w" },
  rider: RIDER,
  depart: "06:30",
  return_by: "11:30",
  crates: 20,
  bags: 58,
  km: 14.8,
  started_at: null,
  completed_at: null,
  version: 1,
  source: "ml",
  left_behind: [],
  stops: [
    stop(1, SUNRISE, w("Sunrise Towers", "सनराइज़ टावर्स", "सनराइज टॉवर्स"), 19.0391, 73.0657, {
      eta: "08:05",
      crates: 16,
      bags: 46,
      empties_expected: 3,
      captain: { display_name: w("Meena tai", "मीना ताई"), photo_url: null },
      donations: [
        {
          donation_id: uid("1", 402),
          ref: "B-402",
          qty: 1,
          note: w(
            "1 bag: school bag and clothes",
            "1 बैग: स्कूल बैग और कपड़े",
            "1 पिशवी: शाळेची बॅग आणि कपडे",
          ),
          status: "expected",
        },
      ],
    }),
    stop(2, KUNJ, w("Krishna Kunj", "कृष्णा कुंज"), 18.9968, 73.1203, {
      eta: "08:40",
      crates: 4,
      bags: 12,
      empties_expected: 1,
      captain: null,
      donations: [],
    }),
  ],
});

/** @param {number} seq @param {string} societyId @param {Words} name @param {number} lat @param {number} lng @param {any} o */
function stop(seq, societyId, name, lat, lng, o) {
  return {
    id: uid("5", seq),
    seq,
    kind: "drop",
    society: { id: societyId, name, location: { lat, lng } },
    slot: SLOT,
    eta: o.eta,
    late_min: 0,
    eta_now: null,
    status: "pending",
    crates: o.crates,
    bags: o.bags,
    redelivery_bags: 0,
    empties_expected: o.empties_expected,
    captain: o.captain,
    rider_mode: !o.captain,
    handover_code: null,
    navigate_url: `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}`,
    arrived_at: null,
    handed_over_at: null,
    // Not in M7 yet (§3.6 is an outline; the donation list is a backend ask): what the rider collects on the way back.
    pickups: { empties_collected: null, donations: o.donations },
  };
}

/**
 * @typedef {{ route: any, doors: Record<string, any>,
 *   keys: Record<string, { fingerprint: string, status: number, body: any }>, pings: number }} State
 * @returns {State}
 */
const seed = () => ({
  route: demoRoute(DEMO_DAY),
  doors: {},
  keys: {},
  pings: 0,
});
/** The one demo day the field roles share; the app asks for "today" and gets this day's route. */
const DEMO_DAY = "2026-10-06";

/** @returns {State} */
const load = () => {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? "null") ?? seed();
  } catch {
    return seed();
  }
};
const save = (/** @type {State} */ s) => localStorage.setItem(KEY, JSON.stringify(s));
const problem = (
  /** @type {number} */ status,
  /** @type {string} */ code,
  /** @type {string} */ title,
  /** @type {Record<string, unknown>} */ extra = {},
) =>
  HttpResponse.json(
    { type: "about:blank", title, status, code, ...extra },
    { status, headers: { "content-type": "application/problem+json" } },
  );
const invalid = () => problem(422, "request.invalid", "The request is not valid");

/**
 * Words objects become the caller's language (Accept-Language), as the server would answer.
 * @param {any} v @param {keyof Words} lang @returns {any}
 */
const localise = (v, lang) => {
  if (Array.isArray(v)) return v.map((x) => localise(x, lang));
  if (v && typeof v === "object") {
    if (typeof v.en === "string" && typeof v.hi === "string") return v[lang] ?? v.en;
    return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, localise(x, lang)]));
  }
  return v;
};
const langOf = (/** @type {Request} */ r) =>
  /** @type {keyof Words} */ (
    (r.headers.get("accept-language") ?? "en").slice(0, 2).replace(/^(?!hi|mr).*/, "en")
  );

/** The rider's endpoints refuse another role. @param {Request} request */
function riderOnly(request) {
  const role = request.headers.get("x-active-role");
  return !role || role.startsWith("rider")
    ? null
    : problem(403, "auth.forbidden", "This role cannot use rider routes");
}

/** A captain's stop is handed over once she has typed the code, some seconds after the van arrives. @param {State} s */
function settle(s) {
  for (const x of s.route.stops) {
    if (
      x.status === "arrived" &&
      !x.rider_mode &&
      Date.now() - Date.parse(/** @type {string} */ (x.arrived_at)) >= CAPTAIN_MS
    ) {
      x.status = "handed_over";
      x.handed_over_at = new Date().toISOString();
      s.route.version += 1;
    }
  }
}

const done = (/** @type {{ status: string }} */ x) =>
  x.status === "handed_over" || x.status === "skipped";
/** RouteSummary: the route without its stops. @param {State["route"]} r */
function summary(r) {
  const { stops, source, left_behind, ...rest } = r;
  return { ...rest, stops_total: stops.length, stops_done: stops.filter(done).length };
}
/** RouteDetail; the handover code shows only while the route is in progress. @param {State["route"]} r */
const detail = (r) => ({
  ...summary(r),
  source: r.source,
  left_behind: r.left_behind,
  stops: r.stops.map((/** @type {any} */ x) => ({
    ...x,
    handover_code: r.status === "in_progress" && !x.rider_mode ? CODES[x.seq] : null,
  })),
});
/** Each stop's handover code, fixed at route start. */
const CODES = /** @type {Record<number, string>} */ ({ 1: "4821", 2: "7305" });

/**
 * A write, once per Idempotency-Key: a repeat answers what the first call answered (the same key
 * with another body is refused). `act` returns [status, body] or a problem Response.
 * @param {Request} request
 * @param {(s: State, body: any) => [number, any] | Response} act
 */
async function once(request, act) {
  const denied = riderOnly(request);
  if (denied) return denied;
  const key = request.headers.get("idempotency-key");
  if (!key)
    return problem(400, "request.idempotency_key_required", "Idempotency-Key header is required");
  const body = await request.json().catch(() => ({}));
  const fingerprint = JSON.stringify([new URL(request.url).pathname, body]);
  const s = load();
  const seen = s.keys[key];
  if (seen)
    return seen.fingerprint === fingerprint
      ? HttpResponse.json(seen.body, { status: seen.status })
      : problem(422, "request.idempotency_mismatch", "That key was used for another request");
  settle(s);
  const out = act(s, body);
  if (out instanceof Response) {
    save(s);
    return out;
  }
  s.keys[key] = { fingerprint, status: out[0], body: out[1] };
  save(s);
  return HttpResponse.json(out[1], { status: out[0] });
}

/** An action needs a started route: before start 409 route_not_started, after complete 409 route_closed. @param {State} s */
function running(s) {
  if (s.route.status === "in_progress") return null;
  return s.route.status === "published"
    ? problem(409, "logistics.route_not_started", "Start the route first")
    : problem(409, "logistics.route_closed", "This route is closed");
}
/** @param {State} s @param {string} id */
const findStop = (s, id) => s.route.stops.find((/** @type {any} */ x) => x.id === id);

/** The summary of tomorrow's route for the rider with none today: "your next route". @param {string} date */
const nextRoute = (date) => ({
  ...summary({ ...demoRoute(date), id: uid("4", 3) }),
  status: "published",
});

const clientAt = (/** @type {any} */ body) =>
  body.client_at === undefined || typeof body.client_at === "string";
const SKIP_REASONS = ["captain_absent", "no_access", "vehicle_issue", "other"];
const UNDELIVERED = ["not_home", "refused", "wrong_address", "damaged", "missing", "other"];

/** @param {State} s @param {string} orderId */
const doorOf = (s, orderId) => {
  if (!KUNJ_ORDERS.includes(orderId)) return null;
  s.doors[orderId] ??= { status: null, reason: null, proof: null, at: null };
  return s.doors[orderId];
};
/** CaptainFlat: a bag at a rider-mode stop. @param {State} s @param {number} i */
function flat(s, i) {
  const stopDone = findStop(s, uid("5", 2))?.status === "handed_over";
  const d = doorOf(s, KUNJ_ORDERS[i]);
  const status =
    d.status === "delivered"
      ? "delivered"
      : d.status
        ? "undelivered"
        : stopDone
          ? "with_you"
          : "on_the_way";
  return {
    order_id: KUNJ_ORDERS[i],
    flat_label: KUNJ_FLATS[i],
    kind: "box",
    bags: 1,
    redelivery: false,
    status,
    hold_until: null,
  };
}

/** Delivery: where an order's last metres stand (§3.4). @param {string} orderId @param {any} d */
const delivery = (orderId, d) => ({
  order_id: orderId,
  attempt: 1,
  delivery_date: DEMO_DAY,
  society_id: KUNJ,
  status: d.status,
  reason: d.reason,
  hold_until: null,
  proof: d.proof,
  by: "rider",
  at: d.at,
});

export const logisticsHandlers = [
  // The rider's routes for a day (§3.3): date is today or tomorrow (riders see them the night
  // before). The mock answers with the demo day's route whatever day is asked, so the rider,
  // packer, captain and kitchen see one day; the Thane rider has none today but one tomorrow.
  http.get("*/v1/riders/me/routes", ({ request }) => {
    const denied = riderOnly(request);
    if (denied) return denied;
    if (localStorage.getItem(FAIL_KEY)) return HttpResponse.error();
    const today = todayIst();
    const date = new URL(request.url).searchParams.get("date") ?? today;
    const day = Date.parse(`${date}T12:00:00Z`);
    const base = Date.parse(`${today}T12:00:00Z`);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || day < base - 7 * 864e5 || day > base + 864e5)
      return problem(422, "logistics.date_out_of_range", "The date is outside the allowed range");
    if (caller(request) === NO_ROUTE_RIDER)
      return HttpResponse.json({
        items: date > today ? [localise(nextRoute(date), langOf(request))] : [],
      });
    const s = load();
    settle(s);
    save(s);
    return HttpResponse.json({ items: [localise(summary(s.route), langOf(request))] });
  }),

  // One route with its stops (ETag = version).
  http.get("*/v1/routes/:id", ({ request, params }) => {
    const denied = riderOnly(request);
    if (denied) return denied;
    const s = load();
    if (String(params.id) !== s.route.id)
      return problem(404, "logistics.route_not_found", "Route not found");
    settle(s);
    save(s);
    return HttpResponse.json(localise(detail(s.route), langOf(request)), {
      headers: { etag: `"${s.route.version}"`, "cache-control": "no-store" },
    });
  }),

  http.post("*/v1/routes/:id/start", ({ request, params }) =>
    once(request, (s, body) => {
      if (String(params.id) !== s.route.id)
        return problem(404, "logistics.route_not_found", "Route not found");
      if (!clientAt(body)) return invalid();
      if (s.route.status === "in_progress")
        return problem(409, "logistics.rider_busy", "This route is already in progress");
      if (s.route.status !== "published")
        return problem(409, "logistics.route_closed", "This route is closed");
      s.route.status = "in_progress";
      s.route.started_at = new Date().toISOString();
      s.route.version += 1;
      return [200, detail(s.route)];
    }),
  ),

  http.post("*/v1/routes/:id/complete", ({ request, params }) =>
    once(request, (s, body) => {
      if (String(params.id) !== s.route.id)
        return problem(404, "logistics.route_not_found", "Route not found");
      const closed = running(s);
      if (closed) return closed;
      if (
        !clientAt(body) ||
        (body.empties_returned !== undefined && !Number.isInteger(body.empties_returned))
      )
        return invalid();
      const open = s.route.stops.filter((/** @type {any} */ x) => !done(x));
      if (open.length)
        return problem(409, "logistics.stops_open", "Hand over every stop first", {
          stop_ids: open.map((/** @type {any} */ x) => x.id),
        });
      s.route.status = "completed";
      s.route.completed_at = new Date().toISOString();
      s.route.version += 1;
      return [200, detail(s.route)];
    }),
  ),

  http.post("*/v1/stops/:id/arrive", ({ request, params }) =>
    once(request, (s, body) => {
      const x = findStop(s, /** @type {string} */ (params.id));
      if (!x) return problem(404, "logistics.stop_not_found", "Stop not found");
      const closed = running(s);
      if (closed) return closed;
      if (!clientAt(body)) return invalid();
      if (x.status === "pending") {
        x.status = "arrived";
        x.arrived_at = new Date().toISOString();
        s.route.version += 1;
      }
      return [200, x];
    }),
  ),

  http.post("*/v1/stops/:id/skip", ({ request, params }) =>
    once(request, (s, body) => {
      const x = findStop(s, /** @type {string} */ (params.id));
      if (!x) return problem(404, "logistics.stop_not_found", "Stop not found");
      const closed = running(s);
      if (closed) return closed;
      if (!SKIP_REASONS.includes(body.reason) || !clientAt(body)) return invalid();
      if (x.status === "handed_over")
        return problem(409, "logistics.already_handed_over", "This stop is already handed over");
      x.status = "skipped";
      s.route.version += 1;
      return [200, x];
    }),
  ),

  // The doors of a rider-mode stop, while the route is in progress.
  http.get("*/v1/stops/:id/deliveries", ({ request, params }) => {
    if (otherRole(request)) return undefined;
    const s = load();
    const x = findStop(s, /** @type {string} */ (params.id));
    if (!x) return problem(404, "logistics.stop_not_found", "Stop not found");
    if (!x.rider_mode)
      return problem(403, "auth.forbidden", "Only a rider-mode stop lists its doors");
    const closed = running(s);
    if (closed) return closed;
    return HttpResponse.json({ items: KUNJ_ORDERS.map((_, i) => flat(s, i)) });
  }),

  // Handover at a rider-mode stop: the rider counts crates and bags and needs no code or photo
  // (a captain's stop is handed over from her phone: mocks/captain.js). Other roles pass.
  http.post("*/v1/stops/:id/handover", ({ request, params }) =>
    otherRole(request)
      ? undefined
      : once(request, (s, body) => {
          const x = findStop(s, /** @type {string} */ (params.id));
          if (!x) return problem(404, "logistics.stop_not_found", "Stop not found");
          if (!x.rider_mode)
            return problem(403, "auth.forbidden", "This stop is handed over by its captain");
          const closed = running(s);
          if (closed) return closed;
          if (!Number.isInteger(body.crates) || !Number.isInteger(body.bags) || !clientAt(body))
            return invalid();
          if (x.status === "handed_over")
            return problem(
              409,
              "logistics.already_handed_over",
              "This stop is already handed over",
            );
          if (body.crates !== x.crates || body.bags !== x.bags)
            return problem(
              409,
              "logistics.count_mismatch",
              "The counts do not match what the van carries",
              {
                expected: { crates: x.crates, bags: x.bags },
              },
            );
          x.status = "handed_over";
          x.handed_over_at = new Date().toISOString();
          s.route.version += 1;
          return [
            200,
            {
              handover_id: uid("6", x.seq),
              stop: x,
              at_society: KUNJ_ORDERS,
              still_dispatched: [],
              mismatch: false,
            },
          ];
        }),
  ),

  // Door proof at a rider-mode stop: a rider needs the customer's code or a photo (never a bare scan).
  http.post("*/v1/deliveries/:orderId/proof", ({ request, params }) =>
    otherRole(request)
      ? undefined
      : once(request, (s, body) => {
          const d = doorOf(s, /** @type {string} */ (params.orderId));
          if (!d) return problem(404, "logistics.delivery_not_found", "Delivery not found");
          const closed = running(s);
          if (closed) return closed;
          if (body.bag_qr !== `GB1:${String(params.orderId).replaceAll("-", "")}`)
            return problem(422, "logistics.bag_mismatch", "That bag is not this order's");
          if (body.proof !== "otp" && body.proof !== "photo")
            return problem(422, "logistics.proof_required", "A code or a photo is required");
          if (body.proof === "otp" && !/^\d{4}$/.test(body.delivery_code ?? ""))
            return problem(422, "logistics.code_invalid", "That code is not right");
          if (body.proof === "photo" && typeof body.photo_key !== "string")
            return problem(422, "logistics.photo_key_invalid", "The photo is not valid");
          if (findStop(s, uid("5", 2))?.status !== "handed_over")
            return problem(409, "orders.illegal_transition", "Hand the crates over first");
          if (d.status) return problem(409, "logistics.delivery_closed", "This order is closed");
          Object.assign(d, {
            status: "delivered",
            proof: body.proof,
            at: new Date().toISOString(),
          });
          return [200, delivery(String(params.orderId), d)];
        }),
  ),

  // Undelivered at a rider-mode stop: not_home rides back; damaged or missing refunds at once;
  // the rest goes to the hub manager's triage (§3.4).
  http.post("*/v1/deliveries/:orderId/undelivered", ({ request, params }) =>
    otherRole(request)
      ? undefined
      : once(request, (s, body) => {
          const d = doorOf(s, /** @type {string} */ (params.orderId));
          if (!d) return problem(404, "logistics.delivery_not_found", "Delivery not found");
          const closed = running(s);
          if (closed) return closed;
          if (!UNDELIVERED.includes(body.reason))
            return problem(422, "logistics.reason_invalid", "That reason is not valid");
          if (body.reason === "other" && !String(body.note ?? "").trim()) return invalid();
          if (findStop(s, uid("5", 2))?.status !== "handed_over")
            return problem(409, "orders.illegal_transition", "Hand the crates over first");
          if (d.status) return problem(409, "logistics.delivery_closed", "This order is closed");
          Object.assign(d, {
            status: ["damaged", "missing"].includes(body.reason)
              ? "refunded"
              : body.reason === "not_home"
                ? "undelivered"
                : "triage",
            reason: body.reason,
            at: new Date().toISOString(),
          });
          return [200, delivery(String(params.orderId), d)];
        }),
  ),

  // Return-leg pickups (§3.6 outline): the request is the outline's; the answer is ours.
  http.post("*/v1/stops/:id/pickups", ({ request, params }) =>
    once(request, (s, body) => {
      const x = findStop(s, /** @type {string} */ (params.id));
      if (!x) return problem(404, "logistics.stop_not_found", "Stop not found");
      const closed = running(s);
      if (closed) return closed;
      const donations = body.donations ?? [];
      if (
        (body.empties !== undefined && !Number.isInteger(body.empties)) ||
        !Array.isArray(donations) ||
        !clientAt(body)
      )
        return invalid();
      if (body.empties !== undefined) x.pickups.empties_collected = body.empties;
      for (const d of donations) {
        const row = x.pickups.donations.find(
          (/** @type {any} */ p) => p.donation_id === d.donation_id,
        );
        if (!row || !["collected", "not_ready"].includes(d.status))
          return problem(422, "request.invalid", "That donation is not on this stop");
        row.status = d.status;
      }
      s.route.version += 1;
      return [
        200,
        { stop_id: x.id, empties: x.pickups.empties_collected, donations: x.pickups.donations },
      ];
    }),
  ),

  // Batched GPS pings while the route is in progress (oldest first, 1 to 20); old, future or
  // too-close pings are dropped and counted, never an error.
  http.post("*/v1/riders/me/location", async ({ request }) => {
    const denied = riderOnly(request);
    if (denied) return denied;
    const body = /** @type {any} */ (await request.json().catch(() => ({})));
    const s = load();
    if (!Array.isArray(body.pings) || body.pings.length < 1 || body.pings.length > 20)
      return invalid();
    if (typeof body.route_id !== "string") return invalid();
    if (s.route.status !== "in_progress" || body.route_id !== s.route.id)
      return problem(409, "logistics.no_active_route", "No route is in progress");
    const now = Date.now();
    const ok = body.pings.filter((/** @type {any} */ p) => {
      const at = Date.parse(p.at);
      return at > now - 10 * 60_000 && at < now + 60_000 && p.accuracy_m <= 200;
    });
    s.pings += ok.length;
    save(s);
    return HttpResponse.json(
      { accepted: ok.length, dropped: body.pings.length - ok.length },
      { status: 202 },
    );
  }),

  // Recent earnings from the ledger (M8 §3: GET /v1/earnings?week=; no week = the latest routes).
  // Every partner role has earnings; these answer only a rider and pass anyone else on.
  http.get("*/v1/earnings", ({ request }) =>
    otherRole(request)
      ? undefined
      : HttpResponse.json({
          total_paise: 178000,
          routes: 3,
          pickup_runs: 2,
          payout_weekday: 0,
          items: [
            { date: "2026-10-06", routes: 1, societies: 2, pickups: true, amount_paise: 62000 },
            { date: "2026-10-02", routes: 1, societies: 2, pickups: false, amount_paise: 54000 },
            { date: "2026-09-29", routes: 1, societies: 2, pickups: true, amount_paise: 62000 },
          ],
        }),
  ),

  // Income certificate (M8 US-7, a PDF). Real: a short-lived signed URL; mock: a tiny local PDF.
  http.get("*/v1/earnings/certificate", ({ request }) => {
    if (otherRole(request)) return undefined;
    const url = mockPdfUrl("GharBasket income certificate - mock");
    return HttpResponse.json({ url, expires_in: 300 });
  }),
];

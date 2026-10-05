// M7 logistics mocks (docs/modules/M7-logistics.md §3) for the rider: today's route, start and
// finish, the handover at each society and the return-leg pickups, plus M8 earnings. The spec
// lists endpoints, not fields; these shapes are our proposal (features/logistics/queries.js).
// Writes need an Idempotency-Key and replay the first answer, as the offline outbox relies on.
import { HttpResponse, http } from "msw";
import { caller } from "./handlers.js";
import { mockPdfUrl } from "./pdf.js";

// Endpoints shared with other roles (handover, earnings) answer here unless another role is acting.
const otherRole = (/** @type {Request} */ request) => {
  const role = request.headers.get("x-active-role");
  return !!role && !role.startsWith("rider");
};

const KEY = "gb-mock-logistics";
/** Mock-only switch for the "route didn't load" state: localStorage[FAIL_KEY] = "1". */
export const FAIL_KEY = "gb-mock-routes-fail";
/** The rider at hub-thane-1 (98000 00003) has no route today: the empty state. */
const NO_ROUTE_RIDER = "+919800000003";

/** @typedef {{ en: string, hi: string, mr: string }} Words */
/** @returns {{ routes: Record<string, any>, replies: Record<string, { status: number, body: any }> }} */
const load = () => {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? "null") ?? { routes: {}, replies: {} };
  } catch {
    return { routes: {}, replies: {} };
  }
};
const save = (/** @type {ReturnType<typeof load>} */ s) =>
  localStorage.setItem(KEY, JSON.stringify(s));
const problem = (
  /** @type {number} */ status,
  /** @type {string} */ code,
  /** @type {string} */ title,
) =>
  HttpResponse.json(
    { type: "about:blank", title, status, code },
    { status, headers: { "content-type": "application/problem+json" } },
  );

/** @param {string} en @param {string} hi @param {string} [mr] @returns {Words} */
const w = (en, hi, mr = hi) => ({ en, hi, mr });
const HUB = { id: "hub-kharghar", name: w("Kharghar hub", "खारघर हब"), lat: 19.0473, lng: 73.0699 };
const MEENA = { name: w("Meena tai", "मीना ताई"), phone: "+919800001004" };

/**
 * The one demo day the field roles share (Tue 6 Oct 2026 at the Kharghar hub), as in mocks/packer.js,
 * captain.js and kitchen.js. The app asks for "today"; the mock answers with this day's route.
 */
const DEMO_DAY = "2026-10-06";

/** The designs' route (field-role canvas: example numbers) on the demo day. */
const demoRoute = (/** @type {string} */ date) => ({
  id: `rte-${date}-kh-1`,
  date,
  slot: { id: "slot-08", from: "08:00", to: "10:00" },
  vehicle: { id: "veh-1", plate: "MH-46 BX 2041" },
  hub: HUB,
  status: "published",
  stops: [
    {
      id: "stp-1",
      seq: 1,
      society: {
        id: "sunrise-towers",
        name: w("Sunrise Towers", "सनराइज़ टावर्स", "सनराइज टॉवर्स"),
        gate: w("gate 2", "गेट 2"),
        where: w("Gate 2 · Sector 20, Kharghar", "गेट 2 · सेक्टर 20, खारघर"),
        lat: 19.0391,
        lng: 73.0657,
      },
      captain: MEENA,
      eta: "08:05",
      bags_count: 46,
      crates: [
        { id: "S-1", bags: 16, state: "scanned" },
        { id: "S-2", bags: 15, state: "scanned" },
        { id: "S-3", bags: 15, state: "waiting" },
      ],
      status: "todo",
    },
    {
      id: "stp-2",
      seq: 2,
      society: {
        id: "krishna-kunj",
        name: w("Krishna Kunj", "कृष्णा कुंज"),
        gate: null,
        where: w("Sector 17, New Panvel", "सेक्टर 17, न्यू पनवेल", "सेक्टर 17, नवीन पनवेल"),
        lat: 18.9968,
        lng: 73.1203,
      },
      captain: null,
      eta: "08:40",
      bags_count: 12,
      crates: [{ id: "K-1", bags: 12, state: "waiting" }],
      status: "todo",
    },
  ],
  back: { eta: "10:30", from: "10:00", to: "12:00" },
  pickups: [
    {
      id: "pck-1",
      kind: "crate",
      stop_id: "stp-1",
      qty: 3,
      ref: null,
      codes: ["S-1", "S-2", "S-3"],
      note: w("From Meena tai at gate 2", "गेट 2 पर मीना ताई से", "गेट 2 वर मीना ताईंकडून"),
      status: "collected",
    },
    {
      id: "pck-2",
      kind: "donation",
      stop_id: "stp-1",
      qty: 1,
      ref: "B-402",
      codes: ["B-402"],
      note: w("1 bag: school bag and clothes", "1 बैग: स्कूल बैग और कपड़े", "1 पिशवी: शाळेची बॅग आणि कपडे"),
      status: "pending",
    },
    {
      id: "pck-3",
      kind: "crate",
      stop_id: "stp-2",
      qty: 1,
      ref: null,
      codes: ["K-1"],
      note: w("At the security cabin", "सिक्योरिटी केबिन पर", "सिक्युरिटी केबिनवर"),
      status: "pending",
    },
  ],
});

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

/** The route a write points at, by route, stop or pickup id. */
const findRoute = (
  /** @type {ReturnType<typeof load>} */ s,
  /** @type {(r: any) => boolean} */ f,
) => Object.values(s.routes).find(f);

/**
 * Runs a write once per Idempotency-Key: a replay gets the first answer back, so an outbox retry
 * never repeats an action. `act` returns [status, body] or a problem Response.
 * @param {Request} request
 * @param {(s: ReturnType<typeof load>, body: any) => [number, any] | Response} act
 */
async function once(request, act) {
  const key = request.headers.get("idempotency-key");
  if (!key)
    return problem(400, "platform.idempotency_key_required", "Idempotency-Key header is required");
  const s = load();
  const seen = s.replies[key];
  if (seen) return HttpResponse.json(seen.body, { status: seen.status });
  const out = act(s, await request.json().catch(() => ({})));
  if (out instanceof Response) return out;
  s.replies[key] = { status: out[0], body: out[1] };
  save(s);
  return HttpResponse.json(out[1], { status: out[0] });
}

const notStarted = () =>
  problem(409, "logistics.route_not_started", "Start the route before handing over");

export const logisticsHandlers = [
  // The rider's own routes for a day (GET /v1/routes?date=, rider: own). The mock answers with the
  // demo day's route whatever day is asked, so the rider, packer, captain and kitchen see one day
  // (the screen shows the route's own date). `next` is our addition for the empty state: the
  // rider's next route day and slot.
  http.get("*/v1/routes", ({ request }) => {
    if (localStorage.getItem(FAIL_KEY)) return HttpResponse.error();
    const date = new URL(request.url).searchParams.get("date") ?? "";
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date))
      return problem(422, "logistics.date_invalid", "date must be YYYY-MM-DD");
    const next = new Date(Date.parse(`${DEMO_DAY}T12:00:00Z`) + 2 * 86_400_000);
    if (caller(request) === NO_ROUTE_RIDER) {
      return HttpResponse.json({
        items: [],
        next: { date: next.toISOString().slice(0, 10), from: "08:00", to: "10:00" },
      });
    }
    const s = load();
    s.routes[DEMO_DAY] ??= demoRoute(DEMO_DAY);
    save(s);
    return HttpResponse.json({
      items: [localise(s.routes[DEMO_DAY], langOf(request))],
      next: null,
    });
  }),

  http.post("*/v1/routes/:id/:action", ({ request, params }) =>
    once(request, (s) => {
      const route = findRoute(s, (r) => r.id === params.id);
      if (!route) return problem(404, "logistics.route_not_found", "Route not found");
      if (params.action === "start") {
        if (route.status !== "published")
          return problem(409, "logistics.route_already_started", "Route already started");
        route.status = "started";
      } else if (params.action === "complete") {
        if (route.status !== "started") return notStarted();
        if (route.stops.some((/** @type {any} */ x) => x.status !== "done"))
          return problem(409, "logistics.stops_open", "Hand over every stop first");
        route.status = "completed";
      } else return problem(404, "platform.not_found", "Not found");
      return [200, { id: route.id, status: route.status }];
    }),
  ),

  // Handover at a society: crates, bag count and the captain's 4-digit code or a photo. The
  // real server checks the code against the captain's app; the mock only checks its shape.
  // Captains post to the same endpoint (M7 §3); their calls go on to mocks/captain.js.
  http.post("*/v1/stops/:id/handover", ({ request, params }) =>
    otherRole(request)
      ? undefined
      : once(request, (s, body) => {
          const route = findRoute(s, (r) =>
            r.stops.some((/** @type {any} */ x) => x.id === params.id),
          );
          const stop = route?.stops.find((/** @type {any} */ x) => x.id === params.id);
          if (!route || !stop) return problem(404, "logistics.stop_not_found", "Stop not found");
          if (route.status !== "started") return notStarted();
          if (stop.status === "done")
            return problem(409, "logistics.stop_handed_over", "This stop is already handed over");
          const otp = typeof body.otp === "string" && /^\d{4}$/.test(body.otp);
          const photo =
            typeof body.photo_jpeg === "string" && body.photo_jpeg.startsWith("data:image/");
          if (!otp && !photo)
            return problem(
              422,
              "logistics.proof_required",
              "A handover code or a photo is required",
            );
          stop.status = "done";
          stop.handover = { proof: otp ? "otp" : "photo", bags_count: body.bags_count ?? null };
          return [201, { stop_id: stop.id, status: "done", proof: stop.handover.proof }];
        }),
  ),

  // A return-leg pickup is collected, by ticking it or scanning one of its codes.
  http.post("*/v1/pickups/:id/collect", ({ request, params }) =>
    once(request, (s, body) => {
      const route = findRoute(s, (r) =>
        r.pickups.some((/** @type {any} */ p) => p.id === params.id),
      );
      const pickup = route?.pickups.find((/** @type {any} */ p) => p.id === params.id);
      if (!route || !pickup) return problem(404, "logistics.pickup_not_found", "Pickup not found");
      if (body.code && !pickup.codes.includes(body.code))
        return problem(422, "logistics.code_mismatch", "That code is not on this pickup");
      pickup.status = "collected";
      return [200, { id: pickup.id, status: "collected" }];
    }),
  ),

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

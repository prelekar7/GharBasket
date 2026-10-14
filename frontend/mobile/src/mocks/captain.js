// M7/M8 captain mocks, in the real shapes of docs/modules/M7-logistics.md §3.4 (frozen) and M8 §3.3,
// §3.6: the day at Sunrise Towers (designs: Tue 6 Oct, 46 bags) as CaptainDay, the handover by count
// and code, door proof and undelivered, plus the captain's partner record and her week of earnings.
// Order ids follow mocks/packer.js (the same bags the packer packed). Writes share applyAction()
// with the screen. Only the captain of the society may call them (X-Active-Role: captain:<society>);
// any other role's call on a shared path goes on to its own mock.
import { HttpResponse, http } from "msw";
import { applyAction, bagQr, countFlats } from "@/features/captain/day.js";
import { HUB_ID } from "./packer.js";
import { mockPdfUrl } from "./pdf.js";

const KEY = "gb-mock-captain-v2";
const DATE = "2026-10-06";
const HANDOVER_CODE = "4821"; // shown on the rider's phone
const MAX_CODE_TRIES = 5;
const PAISE_PER_BAG = 600; // M8 §3.6: ₹6 a bag delivered

/** Ids are UUIDs: the namespace picks the kind, the number the row (as in mocks/packer.js). @param {string} ns @param {number} n */
const uid = (ns, n) => `0199${ns}000-0000-7000-8000-${n.toString(16).padStart(12, "0")}`;
const SOCIETY = { id: uid("e", 1), name: "Sunrise Towers" };
const STOP_ID = uid("5", 1);
const ROUTE_ID = uid("4", 1);
const SLOT = { label: "7-9 am", start: "07:00", end: "09:00" };

/** Flats in walking order per tower. The first six are the packer's queue (mocks/packer.js seq 1 to 6). */
const TOWERS = {
  A: ["1203", "604", "104", "203", "305", "402", "506", "701", "708", "802", "905", "1001"],
  B: ["402", "205", "304", "507", "603", "702", "801", "806", "903", "1002", "1104", "1201"],
  C: ["807", "1102", "203", "302", "407", "505", "608", "702", "901", "1003", "1204", "1301"],
};
const FIRST_SIX = ["A-1203", "B-402", "C-807", "A-604", "B-205", "C-1102"];
const ADDON = "B-205";
const REDELIVERY = "C-407";

/** @returns {import("@/features/captain/day.js").CaptainDay} */
function seedDay() {
  const labels = Object.entries(TOWERS).flatMap(([tower, ns]) => ns.map((n) => `${tower}-${n}`));
  // Pad to the designs' 46 bags with more flats of the same towers.
  for (let i = 0; labels.length < 46; i++) {
    const label = `${"ABC"[i % 3]}-${1301 + i}`;
    if (!labels.includes(label)) labels.push(label);
  }
  let seq = FIRST_SIX.length;
  /** @type {import("@/features/captain/day.js").Flat[]} */
  const flats = labels.map((flat_label) => {
    const first = FIRST_SIX.indexOf(flat_label);
    return {
      order_id: uid("b", first >= 0 ? first + 1 : ++seq),
      flat_label,
      kind: flat_label === ADDON ? "addon" : "box",
      bags: 1,
      redelivery: flat_label === REDELIVERY,
      status: "on_the_way",
      hold_until: null,
    };
  });
  flats.sort((a, b) =>
    a.flat_label[0] === b.flat_label[0]
      ? Number(a.flat_label.slice(2)) - Number(b.flat_label.slice(2))
      : a.flat_label < b.flat_label
        ? -1
        : 1,
  );
  // Three bags to a crate (M6 §3.2); the redelivery bag came back to the hub and rides on the van.
  const bags = flats.length;
  return {
    date: DATE,
    society: SOCIETY,
    stops: [
      {
        stop_id: STOP_ID,
        route_id: ROUTE_ID,
        slot: SLOT,
        eta_now: "07:20",
        status: "pending",
        expected: { crates: Math.ceil(bags / 3), bags, redelivery_bags: 1 },
      },
    ],
    flats,
    counts: countFlats(flats),
  };
}

/** @typedef {{ day: import("@/features/captain/day.js").CaptainDay, tries: number, keys: Record<string, { status: number, body: Record<string, any> }> }} State */
/** @returns {State} */
const fresh = () => ({ day: seedDay(), tries: 0, keys: {} });
/** @returns {State} */
const load = () => {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? "null") ?? fresh();
  } catch {
    return fresh();
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
    {
      type: `https://gharbasket.in/problems/${code}`,
      title,
      status,
      detail: title,
      code,
      ...extra,
    },
    { status, headers: { "content-type": "application/problem+json" } },
  );
const invalid = (/** @type {string} */ field, /** @type {string} */ message) =>
  problem(422, "request.invalid", "The request is not valid", {
    errors: [{ loc: ["body", field], message, type: "value_error" }],
  });

/** The captain of this society, or a problem (reads). @param {Request} request */
const isCaptain = (request) => !!request.headers.get("x-active-role")?.startsWith("captain:");
const forbidden = () => problem(403, "auth.forbidden", "Only this society’s captain can do that");

/**
 * A write: only the captain (anyone else's call goes on to its own mock), Idempotency-Key, a
 * repeated key gets the first answer, then `act` answers or refuses before the day changes.
 * @param {(s: State, body: any, params: Record<string, any>) => Response | { status?: number, body: Record<string, any>, day?: import("@/features/captain/day.js").CaptainDay }} act
 */
const write =
  (act) =>
  async (/** @type {{ request: Request, params: Record<string, any> }} */ { request, params }) => {
    if (!isCaptain(request)) return undefined;
    const key = request.headers.get("idempotency-key");
    if (!key)
      return problem(400, "request.idempotency_key_required", "Idempotency-Key header is required");
    const s = load();
    if (s.keys[key]) return HttpResponse.json(s.keys[key].body, { status: s.keys[key].status });
    const body = await request.json().catch(() => ({}));
    const out = act(s, body, params);
    if (out instanceof Response) {
      save(s); // a wrong code counts
      return out;
    }
    if (out.day) s.day = out.day;
    s.keys[key] = { status: out.status ?? 200, body: out.body };
    save(s);
    return HttpResponse.json(out.body, { status: out.status ?? 200 });
  };

/** @param {State} s @param {string} orderId */
const flatOf = (s, orderId) => s.day.flats.find((f) => f.order_id === orderId);

/** Delivery (M7 §3.4) after a write. @param {State} s @param {string} orderId @param {Record<string, unknown>} more */
const delivery = (s, orderId, more) => ({
  order_id: orderId,
  attempt: flatOf(s, orderId)?.redelivery ? 2 : 1,
  delivery_date: s.day.date,
  society_id: s.day.society.id,
  reason: null,
  hold_until: null,
  proof: null,
  by: "captain",
  at: new Date().toISOString(),
  ...more,
});

/** The order's flat when it may be closed now, or a problem. @param {State} s @param {string} orderId */
const closable = (s, orderId) => {
  const flat = flatOf(s, orderId);
  if (!flat) return problem(404, "logistics.delivery_not_found", "No such bag today");
  if (flat.status === "delivered" || flat.status === "undelivered")
    return problem(409, "logistics.delivery_closed", "This bag is already done");
  if (flat.status === "on_the_way")
    return problem(409, "orders.illegal_transition", "Receive the crates first");
  return flat;
};

const REASONS = ["not_home", "refused", "wrong_address", "damaged", "missing", "other"];

const captainPartner = () => ({
  id: uid("8", 4),
  user_id: uid("9", 4),
  kind: "captain",
  hub_id: HUB_ID,
  display_name: "Meena tai",
  photo_url: null,
  status: "active",
  rating: null,
  verified_at: "2026-09-01T04:00:00Z",
  suspension: null,
  version: 1,
});

export const captainHandlers = [
  // M7 §3.4: CaptainDay for the society of X-Active-Role (`date` defaults to today).
  http.get("*/v1/captains/me/today", ({ request }) =>
    isCaptain(request) ? HttpResponse.json(load().day) : forbidden(),
  ),

  // M8 §3.3: the caller's partner records, one per kind. Other roles' mocks answer their own.
  http.get("*/v1/partners/me", ({ request }) =>
    isCaptain(request) ? HttpResponse.json({ items: [captainPartner()] }) : undefined,
  ),

  http.post(
    "*/v1/stops/:id/handover",
    write((s, body, { id }) => {
      const stop = s.day.stops.find((x) => x.stop_id === id);
      if (!stop) return problem(404, "logistics.stop_not_found", "No such stop");
      for (const f of ["crates", "bags"])
        if (!Number.isInteger(body[f]) || body[f] < 0)
          return invalid(f, "Input should be a non-negative integer");
      if (!!body.handover_code === !!body.photo_key)
        return invalid("handover_code", "Send exactly one of handover_code and photo_key");
      if (stop.status === "handed_over")
        return problem(409, "logistics.already_handed_over", "These crates are already received");
      if (body.handover_code) {
        if (s.tries >= MAX_CODE_TRIES)
          return problem(
            429,
            "logistics.code_attempts_exceeded",
            "Too many wrong codes. Send a photo of the crates",
          );
        if (body.handover_code !== HANDOVER_CODE) {
          s.tries += 1;
          return problem(422, "logistics.code_invalid", "That code is not right");
        }
      }
      const same = body.crates === stop.expected.crates && body.bags === stop.expected.bags;
      /** @type {string[]} */
      const scanned = same ? [] : (body.scanned_bags ?? []);
      if (!same && scanned.length === 0)
        return problem(409, "logistics.count_mismatch", "The counts do not match the van's");
      const onTheWay = s.day.flats.filter((f) => f.status === "on_the_way");
      if (scanned.some((q) => !onTheWay.some((f) => bagQr(f.order_id) === q)))
        return problem(422, "logistics.bag_mismatch", "Those bags are not on this van");
      const day = applyAction(s.day, {
        path: `/v1/stops/${id}/handover`,
        body: same ? {} : { scanned_bags: scanned },
      });
      const moved = (/** @type {import("@/features/captain/day.js").Flat} */ f) =>
        onTheWay.some((o) => o.order_id === f.order_id) && f.status === "with_you";
      const now = new Date().toISOString();
      return {
        day,
        body: {
          handover_id: uid("7", Object.keys(s.keys).length + 1),
          stop: {
            id,
            seq: 1,
            kind: "drop",
            society: { ...s.day.society, location: { lat: 19.0335, lng: 73.0629 } },
            slot: { id: uid("f", 1), ...stop.slot },
            eta: "07:12",
            late_min: 8,
            eta_now: stop.eta_now,
            status: "handed_over",
            crates: stop.expected.crates,
            bags: stop.expected.bags,
            redelivery_bags: stop.expected.redelivery_bags,
            empties_expected: 0,
            captain: { display_name: "Meena tai", photo_url: null },
            rider_mode: false,
            handover_code: null,
            navigate_url: "https://www.google.com/maps/dir/?api=1&destination=19.0335,73.0629",
            arrived_at: now,
            handed_over_at: now,
          },
          at_society: day.flats.filter(moved).map((f) => f.order_id),
          still_dispatched: day.flats
            .filter((f) => f.status === "on_the_way")
            .map((f) => f.order_id),
          mismatch: !same,
        },
      };
    }),
  ),

  http.post(
    "*/v1/deliveries/:orderId/proof",
    write((s, body, { orderId }) => {
      const flat = closable(s, orderId);
      if (flat instanceof Response) return flat;
      if (flat.kind === "tiffin")
        return problem(
          422,
          "logistics.proof_required",
          "A tiffin needs the family’s code or a photo",
        );
      if (!["scan", "otp", "photo"].includes(body.proof)) return invalid("proof", "Unknown proof");
      if (body.bag_qr !== bagQr(orderId))
        return problem(422, "logistics.bag_mismatch", "That is not this order's bag");
      return {
        day: applyAction(s.day, { path: `/v1/deliveries/${orderId}/proof`, body }),
        body: delivery(s, orderId, { status: "delivered", proof: body.proof }),
      };
    }),
  ),

  http.post(
    "*/v1/deliveries/:orderId/undelivered",
    write((s, body, { orderId }) => {
      const flat = closable(s, orderId);
      if (flat instanceof Response) return flat;
      if (!REASONS.includes(body.reason)) return invalid("reason", "Unknown reason");
      if (body.reason === "other" && !String(body.note ?? "").trim())
        return invalid("note", "Say what happened");
      if (String(body.note ?? "").length > 300) return invalid("note", "Up to 300 characters");
      // M7 §3.4: not home = held until 20:00; our fault = refunded; the rest wait for the hub.
      const held = body.reason === "not_home";
      const status = held
        ? "held"
        : ["damaged", "missing"].includes(body.reason)
          ? "refunded"
          : "triage";
      return {
        day: applyAction(s.day, { path: `/v1/deliveries/${orderId}/undelivered`, body }),
        body: delivery(s, orderId, {
          status,
          reason: body.reason,
          hold_until: held ? "20:00" : null,
        }),
      };
    }),
  ),

  // M8 §3.6: this week from the ledger (via billing.api): Monday's bags, and today's as delivered.
  http.get("*/v1/earnings", ({ request }) => {
    if (!isCaptain(request)) return undefined;
    const done = load().day.flats.filter((f) => f.status === "delivered");
    const entry = (/** @type {string} */ id, /** @type {string} */ at) => ({
      id,
      kind: "captain",
      basis: "bag_delivered",
      units: 1,
      amount_paise: PAISE_PER_BAG,
      ref: { type: "order", id },
      at,
    });
    const entries = [
      ...done.map((f) => entry(f.order_id, "2026-10-06T03:30:00Z")),
      ...Array.from({ length: 41 }, (_, i) => entry(uid("a", i + 1), "2026-10-05T03:30:00Z")),
    ];
    const days = ["05", "06", "07", "08", "09", "10", "11"].map((d) => ({
      date: `2026-10-${d}`,
      amount_paise: entries.filter((e) => e.at.startsWith(`2026-10-${d}`)).length * PAISE_PER_BAG,
    }));
    const total = days.reduce((n, d) => n + d.amount_paise, 0);
    return HttpResponse.json({
      week: "2026-W41",
      from: "2026-10-05",
      to: "2026-10-11",
      total_paise: total,
      by_kind: [
        { kind: "captain", basis: "bag_delivered", units: entries.length, amount_paise: total },
      ],
      days,
      entries,
      payouts: [],
      generated_at: new Date().toISOString(),
    });
  }),

  // The income certificate as a short-lived link; here a one-line PDF made on the spot. Its
  // endpoint is not in M8 §3.7 (an outline) yet.
  http.get("*/v1/earnings/certificate", ({ request }) => {
    if (!isCaptain(request)) return undefined;
    const text = "GharBasket income certificate (example) - Meena Jadhav, Society Captain";
    return HttpResponse.json({
      url: mockPdfUrl(text),
      filename: "gharbasket-income-certificate.pdf",
    });
  }),
];

/** Dev helper: start the day again. */
export const resetCaptain = () => localStorage.removeItem(KEY);

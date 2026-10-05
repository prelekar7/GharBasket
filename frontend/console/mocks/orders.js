// Orders, cut-off and slot mocks (M4 §3). The paths `/v1/orders`, `/v1/slots`, `/v1/admin/slots` and
// `POST /v1/hubs/{id}/cutoff/run` are the spec's; `GET /v1/hubs/{id}/cutoff` and `PUT /v1/admin/slots/{id}`
// are proposals for the console (the spec has no read side for the cut-off monitor, and no slot edit).
// Money is integer paise. The mock "now" is fixed to the shared story: Mon 5 Oct 2026, 6:46 PM IST,
// so the cut-off countdown reads "2 h 14 min" on every load.
import { HttpResponse, http } from "msw";
import { idempotent, mockStore, problem, roleOf } from "./util.js";

export const STORY_NOW = "2026-10-05T18:46:00+05:30";
const CUTOFF_AT = "2026-10-05T21:00:00+05:30";
const GRACE_UNTIL = "2026-10-05T23:00:00+05:30";
const DELIVERY_DATE = "2026-10-06";

export const SUNRISE = { id: "sunrise-towers", name: "Sunrise Towers" };
export const KRISHNA = { id: "krishna-kunj", name: "Krishna Kunj" };

/**
 * @typedef {"awaiting_approval" | "approved" | "payment_failed" | "locked"} OrderStatus
 * `awaiting_approval` is a weekly cycle the family hasn't approved yet (M3), shown on the board
 * beside real orders; the other three are order statuses (M4).
 * @typedef {{ id: string, flat: string, society_id: string, society: string, kind: "box" | "tiffin" | "addon",
 *   summary: string, slot_id: string, status: OrderStatus, total_paise: number, delivery_date: string }} Order
 * @typedef {{ id: string, hub_id: string, label: string, start_time: string, end_time: string, serves: string, capacity: number }} Slot
 */

/** @type {Slot[]} */
const SLOT_SEED = [
  {
    id: "slot-early",
    hub_id: "kharghar",
    label: "7 to 9 AM",
    start_time: "07:00",
    end_time: "09:00",
    serves: "Boxes",
    capacity: 40,
  },
  {
    id: "slot-am",
    hub_id: "kharghar",
    label: "8 to 10 AM",
    start_time: "08:00",
    end_time: "10:00",
    serves: "Boxes",
    capacity: 80,
  },
  {
    id: "slot-pm",
    hub_id: "kharghar",
    label: "5 to 7 PM",
    start_time: "17:00",
    end_time: "19:00",
    serves: "Tiffins and add-ons",
    capacity: 40,
  },
];

/** Bookings per delivery day and slot; a slot added later starts at 0. */
const BOOKED = {
  "2026-10-06": { "slot-early": 38, "slot-am": 52, "slot-pm": 14 },
  "2026-10-09": { "slot-early": 12, "slot-am": 18, "slot-pm": 6 },
};

/** Wallet balance of the households short at cut-off, by order id. */
const WALLET_PAISE = { "ord-a101": 13000, "ord-c302": 60000, "ord-b1104": 0 };

/**
 * The 58 orders of Tue 6 Oct: 9 awaiting approval, 46 approved, 3 payment needed; Sunrise Towers 46,
 * Krishna Kunj 12 (the shared story). The first few of each column are the ones in the design.
 * @returns {Order[]}
 */
function seedOrders() {
  /** @type {Order[]} */
  const out = [];
  /** @param {string} id @param {string} flat @param {typeof SUNRISE} s @param {Order["kind"]} kind @param {string} summary @param {string} slot @param {OrderStatus} status @param {number} total */
  const add = (id, flat, s, kind, summary, slot, status, total) =>
    out.push({
      id,
      flat,
      society_id: s.id,
      society: s.name,
      kind,
      summary,
      slot_id: slot,
      status,
      total_paise: total,
      delivery_date: DELIVERY_DATE,
    });
  add("ord-b402", "B-402", SUNRISE, "box", "Box · 14 items", "slot-am", "awaiting_approval", 54000);
  add("ord-c807", "C-807", SUNRISE, "box", "Box · 16 items", "slot-am", "awaiting_approval", 76000);
  add("ord-a1203", "A-1203", SUNRISE, "box", "Box · 14 items", "slot-am", "approved", 69000);
  add("ord-b205", "B-205", KRISHNA, "tiffin", "Tiffin × 2", "slot-pm", "approved", 22000);
  add("ord-a604", "A-604", SUNRISE, "box", "Box + 2 add-ons", "slot-am", "approved", 82000);
  add("ord-a101", "A-101", KRISHNA, "box", "Box · 9 items", "slot-am", "payment_failed", 41000);
  add("ord-c302", "C-302", SUNRISE, "box", "Box · 12 items", "slot-am", "payment_failed", 72000);
  add("ord-b1104", "B-1104", SUNRISE, "box", "Box · 13 items", "slot-am", "payment_failed", 65000);

  // The rest: 7 awaiting + 43 approved = 50, of which 40 Sunrise Towers and 10 Krishna Kunj.
  const taken = new Set(out.map((o) => o.flat));
  let krishna = 10;
  let sunrise = 40;
  for (let i = 0; i < 50; i++) {
    const society = i % 5 === 4 && krishna > 0 ? KRISHNA : sunrise > 0 ? SUNRISE : KRISHNA;
    if (society === KRISHNA) krishna--;
    else sunrise--;
    const wing = "ABC"[i % 3];
    let flat = "";
    for (let k = 0; ; k++) {
      flat = `${wing}-${1 + ((i * 5 + k) % 14)}${String(1 + ((i * 3 + k) % 6)).padStart(2, "0")}`;
      if (!taken.has(flat)) break;
    }
    taken.add(flat);
    const tiffin = i % 9 === 3;
    const items = 9 + ((i * 7) % 9);
    add(
      `ord-g${i}`,
      flat,
      society,
      tiffin ? "tiffin" : "box",
      tiffin ? `Tiffin × ${1 + (i % 3)}` : i % 7 === 5 ? "Box + 2 add-ons" : `Box · ${items} items`,
      tiffin ? "slot-pm" : "slot-am",
      i < 7 ? "awaiting_approval" : "approved",
      tiffin ? 22000 * (1 + (i % 3)) : 38000 + ((i * 37) % 52) * 1000,
    );
  }
  return out;
}

const state = () =>
  mockStore("orders", () => ({
    orders: seedOrders(),
    slots: structuredClone(SLOT_SEED),
    booked: structuredClone(BOOKED),
    skipped: 5,
    autoApproved: 3,
    lastRun: {
      cutoff_on: "2026-09-28",
      locked: 51,
      total: 51,
      duration_s: 12,
      grace_paid: 2,
      cancelled: 0,
    },
  }));

/** The slots that carry boxes, for the society delivery-day pickers (mocks/cycles.js). */
export function boxSlots() {
  return state()
    .slots.filter((s) => s.serves === "Boxes")
    .map((s) => ({ id: s.id, label: s.label }));
}

/** @param {string} t "HH:MM" */
const hour12 = (t) => {
  const [h, m] = t.split(":").map(Number);
  const hh = h % 12 === 0 ? 12 : h % 12;
  return `${hh}${m ? `:${String(m).padStart(2, "0")}` : ""}`;
};
/** "08:00","10:00" → "8 to 10 AM" ; "11:00","13:00" → "11 AM to 1 PM" */
const slotLabel = (/** @type {string} */ start, /** @type {string} */ end) => {
  const ampm = (/** @type {string} */ t) => (Number(t.split(":")[0]) < 12 ? "AM" : "PM");
  return ampm(start) === ampm(end)
    ? `${hour12(start)} to ${hour12(end)} ${ampm(end)}`
    : `${hour12(start)} ${ampm(start)} to ${hour12(end)} ${ampm(end)}`;
};

/**
 * Checks a slot body and returns field errors, or null when it is fine.
 * @param {any} body
 */
function slotProblem(body) {
  /** @type {{ field: string, message: string }[]} */
  const errors = [];
  const time = /^([01]\d|2[0-3]):[0-5]\d$/;
  if (!time.test(body?.start_time ?? ""))
    errors.push({ field: "start_time", message: "Use a time like 08:00." });
  if (!time.test(body?.end_time ?? ""))
    errors.push({ field: "end_time", message: "Use a time like 10:00." });
  if (errors.length === 0 && body.end_time <= body.start_time)
    errors.push({ field: "end_time", message: "Must be after the start." });
  if (!Number.isInteger(body?.capacity) || body.capacity < 1)
    errors.push({ field: "capacity", message: "Enter a whole number, 1 or more." });
  return errors.length ? problem(422, "request.invalid", "Check the slot", { errors }) : null;
}

/** Only hub managers and admins change slots. @param {Request} request */
const mayManage = (request) => ["admin", "hub_manager"].includes(roleOf(request) ?? "");

/** @param {Order[]} orders */
function counts(orders) {
  const n = (/** @type {OrderStatus} */ s) => orders.filter((o) => o.status === s).length;
  return {
    approved: n("approved") + n("locked"),
    locked: n("locked"),
    awaiting: n("awaiting_approval"),
    payment_needed: n("payment_failed"),
  };
}

export const ordersHandlers = [
  // GET /v1/orders?hub_id=&date=&status=&slot_id=&society_id=&kind= (M4 §3; hub staff see their hub)
  http.get("*/v1/orders", ({ request }) => {
    const q = new URL(request.url).searchParams;
    const s = state();
    const date = q.get("date");
    const items = s.orders.filter(
      (o) =>
        (!date || o.delivery_date === date) &&
        (!q.get("status") || o.status === q.get("status")) &&
        (!q.get("slot_id") || o.slot_id === q.get("slot_id")) &&
        (!q.get("society_id") || o.society_id === q.get("society_id")) &&
        (!q.get("kind") || o.kind === q.get("kind")),
    );
    return HttpResponse.json({ items, next_cursor: null });
  }),

  // GET /v1/slots?hub_id=&date= (M4 §3). `booked` is that day's bookings, `serves` is a console extra.
  http.get("*/v1/slots", ({ request }) => {
    const date = new URL(request.url).searchParams.get("date") ?? DELIVERY_DATE;
    const s = state();
    /** @type {Record<string, number>} */
    const day = /** @type {any} */ (s.booked)[date] ?? {};
    return HttpResponse.json({
      items: s.slots.map((slot) => ({ ...slot, booked: day[slot.id] ?? 0 })),
      next_cursor: null,
    });
  }),

  // POST /v1/admin/slots (M4 §3, idempotent). Body: { hub_id, start_time, end_time, capacity, serves }.
  http.post("*/v1/admin/slots", ({ request }) =>
    idempotent(request, (body) => {
      if (!mayManage(request)) return problem(403, "auth.forbidden", "Not allowed");
      const bad = slotProblem(body);
      if (bad) return bad;
      const s = state();
      /** @type {Slot} */
      const slot = {
        id: `slot-${crypto.randomUUID().slice(0, 8)}`,
        hub_id: body.hub_id ?? "kharghar",
        label: slotLabel(body.start_time, body.end_time),
        start_time: body.start_time,
        end_time: body.end_time,
        serves: body.serves === "Tiffins and add-ons" ? "Tiffins and add-ons" : "Boxes",
        capacity: body.capacity,
      };
      s.slots.push(slot);
      s.slots.sort((a, b) => a.start_time.localeCompare(b.start_time));
      return HttpResponse.json({ ...slot, booked: 0 }, { status: 201 });
    }),
  ),

  // PUT /v1/admin/slots/{id}: proposal. Lowering capacity never cancels booked orders.
  http.put("*/v1/admin/slots/:id", async ({ request, params }) => {
    if (!mayManage(request)) return problem(403, "auth.forbidden", "Not allowed");
    const body = /** @type {any} */ (await request.json());
    const bad = slotProblem(body);
    if (bad) return bad;
    const slot = state().slots.find((x) => x.id === params.id);
    if (!slot) return problem(404, "orders.slot_not_found", "No such slot");
    Object.assign(slot, {
      start_time: body.start_time,
      end_time: body.end_time,
      capacity: body.capacity,
      label: slotLabel(body.start_time, body.end_time),
    });
    return HttpResponse.json(slot);
  }),

  // GET /v1/hubs/{id}/delivery-dates : proposal, the next days the hub delivers (from society delivery days).
  http.get("*/v1/hubs/:hub/delivery-dates", () =>
    HttpResponse.json({ items: [{ date: "2026-10-06" }, { date: "2026-10-09" }] }),
  ),

  // GET /v1/hubs/{id}/cutoff?date= : proposal, the cut-off monitor's read side.
  http.get("*/v1/hubs/:hub/cutoff", () => {
    const s = state();
    const mine = s.orders.filter((o) => o.delivery_date === DELIVERY_DATE);
    const c = counts(mine);
    return HttpResponse.json({
      hub_id: "kharghar",
      delivery_date: DELIVERY_DATE,
      cutoff_at: CUTOFF_AT,
      grace_until: GRACE_UNTIL,
      as_of: STORY_NOW,
      counts: { ...c, auto_approved: s.autoApproved, skipped: s.skipped },
      last_run: s.lastRun,
      short: mine
        .filter((o) => o.status === "payment_failed")
        .map((o) => {
          const wallet = /** @type {Record<string, number>} */ (WALLET_PAISE)[o.id] ?? 0;
          return {
            order_id: o.id,
            flat: o.flat,
            society: o.society,
            box_paise: o.total_paise,
            wallet_paise: wallet,
            short_paise: o.total_paise - wallet,
          };
        }),
    });
  }),

  // POST /v1/hubs/{id}/cutoff/run (M4 §3, admin, idempotent): locks approved orders not yet locked.
  http.post("*/v1/hubs/:hub/cutoff/run", ({ request }) =>
    idempotent(request, () => {
      if (roleOf(request) !== "admin") return problem(403, "auth.forbidden", "Admins only");
      const s = state();
      const todo = s.orders.filter(
        (o) => o.delivery_date === DELIVERY_DATE && o.status === "approved",
      );
      for (const o of todo) o.status = "locked";
      const total = s.orders.filter((o) => o.status === "locked").length;
      s.lastRun = {
        cutoff_on: "2026-10-05",
        locked: total,
        total,
        duration_s: 4,
        grace_paid: 0,
        cancelled: 0,
      };
      return HttpResponse.json({
        newly_locked: todo.length,
        already_locked: total - todo.length,
        payment_failed: counts(s.orders).payment_needed,
      });
    }),
  ),
];

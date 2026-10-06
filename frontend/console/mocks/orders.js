// Orders, cut-off and slot mocks (M4 §3). `GET /v1/orders` (OrderSummaryOut), `GET /v1/slots`, `POST
// /v1/admin/slots` and `PATCH /v1/admin/slots/{id}` are the real contract's shapes. `GET /v1/hubs/{id}/cutoff`,
// `GET /v1/hubs/{id}/delivery-dates` and `POST /v1/hubs/{id}/cutoff/run` are proposals for the console (the
// spec has no read side for the cut-off monitor; PR #103 replaces the run with a job).
// Money is integer paise. The mock "now" is fixed to the shared story: Mon 5 Oct 2026, 6:46 PM IST,
// so the cut-off countdown reads "2 h 14 min" on every load.
import { HttpResponse, http } from "msw";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { SLOT_ID, SOCIETY_ID } from "./ids.js";
import { fieldError, idempotent, mockStore, mockUuid, problem, roleOf } from "./util.js";

export const STORY_NOW = "2026-10-05T18:46:00+05:30";
const CUTOFF_AT = "2026-10-05T21:00:00+05:30";
const GRACE_UNTIL = "2026-10-05T23:00:00+05:30";
const DELIVERY_DATE = "2026-10-06";

export const SUNRISE = { id: SOCIETY_ID["sunrise-towers"], name: "Sunrise Towers" };
export const KRISHNA = { id: SOCIETY_ID["krishna-kunj"], name: "Krishna Kunj" };

/**
 * @typedef {"awaiting_approval" | "approved" | "payment_failed" | "locked"} OrderStatus
 * `awaiting_approval` is a weekly cycle the family hasn't approved yet (M3), shown on the board
 * beside real orders; the other three are order statuses (M4).
 * What the mock keeps of an order (the cut-off monitor needs the flat and the society); `/v1/orders`
 * answers with the real OrderSummaryOut, which has neither.
 * @typedef {{ id: string, flat: string, society_id: string, society: string, kind: "box" | "tiffin" | "addon",
 *   summary: string, slot_id: string, status: OrderStatus, total_paise: number, delivery_date: string }} Order
 * A delivery slot as the real SlotOut has it, plus `active` (a slot switched off is not listed); times are "HH:MM:SS".
 * @typedef {{ id: string, hub_id: string, label: string, kind: "box" | "tiffin", start: string, end: string,
 *   capacity: number, active: boolean }} Slot
 */

/** @type {Slot[]} */
const SLOT_SEED = [
  {
    id: SLOT_ID.early,
    hub_id: KHARGHAR_HUB_ID,
    label: "Early 7 to 9 AM",
    kind: "box",
    start: "07:00:00",
    end: "09:00:00",
    capacity: 40,
    active: true,
  },
  {
    id: SLOT_ID.am,
    hub_id: KHARGHAR_HUB_ID,
    label: "Morning 8 to 10 AM",
    kind: "box",
    start: "08:00:00",
    end: "10:00:00",
    capacity: 80,
    active: true,
  },
  {
    id: SLOT_ID.pm,
    hub_id: KHARGHAR_HUB_ID,
    label: "Evening 5 to 7 PM",
    kind: "tiffin",
    start: "17:00:00",
    end: "19:00:00",
    capacity: 40,
    active: true,
  },
];

/** Bookings per delivery day and slot; a slot added later starts at 0. */
const BOOKED = {
  "2026-10-06": { [SLOT_ID.early]: 38, [SLOT_ID.am]: 52, [SLOT_ID.pm]: 14 },
  "2026-10-09": { [SLOT_ID.early]: 12, [SLOT_ID.am]: 18, [SLOT_ID.pm]: 6 },
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
  add(
    "ord-b402",
    "B-402",
    SUNRISE,
    "box",
    "Box · 14 items",
    SLOT_ID.am,
    "awaiting_approval",
    54000,
  );
  add(
    "ord-c807",
    "C-807",
    SUNRISE,
    "box",
    "Box · 16 items",
    SLOT_ID.am,
    "awaiting_approval",
    76000,
  );
  add("ord-a1203", "A-1203", SUNRISE, "box", "Box · 14 items", SLOT_ID.am, "approved", 69000);
  add("ord-b205", "B-205", KRISHNA, "tiffin", "Tiffin × 2", SLOT_ID.pm, "approved", 22000);
  add("ord-a604", "A-604", SUNRISE, "box", "Box + 2 add-ons", SLOT_ID.am, "approved", 82000);
  add("ord-a101", "A-101", KRISHNA, "box", "Box · 9 items", SLOT_ID.am, "payment_failed", 41000);
  add("ord-c302", "C-302", SUNRISE, "box", "Box · 12 items", SLOT_ID.am, "payment_failed", 72000);
  add("ord-b1104", "B-1104", SUNRISE, "box", "Box · 13 items", SLOT_ID.am, "payment_failed", 65000);

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
      tiffin ? SLOT_ID.pm : SLOT_ID.am,
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

/** The active slots that carry boxes, for the society delivery-day pickers (mocks/cycles.js). */
export function boxSlots() {
  return state()
    .slots.filter((s) => s.kind === "box" && s.active)
    .map(({ id, label, start, end }) => ({ id, label, start, end }));
}

/** The real SlotOut for a day: `booked` is that day's bookings. @param {Slot} slot @param {string} date */
function slotOut(slot, date) {
  /** @type {Record<string, number>} */
  const day = /** @type {any} */ (state().booked)[date] ?? {};
  const booked = day[slot.id] ?? 0;
  const { active: _active, ...rest } = slot;
  return { ...rest, booked, available: Math.max(slot.capacity - booked, 0) };
}

/** "HH:MM" of a time the real API accepts ("07:00" or "07:00:00"), or null. @param {unknown} v */
const time = (v) =>
  typeof v === "string" && /^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$/.test(v) ? v.slice(0, 5) : null;

/** Only hub managers and admins change slots. @param {Request} request */
const mayManage = (request) => ["admin", "hub_manager"].includes(roleOf(request) ?? "");

/**
 * The request fields the real SlotIn / SlotPatch refuse (extra, missing or out of range), as 422 errors.
 * @param {any} body @param {"in" | "patch"} shape
 */
function slotBodyErrors(body, shape) {
  /** @type {ReturnType<typeof fieldError>[]} */
  const errors = [];
  const allowed =
    shape === "in"
      ? ["hub_id", "label", "kind", "start", "end", "capacity"]
      : ["capacity", "active"];
  if (typeof body !== "object" || body === null || Array.isArray(body))
    return [fieldError([], "Input should be a valid dictionary", "model_attributes_type")];
  for (const k of Object.keys(body))
    if (!allowed.includes(k))
      errors.push(fieldError(k, "Extra inputs are not permitted", "extra_forbidden"));
  if (shape === "in") {
    if (typeof body.hub_id !== "string")
      errors.push(fieldError("hub_id", "Field required", "missing"));
    if (typeof body.label !== "string" || body.label.length < 1 || body.label.length > 40)
      errors.push(
        fieldError("label", "String should have at least 1 character", "string_too_short"),
      );
    if (body.kind !== undefined && body.kind !== "box" && body.kind !== "tiffin")
      errors.push(fieldError("kind", "Input should be 'box' or 'tiffin'", "enum"));
    for (const f of ["start", "end"])
      if (!time(body[f]))
        errors.push(fieldError(f, "Input should be a valid time", "time_parsing"));
    if (body.capacity === undefined)
      errors.push(fieldError("capacity", "Field required", "missing"));
  }
  if (
    body.capacity !== undefined &&
    body.capacity !== null &&
    (!Number.isInteger(body.capacity) || body.capacity < 1 || body.capacity > 2000)
  )
    errors.push(fieldError("capacity", "Input should be between 1 and 2000", "greater_than_equal"));
  if (body.active !== undefined && body.active !== null && typeof body.active !== "boolean")
    errors.push(fieldError("active", "Input should be a valid boolean", "bool_parsing"));
  return errors;
}

/** The real OrderSummaryOut of the mock's order number `n`; UTC timestamps with Z. @param {Order} o @param {number} n */
function orderOut(o, n) {
  const slot = state().slots.find((x) => x.id === o.slot_id);
  const locked = o.status === "locked";
  return {
    id: mockUuid("b300", n + 1),
    kind: o.kind,
    status: o.status,
    cycle_id: o.kind === "box" ? mockUuid("b500", n + 1) : null,
    household_id: mockUuid("b400", n + 1),
    hub_id: KHARGHAR_HUB_ID,
    society_id: o.society_id,
    delivery_date: o.delivery_date,
    slot: {
      id: o.slot_id,
      label: slot?.label ?? "Slot",
      start: slot?.start ?? "08:00:00",
      end: slot?.end ?? "10:00:00",
    },
    cutoff_at: "2026-10-05T15:30:00Z",
    subtotal_paise: o.total_paise,
    fees_paise: 0,
    discount_paise: 0,
    total_paise: o.total_paise,
    version: 1,
    created_at: "2026-10-03T12:30:00Z",
    locked_at: locked ? "2026-10-05T15:30:00Z" : null,
  };
}

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
  // GET /v1/orders?hub_id=&from=&to=&status=&cursor=&limit= (M4 §3; hub staff pass hub_id) → Page[OrderSummaryOut],
  // default 20, at most 100. A weekly cycle nobody approved yet is not an order, so it is not listed.
  http.get("*/v1/orders", ({ request }) => {
    const q = new URL(request.url).searchParams;
    if (!q.get("hub_id") && roleOf(request) !== null)
      return problem(422, "orders.hub_required", "Hub staff pass hub_id.");
    const limit = Math.min(Number(q.get("limit")) || 20, 100);
    const offset = Number(q.get("cursor")) || 0;
    const all = state()
      .orders.map((o, n) => ({ o, n }))
      .filter(
        ({ o }) =>
          o.status !== "awaiting_approval" &&
          (!q.get("from") || o.delivery_date >= /** @type {string} */ (q.get("from"))) &&
          (!q.get("to") || o.delivery_date <= /** @type {string} */ (q.get("to"))) &&
          (!q.get("status") || o.status === q.get("status")),
      );
    return HttpResponse.json({
      items: all.slice(offset, offset + limit).map(({ o, n }) => orderOut(o, n)),
      next_cursor: offset + limit < all.length ? String(offset + limit) : null,
    });
  }),

  // GET /v1/slots?hub_id=&date= (M4 §3) → SlotsOut: { date, items: [SlotOut] }, the active slots.
  http.get("*/v1/slots", ({ request }) => {
    const q = new URL(request.url).searchParams;
    const date = q.get("date") ?? DELIVERY_DATE;
    return HttpResponse.json({
      date,
      items: state()
        .slots.filter((s) => s.active && (!q.get("hub_id") || s.hub_id === q.get("hub_id")))
        .map((s) => slotOut(s, date)),
    });
  }),

  // POST /v1/admin/slots (M4 §3, idempotent). Body SlotIn: { hub_id, label, kind?, start, end, capacity }.
  // 409 orders.slot_exists (the label is unique per hub), 422 orders.slot_invalid (it must end after it starts).
  http.post("*/v1/admin/slots", ({ request }) =>
    idempotent(request, (body) => {
      if (!mayManage(request)) return problem(403, "auth.forbidden", "Not allowed");
      const errors = slotBodyErrors(body, "in");
      if (errors.length)
        return problem(422, "request.invalid", "Request validation failed", { errors });
      const [start, end] = [time(body.start), time(body.end)];
      if (/** @type {string} */ (end) <= /** @type {string} */ (start))
        return problem(422, "orders.slot_invalid", "The slot must end after it starts.");
      const s = state();
      if (s.slots.some((x) => x.hub_id === body.hub_id && x.label === body.label))
        return problem(409, "orders.slot_exists", "This hub already has a slot with that label.");
      /** @type {Slot} */
      const slot = {
        id: mockUuid("b200", s.slots.length + 1),
        hub_id: body.hub_id,
        label: body.label,
        kind: body.kind ?? "box",
        start: `${start}:00`,
        end: `${end}:00`,
        capacity: body.capacity,
        active: true,
      };
      s.slots.push(slot);
      s.slots.sort((a, b) => a.start.localeCompare(b.start));
      return HttpResponse.json(slotOut(slot, DELIVERY_DATE), { status: 201 });
    }),
  ),

  // PATCH /v1/admin/slots/{id} (M4 §3): { capacity?, active? } only; the times cannot change. Lowering
  // the capacity never cancels booked orders. → SlotOut.
  http.patch("*/v1/admin/slots/:id", async ({ request, params }) => {
    if (!mayManage(request)) return problem(403, "auth.forbidden", "Not allowed");
    const body = /** @type {any} */ (await request.json().catch(() => null));
    const errors = slotBodyErrors(body, "patch");
    if (errors.length)
      return problem(422, "request.invalid", "Request validation failed", { errors });
    const slot = state().slots.find((x) => x.id === params.id);
    if (!slot) return problem(422, "orders.slot_invalid", "No such slot.");
    if (typeof body.capacity === "number") slot.capacity = body.capacity;
    if (typeof body.active === "boolean") slot.active = body.active;
    return HttpResponse.json(slotOut(slot, DELIVERY_DATE));
  }),

  // GET /v1/hubs/{id}/delivery-dates : @proposal (not in the contract yet), the next days the hub delivers (from society delivery days).
  http.get("*/v1/hubs/:hub/delivery-dates", () =>
    HttpResponse.json({ items: [{ date: "2026-10-06" }, { date: "2026-10-09" }] }),
  ),

  // GET /v1/hubs/{id}/cutoff?date= : @proposal (not in the contract yet), the cut-off monitor's read side.
  http.get("*/v1/hubs/:hub/cutoff", () => {
    const s = state();
    const mine = s.orders.filter((o) => o.delivery_date === DELIVERY_DATE);
    const c = counts(mine);
    return HttpResponse.json({
      hub_id: KHARGHAR_HUB_ID,
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

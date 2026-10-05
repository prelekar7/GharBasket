// M4 orders mocks (docs/modules/M4-orders.md §3): the household's orders with their timeline.
// The spec lists tables, not JSON; these shapes are our proposal. Statuses follow handbook §6.2
// (box) and §6.3 (tiffin). Names come back in the caller's language, like the M3 mocks.
import { HttpResponse, http } from "msw";
import { caller, isNewUser } from "./handlers.js";
import { deliveryStage } from "./tracking.js";

const problem = (
  /** @type {number} */ status,
  /** @type {string} */ code,
  /** @type {string} */ title,
) =>
  HttpResponse.json(
    { type: "about:blank", title, status, code },
    { status, headers: { "content-type": "application/problem+json" } },
  );
const langOf = (/** @type {Request} */ r) =>
  (r.headers.get("accept-language") ?? "").startsWith("hi") ? "hi" : "en";

/**
 * The designs' Tuesday: the box is on the way (it reaches the captain, then the door, as the demo
 * day runs: mocks/tracking.js), a tiffin tomorrow, next week's box to approve.
 */
function orders(/** @type {"en" | "hi"} */ lang) {
  const hi = lang === "hi";
  const address = { flat: "B-402", society: "Sunrise Towers" };
  const { status } = deliveryStage();
  const later = /** @type {{ to_status: string, at: string }[]} */ ([]);
  if (status !== "DISPATCHED")
    later.push({ to_status: "AT_SOCIETY", at: "2026-10-06T08:40:00+05:30" });
  if (status === "DELIVERED")
    later.push({ to_status: "DELIVERED", at: "2026-10-06T09:30:00+05:30" });
  return [
    {
      id: "ord-2026-10-06",
      kind: "box",
      status,
      delivery_date: "2026-10-06",
      eta_by: "10:00",
      items_count: 14,
      total_paise: 69000,
      paid_from: "wallet",
      address,
      hub: { name: hi ? "खारघर" : "Kharghar" },
      captain: { name: hi ? "मीना ताई" : "Meena tai", phone_e164: "+919800000002" },
      events: [
        { to_status: "APPROVED", at: "2026-10-01T19:12:00+05:30" },
        { to_status: "LOCKED", at: "2026-10-05T21:00:00+05:30" },
        { to_status: "PACKED", at: "2026-10-06T06:10:00+05:30" },
        { to_status: "DISPATCHED", at: "2026-10-06T07:45:00+05:30" },
        ...later,
      ],
      version: 7,
    },
    {
      id: "ord-tif-2026-10-07",
      kind: "tiffin",
      status: "SCHEDULED",
      delivery_date: "2026-10-07",
      total_paise: 11000,
      paid_from: "wallet",
      address,
      cook: { name: hi ? "लता ताई" : "Lata tai" },
      dish: { name: hi ? "राजमा चावल" : "rajma chawal" },
      events: [],
      version: 1,
    },
    {
      id: "ord-2026-10-13",
      kind: "box",
      status: "AWAITING_APPROVAL",
      delivery_date: "2026-10-13",
      plan_ready_on: "2026-10-10",
      total_paise: 69000,
      address,
      events: [],
      version: 1,
    },
  ];
}

/** Tiffins booked from the Week day sheet (POST /v1/orders, kind "tiffin"); they survive reloads. */
const TIFFINS_KEY = "gb-mock-tiffins";
/** @returns {{ id: string, key: string, delivery_date: string, slot: string, kitchen_id: string, qty?: number }[]} */
export const bookedTiffins = () => JSON.parse(localStorage.getItem(TIFFINS_KEY) ?? "[]");
/**
 * The home kitchen that offers a tiffin to the society this week (M8, priced by M2): the backend
 * seed's only kitchen, Lata Shinde at the Kharghar hub. ₹110 to the family, ₹70 of it to her.
 */
export const TIFFIN_OFFER = {
  kitchen_id: "kit-lata",
  slot: "lunch",
  cook: ["Lata tai", "लता ताई"],
  dish: ["Chapati-bhaji thali", "चपाती-भाजी थाली"],
  items: ["Chapati, bhaji, dal, rice, salad", "चपाती, भाजी, दाल, चावल, सलाद"],
  price_paise: 11000,
};

const ISSUE_KINDS = ["missing", "damaged", "late", "other"];
/** @type {Record<string, any>} */
const issuesByKey = {};

export const orderHandlers = [
  http.get("*/v1/orders", ({ request }) => {
    if (!caller(request)) return problem(401, "auth.invalid_token", "Sign in again");
    // A household still in onboarding has no orders yet.
    if (isNewUser(caller(request) ?? ""))
      return HttpResponse.json({ items: [], next_cursor: null });
    const hi = langOf(request) === "hi";
    const booked = bookedTiffins().map((b) => ({
      id: b.id,
      kind: "tiffin",
      status: "SCHEDULED",
      delivery_date: b.delivery_date,
      slot: b.slot,
      qty: b.qty ?? 1,
      total_paise: (b.qty ?? 1) * TIFFIN_OFFER.price_paise,
      paid_from: "wallet",
      address: { flat: "B-402", society: "Sunrise Towers" },
      cook: { name: TIFFIN_OFFER.cook[hi ? 1 : 0] },
      dish: { name: TIFFIN_OFFER.dish[hi ? 1 : 0] },
      events: [],
      version: 1,
    }));
    const items = [...orders(langOf(request)), ...booked].sort((a, b) =>
      a.delivery_date.localeCompare(b.delivery_date),
    );
    return HttpResponse.json({ items, next_cursor: null });
  }),

  // A direct order (M4 §3): only tiffins and add-ons; boxes come from the weekly cycle.
  http.post("*/v1/orders", async ({ request }) => {
    const key = request.headers.get("idempotency-key");
    if (!key)
      return problem(
        400,
        "platform.idempotency_key_required",
        "Idempotency-Key header is required",
      );
    const list = bookedTiffins();
    const replay = list.find((b) => b.key === key);
    if (replay) return HttpResponse.json(replay, { status: 201 });
    const body =
      /** @type {{ kind?: string, delivery_date?: string, slot?: string, kitchen_id?: string, qty?: number }} */ (
        await request.json()
      );
    const qty = body.qty ?? 1;
    if (
      body.kind !== "tiffin" ||
      !body.delivery_date ||
      body.kitchen_id !== TIFFIN_OFFER.kitchen_id
    )
      return problem(422, "orders.invalid_order", "Only a tiffin from this week's kitchen");
    if (!(Number.isInteger(qty) && qty >= 1 && qty <= 6))
      return problem(422, "orders.qty_invalid", "Book between 1 and 6 tiffins");
    if (list.some((b) => b.delivery_date === body.delivery_date && b.slot === body.slot))
      return problem(409, "orders.already_booked", "A tiffin is already booked for that meal");
    const order = {
      id: `ord-tif-${body.delivery_date}-${body.slot}`,
      key,
      delivery_date: body.delivery_date,
      slot: body.slot ?? TIFFIN_OFFER.slot,
      kitchen_id: body.kitchen_id,
      qty,
      total_paise: qty * TIFFIN_OFFER.price_paise,
    };
    localStorage.setItem(TIFFINS_KEY, JSON.stringify([...list, order]));
    return HttpResponse.json(order, { status: 201 });
  }),

  http.get("*/v1/orders/:id", ({ request, params }) => {
    const order = orders(langOf(request)).find((o) => o.id === params.id);
    return order ? HttpResponse.json(order) : problem(404, "orders.not_found", "No such order");
  }),

  // Our proposal (not yet in M4): a delivery problem the hub calls back about.
  http.post("*/v1/orders/:id/issues", async ({ request, params }) => {
    const key = request.headers.get("idempotency-key");
    if (!key)
      return problem(
        400,
        "platform.idempotency_key_required",
        "Idempotency-Key header is required",
      );
    if (issuesByKey[key]) return HttpResponse.json(issuesByKey[key], { status: 201 });
    const body = /** @type {{ kind?: string, note?: string }} */ (await request.json());
    if (!ISSUE_KINDS.includes(body.kind ?? ""))
      return problem(422, "orders.invalid_issue", "Pick what went wrong");
    issuesByKey[key] = {
      id: `iss-${Object.keys(issuesByKey).length + 1}`,
      order_id: params.id,
      kind: body.kind,
      status: "open",
      callback_within_minutes: 120,
    };
    return HttpResponse.json(issuesByKey[key], { status: 201 });
  }),
];

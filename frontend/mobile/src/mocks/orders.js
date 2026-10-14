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

/** A tiffin for the box's day comes with the box: cooked while the van drives, then the captain brings both. */
const TIFFIN_WITH_BOX = /** @type {Record<string, string>} */ ({
  DISPATCHED: "PREPARING",
  AT_SOCIETY: "PICKED_UP",
  DELIVERED: "DELIVERED",
});

/** Ratings by order id (POST /v1/orders/{id}/rating, our proposal for M4 and M8); they survive reloads. */
const RATINGS_KEY = "gb-mock-ratings";
/** @returns {Record<string, { key: string, score: number, tags: string[], comment: string | null }>} */
const ratings = () => JSON.parse(localStorage.getItem(RATINGS_KEY) ?? "{}");
/** A new demo day (sign-in) has nothing rated yet. */
export const clearRatings = () => localStorage.removeItem(RATINGS_KEY);
const RATING_TAGS = {
  box: ["fresh", "on_time", "packed_well", "missing"],
  tiffin: ["tasty", "hot", "portion", "oily"],
};

/** The seeded orders and the booked tiffins, soonest first, each with its rating if any. */
function allOrders(/** @type {"en" | "hi"} */ lang) {
  const hi = lang === "hi";
  const seeded = orders(lang);
  const box = seeded[0];
  const booked = bookedTiffins().map((b) => {
    const status =
      b.delivery_date === box.delivery_date ? TIFFIN_WITH_BOX[box.status] : "SCHEDULED";
    return {
      id: b.id,
      kind: "tiffin",
      status,
      delivery_date: b.delivery_date,
      slot: b.slot,
      qty: b.qty ?? 1,
      total_paise: (b.qty ?? 1) * TIFFIN_OFFER.price_paise,
      paid_from: "wallet",
      address: { flat: "B-402", society: "Sunrise Towers" },
      kitchen_id: b.kitchen_id,
      cook: { name: TIFFIN_OFFER.cook[hi ? 1 : 0] },
      dish: { name: TIFFIN_OFFER.dish[hi ? 1 : 0] },
      events:
        status === "DELIVERED"
          ? [{ to_status: "DELIVERED", at: `${b.delivery_date}T09:30:00+05:30` }]
          : [],
      version: 1,
    };
  });
  const rated = ratings();
  return [...seeded, ...booked]
    .map((o) => ({ ...o, rating: rated[o.id] ? { score: rated[o.id].score } : null }))
    .sort((a, b) => a.delivery_date.localeCompare(b.delivery_date));
}

const ISSUE_KINDS = ["missing", "damaged", "late", "other"];
/** @type {Record<string, any>} */
const issuesByKey = {};

export const orderHandlers = [
  http.get("*/v1/orders", ({ request }) => {
    if (!caller(request)) return problem(401, "auth.invalid_token", "Sign in again");
    // A household still in onboarding has no orders yet.
    if (isNewUser(caller(request) ?? ""))
      return HttpResponse.json({ items: [], next_cursor: null });
    const items = allOrders(langOf(request));
    return HttpResponse.json({ items, next_cursor: null });
  }),

  // A direct order (M4 §3): only tiffins and add-ons; boxes come from the weekly cycle.
  http.post("*/v1/orders", async ({ request }) => {
    const key = request.headers.get("idempotency-key");
    if (!key)
      return problem(400, "request.idempotency_key_required", "Idempotency-Key header is required");
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
    const order = allOrders(langOf(request)).find((o) => o.id === params.id);
    return order ? HttpResponse.json(order) : problem(404, "orders.not_found", "No such order");
  }),

  // Our proposal: one rating per delivered order, 1–5 with tags and words. M8 keeps the
  // kitchen's and captain's averages; they never see who rated.
  http.post("*/v1/orders/:id/rating", async ({ request, params }) => {
    const key = request.headers.get("idempotency-key");
    if (!key)
      return problem(400, "request.idempotency_key_required", "Idempotency-Key header is required");
    const id = String(params.id);
    const rated = ratings();
    if (rated[id]?.key === key)
      return HttpResponse.json({ order_id: id, ...rated[id] }, { status: 201 });
    if (rated[id]) return problem(409, "orders.already_rated", "This delivery is rated already");
    const order = allOrders("en").find((o) => o.id === id);
    if (!order) return problem(404, "orders.not_found", "No such order");
    if (order.status !== "DELIVERED")
      return problem(409, "orders.not_delivered", "Rate it once it reaches you");
    const body = /** @type {{ score?: number, tags?: string[], comment?: string }} */ (
      await request.json()
    );
    const allowed = RATING_TAGS[order.kind === "tiffin" ? "tiffin" : "box"];
    if (
      !(Number.isInteger(body.score) && Number(body.score) >= 1 && Number(body.score) <= 5) ||
      !(body.tags ?? []).every((t) => allowed.includes(t))
    )
      return problem(422, "orders.invalid_rating", "Pick 1 to 5");
    // Read again after the await: the box and the tiffin are rated at the same time.
    const all = ratings();
    all[id] = {
      key,
      score: Number(body.score),
      tags: body.tags ?? [],
      comment: body.comment?.trim() || null,
    };
    localStorage.setItem(RATINGS_KEY, JSON.stringify(all));
    return HttpResponse.json({ order_id: id, ...all[id] }, { status: 201 });
  }),

  // Our proposal (not yet in M4): a delivery problem the hub calls back about.
  http.post("*/v1/orders/:id/issues", async ({ request, params }) => {
    const key = request.headers.get("idempotency-key");
    if (!key)
      return problem(400, "request.idempotency_key_required", "Idempotency-Key header is required");
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

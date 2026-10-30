// M4 orders mocks (docs/modules/M4-orders.md §3.1, frozen for S2): OrderSummaryOut for the list,
// OrderOut (with lines and timeline) for one order, exactly as the API sends them. Statuses are
// lowercase (handbook §6.2); times are UTC. What §3.1 does not have lives under its own proposed
// endpoint: issues (mocks/tracking.js has the van). A tiffin's dishes and kitchen are on its order;
// the cook's name is the card (mocks/partners.js); ratings are M8 §3.5 (POST and GET /v1/ratings).
import { HttpResponse, http } from "msw";
import { addonBox } from "./addons.js";
import { caller, isNewUser } from "./handlers.js";
import { deliveryStage, TODAY_BOX_ID } from "./tracking.js";

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

const HOUSEHOLD_ID = "0199b000-0000-7000-8000-000000000001";
const HUB_ID = "0199a000-0000-7000-8000-00000000a001";
const SOCIETY_ID = "0199a000-0000-7000-8000-00000000b001";
const BOX_SLOT = {
  id: "0199a000-0000-7000-8000-00000000c001",
  label: "Morning 8-10",
  start: "08:00:00",
  end: "10:00:00",
};
const TIFFIN_SLOT = {
  id: "0199a000-0000-7000-8000-00000000c002",
  label: "Lunch 12-2",
  start: "12:00:00",
  end: "14:00:00",
};
const PAST_BOX_ID = "0199b000-0000-7000-8000-0000000000a5";
const TIFFIN_TOMORROW_ID = "0199b000-0000-7000-8000-0000000000b7";

/** A UTC timestamp from an India date and clock time. @param {string} date @param {string} hhmm */
const utc = (date, hhmm) =>
  new Date(`${date}T${hhmm}:00+05:30`).toISOString().replace(".000Z", "Z");
/** 21:00 India time on the day before a delivery: the cut-off is a property of the delivery date. @param {string} date */
const cutoffOf = (date) =>
  utc(new Date(Date.parse(`${date}T12:00:00Z`) - 86_400_000).toISOString().slice(0, 10), "21:00");

/** The box, line by line: slug, [English, Hindi], steps, step label, price per step. They add up to ₹690 (design HcOrders: 14 items).
 * @type {[string, [string, string], number, [number, string, string], number][]}
 */
const BOX_LINES = [
  ["bhindi", ["Bhindi", "भिंडी"], 2, [250, "g", "250 g"], 2000],
  ["palak", ["Palak", "पालक"], 2, [1, "bunch", "1 bunch"], 1500],
  ["methi", ["Methi", "मेथी"], 2, [1, "bunch", "1 bunch"], 2000],
  ["tomato", ["Tomato", "टमाटर"], 2, [500, "g", "500 g"], 3000],
  ["onion", ["Onion", "प्याज़"], 2, [1, "kg", "1 kg"], 3500],
  ["potato", ["Potato", "आलू"], 2, [1, "kg", "1 kg"], 3000],
  ["carrot", ["Carrot", "गाजर"], 1, [500, "g", "500 g"], 4000],
  ["bottle-gourd", ["Lauki", "लौकी"], 1, [1, "piece", "1 piece"], 4500],
  ["tindora", ["Tindora", "टिंडोरा"], 1, [250, "g", "250 g"], 4000],
  ["cauliflower", ["Cauliflower", "फूलगोभी"], 1, [1, "piece", "1 piece"], 6000],
  ["coriander", ["Coriander", "धनिया"], 2, [1, "bunch", "1 bunch"], 1000],
  ["basmati-rice", ["Basmati rice", "बासमती चावल"], 1, [1, "kg", "1 kg"], 11000],
  ["toor-dal", ["Toor dal", "तूर दाल"], 1, [500, "g", "500 g"], 6500],
  ["green-chilli", ["Green chilli", "हरी मिर्च"], 1, [100, "g", "100 g"], 1000],
];

/** The lines of a box in the caller's language: the real OrderLineOut, with UUID product ids. @param {"en" | "hi"} lang */
const boxLines = (lang) =>
  BOX_LINES.map(([slug, name, qty, [stepQty, unit, label], price], i) => ({
    product_id: `0199c000-0000-7000-8000-${String(i + 1).padStart(12, "0")}`,
    slug,
    name: name[lang === "hi" ? 1 : 0],
    kind: "box",
    qty,
    step: {
      qty: stepQty,
      unit,
      label: lang === "hi" ? label.replace(" kg", " किग्रा").replace(" g", " ग्रा") : label,
    },
    unit_price_paise: price,
    total_paise: qty * price,
    reason_code: slug === "methi" ? "in_season" : null,
    reason_params: slug === "methi" ? { item: name[lang === "hi" ? 1 : 0] } : null,
    substituted_from: null,
    shortage_qty: 0,
  }));
const BOX_TOTAL = BOX_LINES.reduce((sum, l) => sum + l[2] * l[4], 0);

/** Who does what in the timeline: the customer approves, the system locks at the cut-off, the hub does the rest. */
const ACTORS = /** @type {const} */ ({
  approved: "customer",
  locked: "system",
  payment_failed: "system",
  cancelled: "customer",
});
/**
 * The timeline of a box delivered on `date` as far as `status`, with the day's usual hours.
 * @param {string} date @param {string} status
 */
function timelineOf(date, status) {
  const prior = new Date(Date.parse(`${date}T12:00:00Z`) - 86_400_000).toISOString().slice(0, 10);
  const steps = /** @type {[string, string][]} */ ([
    ["approved", utc("2026-10-01", "19:12")],
    ["locked", utc(prior, "21:00")],
    ["picking", utc(date, "05:40")],
    ["packed", utc(date, "06:10")],
    ["dispatched", utc(date, "07:45")],
    ["at_society", utc(date, "08:40")],
    ["delivered", utc(date, "09:30")],
  ]);
  if (status === "payment_failed")
    return [steps[0], ["payment_failed", utc(prior, "21:00")]].map(([s, at]) => ({
      status: s,
      at,
      actor: /** @type {Record<string, string>} */ (ACTORS)[s] ?? "hub",
    }));
  const upTo = steps.findIndex(([s]) => s === status);
  return steps.slice(0, upTo + 1).map(([s, at]) => ({
    status: s,
    at,
    actor: /** @type {Record<string, string>} */ (ACTORS)[s] ?? "hub",
  }));
}

/** Where the wallet stands at the cut-off, from the weekly-cycle mock (98000 00004 starts short). */
const walletWasShort = () => {
  try {
    return JSON.parse(localStorage.getItem("gb-mock-cycle") ?? "null")?.status === "payment_failed";
  } catch {
    return false;
  }
};

/**
 * One order as OrderOut. `lines` and `timeline` go too; the list drops them (OrderSummaryOut).
 * @param {{ id: string, kind: "box" | "tiffin", status: string, delivery_date: string, slot: typeof BOX_SLOT, total_paise: number,
 *   cycle_id: string | null, lines: object[], timeline: object[], created_at: string }} o
 */
const full = (o) => ({
  id: o.id,
  kind: o.kind,
  status: o.status,
  cycle_id: o.cycle_id,
  household_id: HOUSEHOLD_ID,
  hub_id: HUB_ID,
  society_id: SOCIETY_ID,
  delivery_date: o.delivery_date,
  slot: o.slot,
  cutoff_at: cutoffOf(o.delivery_date),
  lines: o.lines,
  subtotal_paise: o.total_paise,
  fees_paise: 0,
  discount_paise: 0,
  total_paise: o.total_paise,
  version: o.timeline.length + 1,
  created_at: o.created_at,
  locked_at: /** @type {any[]} */ (o.timeline).find((e) => e.status === "locked")?.at ?? null,
  timeline: o.timeline,
});

/** `full`, for the add-ons mock (mocks/addons.js). */
export const fullOrder = (/** @type {Parameters<typeof full>[0]} */ o) => full(o);

/**
 * The designs' Tuesday: the box is on the way (it reaches the captain, then the door, as the demo
 * day runs: mocks/tracking.js), a tiffin tomorrow, and last week's box delivered. The week still
 * to approve is M3's cycle, not an order.
 */
function orders(/** @type {"en" | "hi"} */ lang) {
  const { status: moving } = deliveryStage();
  const status = walletWasShort() ? "payment_failed" : moving;
  return [
    full({
      id: TODAY_BOX_ID,
      kind: "box",
      status,
      delivery_date: "2026-10-06",
      slot: BOX_SLOT,
      total_paise: BOX_TOTAL,
      cycle_id: "0199b000-0000-7000-8000-0000000000c6",
      lines: boxLines(lang),
      timeline: timelineOf("2026-10-06", status),
      created_at: utc("2026-10-01", "19:12"),
    }),
    {
      ...full({
        id: TIFFIN_TOMORROW_ID,
        kind: "tiffin",
        status: "approved",
        delivery_date: "2026-10-07",
        slot: TIFFIN_SLOT,
        total_paise: 11000,
        cycle_id: null,
        lines: [],
        timeline: [{ status: "approved", at: utc("2026-10-04", "10:05"), actor: "customer" }],
        created_at: utc("2026-10-04", "10:05"),
      }),
      ...tiffinOf("2026-10-07", TIFFIN_OFFER.slot, lang),
    },
    full({
      id: PAST_BOX_ID,
      kind: "box",
      status: "delivered",
      delivery_date: "2026-09-29",
      slot: BOX_SLOT,
      total_paise: BOX_TOTAL,
      cycle_id: "0199b000-0000-7000-8000-0000000000c5",
      lines: boxLines(lang),
      timeline: timelineOf("2026-09-29", "delivered"),
      created_at: utc("2026-09-24", "19:30"),
    }),
    addonBox(BOX_SLOT, lang),
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
  // The recipes of the menu (TiffinMenuOut.dishes): the real menu has these, not a thali name.
  dishes: [
    ["Chapati", "चपाती"],
    ["Bhaji", "भाजी"],
    ["Dal", "दाल"],
    ["Rice", "चावल"],
  ],
  price_paise: 11000,
};

/** The id of the approved lunch menu of a date (GET /v1/tiffin-menus): `…<yyyymmdd>0001`. @param {string} date */
export const tiffinMenuId = (date) => `0199c000-0000-7000-8000-${date.replaceAll("-", "")}0001`;
/** The date a mock menu id stands for, or null for an id this mock never sent. @param {unknown} id */
const menuDate = (id) => {
  const m = /^0199c000-0000-7000-8000-(\d{4})(\d{2})(\d{2})0001$/.exec(String(id));
  return m ? `${m[1]}-${m[2]}-${m[3]}` : null;
};
/** The menu's dishes, in the caller's language. @param {"en" | "hi"} lang */
export const menuDishes = (lang) =>
  TIFFIN_OFFER.dishes.map((d, i) => ({
    recipe_id: `0199d000-0000-7000-8000-${String(i + 1).padStart(12, "0")}`,
    name: d[lang === "hi" ? 1 : 0],
  }));

/** A tiffin for the box's day comes with the box: cooked while the van drives, then the captain brings both. */
const TIFFIN_WITH_BOX = /** @type {Record<string, string>} */ ({
  dispatched: "locked",
  at_society: "dispatched",
  delivered: "delivered",
});

/** Ratings by order id (POST /v1/ratings, M8 §3.5); they survive reloads. */
const RATINGS_KEY = "gb-mock-ratings";
/** @returns {Record<string, { key: string, partner_id: string, score: number, comment: string | null, created_at: string }>} */
const ratings = () => JSON.parse(localStorage.getItem(RATINGS_KEY) ?? "{}");
/** A new demo day (sign-in) has nothing rated yet. */
export const clearRatings = () => localStorage.removeItem(RATINGS_KEY);

/** The seeded orders and the booked tiffins, each as OrderOut, soonest first (the list reverses them). @param {"en" | "hi"} lang */
function allOrders(lang) {
  const seeded = orders(lang);
  const box = seeded[0];
  const booked = bookedTiffins().map((b) => {
    const status =
      b.delivery_date === box.delivery_date
        ? (TIFFIN_WITH_BOX[box.status] ?? "approved")
        : "approved";
    return {
      ...full({
        id: b.id,
        kind: "tiffin",
        status,
        delivery_date: b.delivery_date,
        slot: TIFFIN_SLOT,
        total_paise: (b.qty ?? 1) * TIFFIN_OFFER.price_paise,
        cycle_id: null,
        lines: [],
        timeline:
          status === "delivered"
            ? [
                { status: "approved", at: utc(b.delivery_date, "00:00"), actor: "customer" },
                { status: "delivered", at: utc(b.delivery_date, "13:00"), actor: "hub" },
              ]
            : [{ status: "approved", at: utc("2026-10-05", "10:00"), actor: "customer" }],
        created_at: utc("2026-10-05", "10:00"),
      }),
      ...tiffinOf(b.delivery_date, b.slot, lang),
    };
  });
  return [...seeded, ...booked].sort((a, b) => a.delivery_date.localeCompare(b.delivery_date));
}

/**
 * What a tiffin order carries besides a box's fields: the menu's dishes (`tiffin`) and the kitchen
 * that holds it (`kitchen`, M4 §3.1). The mock's one kitchen holds every tiffin.
 * @param {string} date @param {string} meal @param {"en" | "hi"} lang
 */
function tiffinOf(date, meal, lang) {
  return {
    tiffin: { menu_id: tiffinMenuId(date), meal, dishes: menuDishes(lang) },
    kitchen: { partner_id: TIFFIN_OFFER.kitchen_id },
  };
}

/** The answer of POST /v1/orders: OrderOut with the menu's `tiffin`. @param {ReturnType<typeof bookedTiffins>[number]} b @param {"en" | "hi"} lang */
function tiffinOrderOut(b, lang) {
  return allOrders(lang).find((o) => o.id === b.id);
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
    // OrderSummaryOut: no lines, no timeline. Newest delivery first, as the API sends them.
    const q = new URL(request.url).searchParams;
    const limit = Math.min(100, Math.max(1, Number(q.get("limit") ?? 20)));
    const items = allOrders(langOf(request))
      .filter(
        (o) =>
          (!q.get("status") || o.status === q.get("status")) &&
          (!q.get("from") || o.delivery_date >= String(q.get("from"))) &&
          (!q.get("to") || o.delivery_date <= String(q.get("to"))),
      )
      .reverse()
      .slice(0, limit)
      .map((o) => {
        const {
          lines: _l,
          timeline: _t,
          tiffin: _tf,
          kitchen: _k,
          ...summary
        } = /** @type {typeof o & { tiffin?: unknown, kitchen?: unknown }} */ (o);
        return summary;
      });
    return HttpResponse.json({ items, next_cursor: null });
  }),

  // A direct tiffin order (M4 §3.5): { kind: "tiffin", menu_id, portions }. The menu fixes the date,
  // the meal, the price and the cook. Answers OrderOut with `tiffin`.
  http.post("*/v1/orders", async ({ request }) => {
    const lang = langOf(request);
    const key = request.headers.get("idempotency-key");
    if (!key)
      return problem(400, "request.idempotency_key_required", "Idempotency-Key header is required");
    const list = bookedTiffins();
    const replay = list.find((b) => b.key === key);
    if (replay) return HttpResponse.json(tiffinOrderOut(replay, lang), { status: 201 });
    const body = /** @type {{ kind?: string, menu_id?: string, portions?: number }} */ (
      await request.json()
    );
    if (body.kind !== "tiffin")
      return problem(422, "orders.kind_unavailable", "Only a tiffin can be ordered directly");
    const date = menuDate(body.menu_id);
    if (!date) return problem(422, "orders.menu_unknown", "That menu is not open to book");
    const portions = body.portions ?? 2;
    if (!(Number.isInteger(portions) && portions >= 1 && portions <= 12))
      return problem(422, "request.invalid", "Book between 1 and 12 portions");
    const meal = TIFFIN_OFFER.slot;
    const twin = list.find((b) => b.delivery_date === date && b.slot === meal);
    if (twin)
      return HttpResponse.json(
        {
          type: "about:blank",
          title: "A tiffin is already booked for that meal",
          status: 409,
          code: "orders.tiffin_already_booked",
          order_id: twin.id,
        },
        { status: 409, headers: { "content-type": "application/problem+json" } },
      );
    const order = {
      id: crypto.randomUUID(),
      key,
      delivery_date: date,
      slot: meal,
      kitchen_id: TIFFIN_OFFER.kitchen_id,
      qty: portions,
    };
    localStorage.setItem(TIFFINS_KEY, JSON.stringify([...list, order]));
    return HttpResponse.json(tiffinOrderOut(order, lang), { status: 201 });
  }),

  http.get("*/v1/orders/:id", ({ request, params }) => {
    if (!caller(request)) return problem(401, "auth.invalid_token", "Sign in again");
    const order = allOrders(langOf(request)).find((o) => o.id === params.id);
    return order
      ? HttpResponse.json(order, { headers: { etag: `"${order.version}"` } })
      : problem(404, "orders.not_found", "No such order");
  }),

  // M8 §3.5: GET /v1/ratings?order_id= answers the caller's own rating, else 404 (also for a box).
  http.get("*/v1/ratings", ({ request }) => {
    if (!caller(request)) return problem(401, "auth.invalid_token", "Sign in again");
    const id = String(new URL(request.url).searchParams.get("order_id"));
    const { key: _key, ...r } = ratings()[id] ?? {};
    return r.score
      ? HttpResponse.json({ order_id: id, ...r })
      : problem(404, "partners.rating_not_found", "No rating for this order");
  }),

  // M8 §3.5: rate a delivered tiffin once, 1 to 5 and a word for the staff. Boxes are not rated.
  http.post("*/v1/ratings", async ({ request }) => {
    if (!caller(request)) return problem(401, "auth.invalid_token", "Sign in again");
    const key = request.headers.get("idempotency-key");
    if (!key)
      return problem(400, "request.idempotency_key_required", "Idempotency-Key header is required");
    const body = /** @type {{ order_id?: string, score?: number, comment?: string | null }} */ (
      await request.json()
    );
    const id = String(body.order_id);
    if (
      !(Number.isInteger(body.score) && Number(body.score) >= 1 && Number(body.score) <= 5) ||
      (body.comment ?? "").length > 300
    )
      return problem(422, "request.validation_failed", "Pick 1 to 5");
    const rated = ratings();
    const { key: replayKey, ...done } = rated[id] ?? {};
    if (replayKey === key) return HttpResponse.json({ order_id: id, ...done }, { status: 201 });
    const order = allOrders("en").find((o) => o.id === id);
    if (order?.kind !== "tiffin")
      return problem(404, "partners.rating_not_found", "No such tiffin order");
    if (order.status !== "delivered")
      return problem(409, "partners.rating_window_closed", "Rate it once it reaches you");
    if (rated[id]) return problem(409, "partners.rating_exists", "This tiffin is rated already");
    // Read again after the await: stored ratings may have changed meanwhile.
    const all = ratings();
    all[id] = {
      key,
      partner_id: TIFFIN_OFFER.kitchen_id,
      score: Number(body.score),
      comment: body.comment?.trim() || null,
      created_at: new Date().toISOString(),
    };
    localStorage.setItem(RATINGS_KEY, JSON.stringify(all));
    const { key: _k, ...out } = all[id];
    return HttpResponse.json({ order_id: id, ...out }, { status: 201 });
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

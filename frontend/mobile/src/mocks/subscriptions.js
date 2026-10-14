// M3 weekly-cycle mocks (docs/modules/M3-subscriptions.md §3) for the first-week preview, Week
// and Box screens. The spec lists endpoints, not fields, so this shape is our proposal from the
// M3 tables. Names are localised from Accept-Language, as the API should do; reasons and
// highlights are codes with params, which the app turns into sentences.
import { HttpResponse, http } from "msw";
import { chargeBox, walletBalance } from "./billing.js";
import { PRODUCTS } from "./catalog.js";
import { bookedTiffins, TIFFIN_OFFER } from "./orders.js";

const STATE_KEY = "gb-mock-cycle"; // status and version survive reloads, like a server's
const DISHES = {
  en: [
    ["Varan bhaat", "Bhindi bhaji"],
    ["Methi thepla", "Dal khichdi"],
    ["Rajma chawal", "Usal pav"],
    ["Lauki chana dal", "Phulka"],
    ["Paneer bhurji", "Jeera rice"],
    ["Pav bhaji", "Kadhi chawal"],
    ["Puran poli", "Varan bhaat"],
  ],
  hi: [
    ["वरण भात", "भिंडी भाजी"],
    ["मेथी थेपला", "दाल खिचड़ी"],
    ["राजमा चावल", "उसल पाव"],
    ["लौकी चना दाल", "फुलका"],
    ["पनीर भुर्जी", "जीरा राइस"],
    ["पाव भाजी", "कढ़ी चावल"],
    ["पूरन पोली", "वरण भात"],
  ],
};
const TIFFIN_DAY = 2; // Wednesday: tiffin by Sunita tai
/** Other meals Autopilot can swap in (M3 meal swap, our proposal): [en, hi]. */
const ALT_DISHES = [
  ["Moong dal khichdi", "मूंग दाल खिचड़ी"],
  ["Palak paneer, roti", "पालक पनीर, रोटी"],
  ["Masoor dal, rice", "मसूर दाल, चावल"],
];
/** Why a meal was planned: [day index, slot, reason code, params]. */
const MEAL_REASONS = [
  [1, "lunch", "in_season", { item: ["Methi", "मेथी"] }],
  [1, "dinner", "quick_cook", { minutes: 30 }],
  [0, "dinner", "quick_cook", { minutes: 25 }],
];

/** [id, en name, hi name, en qty, hi qty, price_paise, reason_code?, reason_params?, swapped_from_paise?] */
/** @type {[string, string, string, string, string, number, string?, Record<string, any>?, number?][]} */
const LINES = [
  ["methi", "Methi", "मेथी", "2 judi", "2 जुड़ी", 4000, "in_season", { item: ["Methi", "मेथी"] }],
  [
    "lauki",
    "Lauki",
    "लौकी",
    "1 piece",
    "1 नग",
    3000,
    "price_spike",
    { item: ["Tomato", "टमाटर"], pct: 40 },
    5600,
  ],
  ["bhindi", "Bhindi", "भिंडी", "500 g", "500 ग्रा", 4000],
  ["paneer", "Paneer", "पनीर", "200 g", "200 ग्रा", 9000, "high_protein", { member: "adults-1" }],
  ["onion", "Onion", "प्याज़", "1 kg", "1 किलो", 3800],
  ["potato", "Potato", "आलू", "1 kg", "1 किलो", 3500],
  ["toor-dal", "Toor dal", "तूर दाल", "1 kg", "1 किलो", 18000],
  ["rajma", "Rajma", "राजमा", "1 kg", "1 किलो", 12000],
  ["coriander", "Coriander", "धनिया", "1 bunch", "1 जुड़ी", 1000],
  ["chilli", "Green chilli", "हरी मिर्च", "100 g", "100 ग्रा", 1000],
  ["ginger", "Ginger", "अदरक", "100 g", "100 ग्रा", 1500],
  ["curd", "Curd", "दही", "400 g", "400 ग्रा", 4500],
  ["lemon", "Lemon", "नींबू", "4 pcs", "4 नग", 1600],
  ["cucumber", "Cucumber", "खीरा", "500 g", "500 ग्रा", 2100],
];

/**
 * How a line changes in the quantity sheet (Core-B3): one step is [amount, en unit, hi unit, paise],
 * and the box starts with `qty` steps. Lines not listed step by their whole pack.
 */
const STEPS = /** @type {Record<string, [number, string, string, number, number]>} */ ({
  methi: [1, "judi", "जुड़ी", 2000, 2],
  bhindi: [250, "g", "ग्रा", 2000, 2],
  paneer: [100, "g", "ग्रा", 4500, 2],
  onion: [500, "g", "ग्रा", 1900, 2],
});
/** Which planned meals use a line: [day index, slot]. */
const USED_IN = /** @type {Record<string, [number, "lunch" | "dinner"][]>} */ ({
  methi: [[1, "lunch"]],
  bhindi: [[0, "dinner"]],
  paneer: [[4, "lunch"]],
  rajma: [[2, "lunch"]],
});
/** "2 judi", "750 g", "1.5 kg": grams turn into kilos from 1000 up. */
const qtyLabel = (
  /** @type {number} */ amount,
  /** @type {string} */ unit,
  /** @type {"en" | "hi"} */ lang,
) =>
  (unit === "g" || unit === "ग्रा") && amount >= 1000
    ? `${amount / 1000} ${lang === "hi" ? "किलो" : "kg"}`
    : `${amount} ${unit}`;

/** The week's price spike: tomato +40%, so Autopilot used lauki (Core-B4). */
const SPIKE = {
  price: 5600,
  original: (/** @type {"en" | "hi"} */ lang) => ({
    product_id: "tomato",
    replaces: "lauki",
    name: lang === "hi" ? "टमाटर" : "Tomato",
    qty_label: lang === "hi" ? "500 ग्रा" : "500 g",
    qty: 1,
    step: { label: lang === "hi" ? "500 ग्रा" : "500 g", price_paise: 5600 },
    price_paise: 5600,
    reason_code: "kept_by_you",
    reason_params: null,
    swapped_from_paise: null,
  }),
};

/** M2 substitutes: [id, en, hi, en qty, hi qty, price_paise, reason_code], best first. */
/** @type {Record<string, [string, string, string, string, string, number, string][]>} */
const SUBSTITUTES = {
  bhindi: [
    ["tondli", "Tondli", "तोंडली", "500 g", "500 ग्रा", 3200, "same_style"],
    ["gawar", "Gawar", "ग्वार", "500 g", "500 ग्रा", 3600, "seasonal"],
    ["bhindi-half", "Bhindi", "भिंडी", "250 g", "250 ग्रा", 2000, "half_qty"],
  ],
  paneer: [
    ["tofu", "Tofu", "टोफ़ू", "200 g", "200 ग्रा", 7000, "same_protein"],
    ["chana", "Kala chana", "काला चना", "500 g", "500 ग्रा", 5500, "same_protein"],
    ["paneer-half", "Paneer", "पनीर", "100 g", "100 ग्रा", 4500, "half_qty"],
  ],
};
const DEFAULT_SUBS = [
  ["seasonal-mix", "Seasonal sabzi", "मौसमी सब्ज़ी", "500 g", "500 ग्रा", 3500, "seasonal"],
];

const loadState = () => {
  try {
    return (
      JSON.parse(localStorage.getItem(STATE_KEY) ?? "null") ?? {
        status: "ready",
        version: 1,
        swaps: {},
      }
    );
  } catch {
    return { status: "ready", version: 1, swaps: {} };
  }
};
const saveState = (
  /** @type {{ status: string, version: number, swaps?: Record<string, any>, approved_at?: string, meals?: Record<string, string[]>, qty?: Record<string, number>, removed?: string[], added?: Record<string, number>, parked?: Record<string, number>, kept?: boolean, alertSeen?: boolean }} */ s,
) => localStorage.setItem(STATE_KEY, JSON.stringify(s));

/** Localised value: [en, hi] pairs become one string. */
const pick = (/** @type {any} */ v, /** @type {"en" | "hi"} */ lang) =>
  Array.isArray(v) ? v[lang === "hi" ? 1 : 0] : v;

function cycle(/** @type {"en" | "hi"} */ lang) {
  const {
    status,
    version,
    swaps = {},
    approved_at = null,
    meals = {},
    qty = {},
    removed = [],
    added = {},
    kept = false,
    alertSeen = false,
  } = loadState();
  const base = LINES.filter(([id]) => !removed.includes(id)).map(
    ([id, en, hi, qen, qhi, price, code, params, from]) => {
      // A pack bought as a whole steps by itself: "1 kg" for ₹35 is one step of 1 kg.
      const [n, uen] = qen.split(/ (.*)/);
      const [, uhi] = qhi.split(/ (.*)/);
      const [amount, stepEn, stepHi, stepPaise, startQty] = STEPS[id] ?? [
        Number(n),
        uen,
        uhi,
        price,
        1,
      ];
      const count = qty[id] ?? startQty;
      return {
        product_id: id,
        name: lang === "hi" ? hi : en,
        qty_label: qtyLabel(count * amount, lang === "hi" ? stepHi : stepEn, lang),
        qty: count,
        step: {
          label: qtyLabel(amount, lang === "hi" ? stepHi : stepEn, lang),
          price_paise: stepPaise,
        },
        used_in: (USED_IN[id] ?? []).map(([d, slot]) => {
          const date = `2026-10-${String(5 + d).padStart(2, "0")}`;
          const swapped = meals[`${date}:${slot}`];
          const dish = swapped
            ? swapped[lang === "hi" ? 1 : 0]
            : DISHES[lang][d][slot === "lunch" ? 0 : 1];
          return { date, slot, dish };
        }),
        price_paise: count * stepPaise,
        reason_code: code ?? null,
        reason_params: params
          ? Object.fromEntries(Object.entries(params).map(([k, v]) => [k, pick(v, lang)]))
          : null,
        swapped_from_paise: from ?? null,
      };
    },
  );
  // A swapped line becomes its substitute, remembering the price it replaced.
  const lines = base.map((l) => {
    // Autopilot swapped tomato for lauki on price (Core-B4); "Keep tomato" puts it back.
    if (l.product_id === "lauki" && kept) return { ...SPIKE.original(lang), used_in: [] };
    const sub = swaps[l.product_id];
    if (!sub) return l;
    return {
      product_id: sub.id,
      replaces: l.product_id,
      name: lang === "hi" ? sub.hi : sub.en,
      qty_label: lang === "hi" ? sub.qhi : sub.qen,
      price_paise: sub.price,
      reason_code: "swapped_by_you",
      reason_params: null,
      swapped_from_paise: l.price_paise,
    };
  });
  // Add-ons are lines on the weekly box, paid at the same cut-off (design review, 2 Oct).
  for (const [id, count] of Object.entries(added)) {
    const p = PRODUCTS.find(([pid]) => pid === id);
    if (!p) continue;
    const [, en, hin, , amount, uen, uhi, price] = p;
    const unit = lang === "hi" ? uhi : uen;
    lines.push({
      product_id: id,
      name: lang === "hi" ? hin : en,
      qty_label: qtyLabel(count * amount, unit, lang),
      qty: count,
      step: { label: qtyLabel(amount, unit, lang), price_paise: price },
      used_in: [],
      price_paise: count * price,
      reason_code: "added_by_you",
      reason_params: null,
      swapped_from_paise: null,
    });
  }
  const spikeOpen = !kept && !alertSeen && lines.some((l) => l.product_id === "lauki");
  return {
    id: "cyc-2026-10-05",
    week_start: "2026-10-05",
    delivery_date: "2026-10-06",
    delivery_window: { from: "08:00", to: "10:00" },
    cutoff_at: "2026-10-05T21:00:00+05:30",
    // ready → approved | skipped; at the cut-off approved → locked, or payment_failed when the
    // wallet is short (2 h grace until 23:00, then cancelled). Edits stay open until the cut-off.
    status,
    version,
    approved_at,
    meal_plan: {
      days: DISHES[lang].map(([lunch, dinner], i) => ({
        date: `2026-10-${String(5 + i).padStart(2, "0")}`,
        meals: /** @type {const} */ (["lunch", "dinner"]).map((slot) => {
          const date = `2026-10-${String(5 + i).padStart(2, "0")}`;
          const swapped = meals[`${date}:${slot}`];
          const reason = MEAL_REASONS.find(([d, sl]) => d === i && sl === slot);
          const booked = bookedTiffins().some((b) => b.delivery_date === date && b.slot === slot);
          const planned = slot === "lunch" ? lunch : dinner;
          return {
            slot,
            // A booked tiffin replaces the home-cooked meal.
            name: booked
              ? pick(TIFFIN_OFFER.dish, lang)
              : swapped
                ? swapped[lang === "hi" ? 1 : 0]
                : planned,
            tiffin: i === TIFFIN_DAY || booked,
            reason_code: swapped ? "swapped_by_you" : (reason?.[2] ?? null),
            reason_params:
              !swapped && reason
                ? Object.fromEntries(Object.entries(reason[3]).map(([k, v]) => [k, pick(v, lang)]))
                : null,
          };
        }),
        // A home kitchen's dinner tiffin, until the cut-off (design Core-W2).
        tiffin_offer:
          i === TIFFIN_DAY ||
          bookedTiffins().some(
            (b) => b.delivery_date === `2026-10-${String(5 + i).padStart(2, "0")}`,
          )
            ? null
            : {
                kitchen_id: TIFFIN_OFFER.kitchen_id,
                slot: "dinner",
                cook: { name: pick(TIFFIN_OFFER.cook, lang) },
                dish: { name: pick(TIFFIN_OFFER.dish, lang) },
                price_paise: TIFFIN_OFFER.price_paise,
                order_by: "2026-10-05T21:00:00+05:30",
              },
      })),
    },
    lines,
    total_paise: lines.reduce((s, l) => s + l.price_paise, 0),
    highlights: [
      { code: "in_season", params: { item: lang === "hi" ? "मेथी" : "Methi" } },
      {
        code: "tiffin_day",
        params: { weekday: TIFFIN_DAY, cook: lang === "hi" ? "सुनीता ताई" : "Sunita tai" },
      },
      { code: "health_tag", params: { tag: "low_sugar", member: "adults-2" } },
    ],
    // A skipped week spends nothing from the Budget Lock.
    budget_lock: {
      budget_paise: 300000,
      spent_paise: status === "skipped" ? 0 : lines.reduce((t, l) => t + l.price_paise, 0),
    },
    captain: { name: lang === "hi" ? "मीना ताई" : "Meena tai", flat: "B-402" },
    hub: { name: lang === "hi" ? "खारघर" : "Kharghar" },
    // Price moves Autopilot reacted to; the banner shows until kept or OK'd (Core-B4).
    price_alerts: spikeOpen
      ? [
          {
            id: "spike-tomato",
            product_id: "lauki",
            item: pick(["Tomato", "टमाटर"], lang),
            substitute: pick(["lauki", "लौकी"], lang),
            pct: 40,
            market: pick(["Vashi APMC", "वाशी APMC"], lang),
            meals: 2,
            keep_delta_paise: SPIKE.price - 3000,
          },
        ]
      : [],
    payment_due:
      status === "payment_failed"
        ? {
            wallet_paise: walletBalance(),
            shortfall_paise: Math.max(
              0,
              lines.reduce((t, l) => t + l.price_paise, 0) - walletBalance(),
            ),
            due_at: "2026-10-05T23:00:00+05:30",
          }
        : null,
  };
}

/** Demo scenario for 98000 00004: the cut-off found the wallet short (design Core-D4). */
export const seedPaymentFailed = () =>
  saveState({
    status: "payment_failed",
    version: 1,
    swaps: {},
    approved_at: "2026-10-01T19:12:00+05:30",
  });

const langOf = (/** @type {Request} */ r) =>
  (r.headers.get("accept-language") ?? "").startsWith("hi") ? "hi" : "en";
const problem = (
  /** @type {number} */ status,
  /** @type {string} */ code,
  /** @type {string} */ title,
) =>
  HttpResponse.json(
    { type: "about:blank", title, status, code },
    { status, headers: { "content-type": "application/problem+json" } },
  );

/** Which status each action may start from (M3 cycle lifecycle; edits close at the cut-off). */
const FROM = /** @type {Record<string, string[]>} */ ({
  approve: ["ready", "approved"],
  skip: ["ready", "approved", "payment_failed"],
  unskip: ["skipped"],
  pay: ["payment_failed"],
});

/** POST approve/skip/unskip/pay: needs an Idempotency-Key, and If-Match with the current version. */
const transition = (/** @type {keyof typeof FROM} */ action) =>
  http.post(`*/v1/cycles/:id/${action}`, ({ request }) => {
    if (!request.headers.get("idempotency-key"))
      return problem(
        400,
        "platform.idempotency_key_required",
        "Idempotency-Key header is required",
      );
    const state = loadState();
    const ifMatch = request.headers.get("if-match");
    if (ifMatch && Number(ifMatch) !== state.version)
      return problem(412, "subscriptions.version_conflict", "The box changed; reload it");
    if (!FROM[action].includes(state.status))
      return problem(409, "orders.invalid_transition", `Can't ${action} a ${state.status} week`);
    /** @type {Partial<ReturnType<typeof loadState>>} */
    let next = {};
    if (action === "approve")
      next = { status: "approved", approved_at: state.approved_at ?? new Date().toISOString() };
    if (action === "skip") next = { status: "skipped" };
    if (action === "unskip") next = { status: "ready", approved_at: undefined };
    if (action === "pay") {
      // The retry after a top-up: charge the wallet now, inside the 2 h grace.
      const total = cycle(langOf(request)).total_paise;
      if (!chargeBox(total, "2026-10-06"))
        return problem(402, "billing.insufficient_balance", "The wallet is still short");
      next = { status: "locked" };
    }
    saveState({ ...state, ...next, version: state.version + 1 });
    return HttpResponse.json(cycle(langOf(request)));
  });

export const subscriptionHandlers = [
  http.get("*/v1/products/:id/substitutes", ({ request, params }) => {
    const lang = langOf(request);
    const line = LINES.find(([id]) => id === params.id);
    const price = line ? line[5] : 0;
    const items = (SUBSTITUTES[String(params.id)] ?? DEFAULT_SUBS)
      .slice(0, 3)
      .map(([id, en, hi, qen, qhi, p, code]) => ({
        product_id: id,
        name: lang === "hi" ? hi : en,
        qty_label: lang === "hi" ? qhi : qen,
        price_paise: p,
        price_delta_paise: p - price,
        reason_code: code,
        reason_params: null,
      }));
    return HttpResponse.json({ items });
  }),

  // PATCH lines: { op: "substitute", product_id, substitute_id } with If-Match: <version>.
  http.patch("*/v1/cycles/:id/lines", async ({ request }) => {
    const state = loadState();
    if (Number(request.headers.get("if-match")) !== state.version) {
      return problem(412, "subscriptions.version_conflict", "The box changed; reload it");
    }
    if (state.status !== "ready" && state.status !== "approved")
      return problem(409, "orders.cutoff_passed", "Edits are closed for this week");
    const body = /** @type {any} */ (await request.json());
    // { op: "qty", product_id, qty } | { op: "remove" | "restore", product_id } (Core-B3, B4).
    // { op: "undo_swap", product_id } puts back what a swap replaced: Autopilot's price swap
    // (lauki → tomato) or the person's own. { op: "dismiss_alert" } is the banner's OK.
    if (body.op === "undo_swap" || body.op === "dismiss_alert") {
      if (body.op === "dismiss_alert")
        saveState({ ...state, version: state.version + 1, alertSeen: true });
      else if (body.product_id === "lauki")
        saveState({ ...state, version: state.version + 1, kept: !state.kept });
      else {
        const swaps = { ...(state.swaps ?? {}) };
        const from = Object.keys(swaps).find((k) => swaps[k].id === body.product_id);
        if (!from) return problem(404, "subscriptions.line_not_found", "Nothing to undo");
        delete swaps[from];
        saveState({ ...state, version: state.version + 1, swaps });
      }
      return HttpResponse.json(cycle(langOf(request)));
    }
    // { op: "add", product_id }: an add-on from the catalog, one step to start (or one more).
    if (body.op === "add" || (body.op && PRODUCTS.some(([pid]) => pid === body.product_id))) {
      const added = { ...(state.added ?? {}) };
      if (!PRODUCTS.some(([pid]) => pid === body.product_id))
        return problem(404, "catalog.product_not_found", "No such product");
      if (body.op === "add") added[body.product_id] = (added[body.product_id] ?? 0) + 1;
      else if (body.op === "qty") {
        if (!(Number.isInteger(body.qty) && body.qty >= 1 && body.qty <= 20))
          return problem(422, "subscriptions.qty_invalid", "Pick between 1 and 20");
        added[body.product_id] = body.qty;
      }
      // Remember what was removed, so Undo brings back the same amount.
      const parked = { ...(state.parked ?? {}) };
      if (body.op === "remove") {
        parked[body.product_id] = added[body.product_id];
        delete added[body.product_id];
      } else if (body.op === "restore") added[body.product_id] = parked[body.product_id] ?? 1;
      saveState({ ...state, version: state.version + 1, added, parked });
      return HttpResponse.json(cycle(langOf(request)));
    }
    if (body.op === "qty" || body.op === "remove" || body.op === "restore") {
      if (!LINES.some(([id]) => id === body.product_id))
        return problem(404, "subscriptions.line_not_found", "That item is not in the box");
      if (body.op === "qty" && !(Number.isInteger(body.qty) && body.qty >= 1 && body.qty <= 20))
        return problem(422, "subscriptions.qty_invalid", "Pick between 1 and 20");
      const removed = (state.removed ?? []).filter(
        (/** @type {string} */ id) => id !== body.product_id,
      );
      saveState({
        ...state,
        version: state.version + 1,
        qty: body.op === "qty" ? { ...state.qty, [body.product_id]: body.qty } : state.qty,
        removed: body.op === "remove" ? [...removed, body.product_id] : removed,
      });
      return HttpResponse.json(cycle(langOf(request)));
    }
    const sub = (SUBSTITUTES[body.product_id] ?? DEFAULT_SUBS).find(
      ([id]) => id === body.substitute_id,
    );
    if (!sub)
      return problem(422, "subscriptions.substitute_unknown", "That substitute is not available");
    const [id, en, hi, qen, qhi, price] = sub;
    saveState({
      ...state,
      version: state.version + 1,
      swaps: { ...state.swaps, [body.product_id]: { id, en, hi, qen, qhi, price } },
    });
    return HttpResponse.json(cycle(langOf(request)));
  }),

  // PATCH meals: { date, slot } with If-Match: swaps that meal for the next alternative (M3).
  http.patch("*/v1/cycles/:id/meals", async ({ request }) => {
    const state = loadState();
    if (Number(request.headers.get("if-match")) !== state.version)
      return problem(412, "subscriptions.version_conflict", "The plan changed; reload it");
    if (state.status !== "ready" && state.status !== "approved")
      return problem(409, "orders.cutoff_passed", "Edits are closed for this week");
    const { date, slot } = /** @type {{ date: string, slot: string }} */ (await request.json());
    const meals = { ...(state.meals ?? {}) };
    const key = `${date}:${slot}`;
    const at = ALT_DISHES.findIndex(([en]) => en === meals[key]?.[0]);
    meals[key] = ALT_DISHES[(at + 1) % ALT_DISHES.length];
    saveState({ ...state, meals, version: state.version + 1 });
    return HttpResponse.json(cycle(langOf(request)));
  }),

  http.get("*/v1/cycles/current", ({ request }) => HttpResponse.json(cycle(langOf(request)))),
  transition("approve"),
  transition("skip"),
  transition("unskip"),
  transition("pay"),
];

/** Dev helper: put the mock cycle back to "ready". */
export const resetCycle = () => saveState({ status: "ready", version: 1, swaps: {} });

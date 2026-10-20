// M3 weekly-cycle mocks (docs/modules/M3-subscriptions.md §3.1, contract/openapi.json): the real
// CycleOut for Week, Box and the first-week preview. Names are localised from Accept-Language, as
// the API does; reasons and highlights are codes with params, which the app turns into sentences.
// Edits, approve and skip are PR #102 and not shipped; they follow the spec's documented shapes.
// Data the API has nowhere (a home kitchen's tiffin on a day) is its own proposed endpoint.
import { HttpResponse, http } from "msw";
import { chargeBox } from "./billing.js";
import { PRODUCTS } from "./catalog.js";
import { bookedTiffins, TIFFIN_OFFER } from "./orders.js";
import { HUB_ID, SOCIETY_ID } from "./packer.js";

const STATE_KEY = "gb-mock-cycle"; // status and version survive reloads, like a server's
const NO_CYCLE_KEY = "gb-mock-no-cycle"; // "1": a new household, before the first generation
/** [breakfast, lunch, dinner] per day, Monday first, [en, hi]. */
const DISHES = [
  [
    ["Kanda poha", "कांदा पोहा"],
    ["Varan bhaat", "वरण भात"],
    ["Bhindi bhaji", "भिंडी भाजी"],
  ],
  [
    ["Sabudana khichdi", "साबूदाना खिचड़ी"],
    ["Methi thepla", "मेथी थेपला"],
    ["Dal khichdi", "दाल खिचड़ी"],
  ],
  [
    ["Upma", "उपमा"],
    ["Rajma chawal", "राजमा चावल"],
    ["Usal pav", "उसल पाव"],
  ],
  [
    ["Thalipeeth", "थालीपीठ"],
    ["Lauki chana dal", "लौकी चना दाल"],
    ["Phulka", "फुलका"],
  ],
  [
    ["Moong chilla", "मूंग चीला"],
    ["Paneer bhurji", "पनीर भुर्जी"],
    ["Jeera rice", "जीरा राइस"],
  ],
  [
    ["Idli", "इडली"],
    ["Pav bhaji", "पाव भाजी"],
    ["Kadhi chawal", "कढ़ी चावल"],
  ],
  [
    ["Fruit bowl", "फल कटोरी"],
    ["Puran poli", "पूरन पोली"],
    ["Varan bhaat", "वरण भात"],
  ],
];
const SLOTS = /** @type {const} */ (["breakfast", "lunch", "dinner"]);
const TIFFIN_DAY = 2; // Wednesday: tiffin by Lata tai
/** Other meals Autopilot can swap in (M3 meal swap, our proposal): [en, hi]. */
const ALT_DISHES = [
  ["Moong dal khichdi", "मूंग दाल खिचड़ी"],
  ["Palak paneer, roti", "पालक पनीर, रोटी"],
  ["Masoor dal, rice", "मसूर दाल, चावल"],
];

/** A stable UUID for a slug, so ids look like the API's and survive reloads. @param {string} slug */
export const uid = (slug) =>
  `0199a000-0000-7000-8000-${[...slug]
    .reduce((h, c) => (Math.imul(h, 31) + c.charCodeAt(0)) >>> 0, 7)
    .toString(16)
    .padStart(12, "0")}`;

const UNITS = /** @type {Record<string, [string, string]>} */ ({
  g: ["g", "ग्रा"],
  kg: ["kg", "किलो"],
  piece: ["pc", "नग"],
  bunch: ["bunch", "जुड़ी"],
});
/** A step as the API labels it: "250 g", "1 bunch". */
const stepOf = (
  /** @type {number} */ qty,
  /** @type {"g" | "kg" | "piece" | "bunch"} */ unit,
  /** @type {"en" | "hi"} */ lang,
) => ({ qty, unit, label: `${qty} ${UNITS[unit][lang === "hi" ? 1 : 0]}` });

/**
 * The box: [slug, en, hi, step qty, step unit, price per step, steps to start, reason code, reason
 * params]. Prices are per step; the box starts with that many steps.
 * @type {[string, string, string, number, "g" | "kg" | "piece" | "bunch", number, number, string, Record<string, any> | null][]}
 */
const LINES = [
  ["methi", "Methi", "मेथी", 1, "bunch", 2000, 2, "in_season", { item: ["Methi", "मेथी"] }],
  ["lauki", "Lauki", "लौकी", 1, "piece", 3000, 1, "price_swap", { from: "tomato", pct: 40 }],
  ["bhindi", "Bhindi", "भिंडी", 250, "g", 2000, 2, "meal_plan", { recipes: ["bhindi-bhaji"] }],
  [
    "paneer",
    "Paneer",
    "पनीर",
    100,
    "g",
    4500,
    2,
    "health_tag",
    { tag: "high_protein", member: "adults-1" },
  ],
  ["onion", "Onion", "प्याज़", 500, "g", 1900, 2, "daily_use", null],
  ["potato", "Potato", "आलू", 1, "kg", 3500, 1, "daily_use", null],
  ["toor-dal", "Toor dal", "तूर दाल", 1, "kg", 18000, 1, "daily_use", null],
  ["rajma", "Rajma", "राजमा", 1, "kg", 12000, 1, "meal_plan", { recipes: ["rajma-chawal"] }],
  [
    "coriander",
    "Coriander",
    "धनिया",
    1,
    "bunch",
    1000,
    1,
    "meal_plan",
    { recipes: ["varan-bhaat", "dal-khichdi"] },
  ],
  ["chilli", "Green chilli", "हरी मिर्च", 100, "g", 1000, 1, "daily_use", null],
  ["ginger", "Ginger", "अदरक", 100, "g", 1500, 1, "daily_use", null],
  ["curd", "Curd", "दही", 400, "g", 4500, 1, "daily_use", null],
  ["lemon", "Lemon", "नींबू", 4, "piece", 1600, 1, "meal_plan", { recipes: ["moong-chilla"] }],
  [
    "cucumber",
    "Cucumber",
    "खीरा",
    500,
    "g",
    2100,
    1,
    "meal_plan",
    { recipes: ["kakdi-koshimbir"] },
  ],
];
/** The week's price swap: tomato is +40%, so Autopilot used lauki (Core-B4). Tomato: ₹56 a step. */
const TOMATO = {
  slug: "tomato",
  en: "Tomato",
  hi: "टमाटर",
  qty: 500,
  unit: /** @type {const} */ ("g"),
  price: 5600,
};

/**
 * M2 substitutes for a line, best first: [slug, en, hi, step qty, step unit, price per step, reason
 * code, reason params].
 * @type {Record<string, [string, string, string, number, "g" | "kg" | "piece" | "bunch", number, string, Record<string, any>][]>}
 */
const SUBSTITUTES = {
  bhindi: [
    ["tondli", "Tondli", "तोंडली", 250, "g", 1600, "similar_use", {}],
    ["gawar", "Gawar", "ग्वार", 250, "g", 1800, "in_season", {}],
    ["turai", "Turai", "तुरई", 250, "g", 1500, "cheaper", { saves_paise: 500 }],
  ],
  paneer: [
    ["tofu", "Tofu", "टोफ़ू", 100, "g", 3500, "similar_use", {}],
    ["chana", "Kala chana", "काला चना", 250, "g", 2750, "same_family", {}],
    ["soya", "Soya chunks", "सोया चंक्स", 100, "g", 2000, "cheaper", { saves_paise: 2500 }],
  ],
};
const DEFAULT_SUBS = /** @type {typeof SUBSTITUTES[string]} */ ([
  ["seasonal-mix", "Seasonal sabzi", "मौसमी सब्ज़ी", 500, "g", 3500, "in_season", {}],
]);

/** @typedef {{ status: "awaiting_approval" | "approved" | "skipped" | "cancelled" | "locked" | "payment_failed", version: number, swaps?: Record<string, any>, approved_at?: string, meals?: Record<string, string[]>, qty?: Record<string, number>, removed?: string[], added?: Record<string, number> }} CycleState */
const FRESH = /** @type {CycleState} */ ({ status: "awaiting_approval", version: 1, swaps: {} });
const loadState = () => {
  try {
    const s = JSON.parse(localStorage.getItem(STATE_KEY) ?? "null");
    // Older mock sessions stored the week as "ready".
    return s ? { ...s, status: s.status === "ready" ? "awaiting_approval" : s.status } : FRESH;
  } catch {
    return FRESH;
  }
};
const saveState = (/** @type {CycleState} */ s) =>
  localStorage.setItem(STATE_KEY, JSON.stringify(s));

/** Localised value: [en, hi] pairs become one string. */
const pick = (/** @type {any} */ v, /** @type {"en" | "hi"} */ lang) =>
  Array.isArray(v) ? v[lang === "hi" ? 1 : 0] : v;

/** Slug of a box line or substitute by its UUID; anything else (a catalog id) stays as it is. */
const SLUGS = new Map(
  [
    ...LINES.map(([slug]) => slug),
    TOMATO.slug,
    ...Object.values(SUBSTITUTES)
      .flat()
      .map(([slug]) => slug),
    ...DEFAULT_SUBS.map(([slug]) => slug),
  ].map((slug) => [uid(slug), slug]),
);
const slugOf = (/** @type {string} */ id) => SLUGS.get(id) ?? id;
const inBox = (/** @type {string} */ slug) => LINES.some(([l]) => l === slug);

/** One CycleLine, priced per step. */
function line(
  /** @type {"en" | "hi"} */ lang,
  /** @type {{ slug: string, name: string, step: ReturnType<typeof stepOf>, count: number, price: number, code: string, params?: Record<string, any> | null, source: "ai" | "rule" | "customer", replaces?: any }} */ l,
) {
  return {
    product_id: uid(l.slug),
    slug: l.slug,
    name: l.name,
    qty: l.count,
    step: l.step,
    unit_price_paise: l.price,
    total_paise: l.count * l.price,
    reason_code: l.code,
    reason_params: l.params
      ? Object.fromEntries(
          Object.entries(l.params).map(([k, v]) => [k, k === "item" ? pick(v, lang) : v]),
        )
      : null,
    source: l.source,
    replaces: l.replaces ?? null,
  };
}

/** The cycle as it would be after an edit, to price it. @param {CycleState} st */
const cycleWith = (st) => cycle("en", false, st);

const MONTH = "2026-10";
const NEXT_MONTH = "2026-11";
const BUDGET_KEY = "gb-mock-budget-lock"; // month → limit in paise, null when switched off
const WHEN_OVER_KEY = "gb-mock-budget-when-over";
const EARLIER_SPEND = 118000; // what October's earlier boxes spent (the designs' ₹1,180)
const budgets = () =>
  /** @type {Record<string, number | null>} */ (
    JSON.parse(localStorage.getItem(BUDGET_KEY) ?? `{"${MONTH}":300000}`)
  );
/** The BudgetLock for a month, or null while it is off. */
const budgetLock = (/** @type {string} */ month, /** @type {number} */ spent) => {
  const budget = budgets()[month] ?? null;
  return budget === null
    ? null
    : { month, budget_paise: budget, spent_paise: spent, remaining_paise: budget - spent };
};

/**
 * What an add op can put in the box: the item Autopilot swapped out (back at its price) or a
 * catalog product. @param {string} id the product_id as sent: a UUID of ours, or a catalog id
 * @returns {{ id: string, slug: string, en: string, hi: string, qty: number, unit: "g" | "kg" | "piece" | "bunch", price: number } | null}
 */
function addable(id) {
  if (slugOf(id) === TOMATO.slug) return { id: uid(TOMATO.slug), ...TOMATO };
  const p = PRODUCTS.find(([pid]) => pid === id);
  if (!p) return null;
  const [pid, en, hi, , qty, uen, , price] = p;
  return {
    id: pid,
    slug: pid,
    en,
    hi,
    qty,
    unit: uen === "pcs" ? "piece" : /** @type {any} */ (uen),
    price,
  };
}

/** The line a swap or removal replaced, as `replaces`. */
const replaced = (
  /** @type {"en" | "hi"} */ lang,
  /** @type {string} */ slug,
  /** @type {string} */ en,
  /** @type {string} */ hi,
  /** @type {number} */ price,
) => ({ product_id: uid(slug), slug, name: lang === "hi" ? hi : en, unit_price_paise: price });

/**
 * The cycle for a language. `preview` is the first week for onboarding: nothing saved, so no id,
 * version 0 and the untouched box.
 */
function cycle(
  /** @type {"en" | "hi"} */ lang,
  preview = false,
  /** @type {CycleState} */ at = loadState(),
) {
  const {
    status,
    version,
    swaps = {},
    approved_at = null,
    meals = {},
    qty = {},
    removed = [],
    added = {},
  } = preview ? FRESH : at;
  const base = LINES.filter(([slug]) => !removed.includes(slug)).map(
    ([slug, en, hi, sq, unit, price, start, code, params]) => {
      const step = stepOf(sq, unit, lang);
      const count = qty[slug] ?? start;
      const own = { slug, name: lang === "hi" ? hi : en, step, count, price };
      // Autopilot swapped tomato for lauki on price (Core-B4); "Keep tomato" removes lauki and adds
      // tomato back at its price.
      if (slug === "lauki") {
        return line(lang, {
          ...own,
          code,
          params,
          source: "ai",
          replaces: replaced(lang, TOMATO.slug, TOMATO.en, TOMATO.hi, TOMATO.price),
        });
      }
      const sub = swaps[slug];
      if (sub)
        return line(lang, {
          slug: sub.slug,
          name: lang === "hi" ? sub.hi : sub.en,
          step: stepOf(sub.qty, sub.unit, lang),
          count,
          price: sub.price,
          code: "swapped_by_you",
          source: "customer",
          replaces: replaced(lang, slug, en, hi, price),
        });
      return line(lang, { ...own, code, params, source: code === "daily_use" ? "rule" : "ai" });
    },
  );
  // Add-ons are lines on the weekly box, paid at the same cut-off (design review, 2 Oct).
  const lines = [...base];
  for (const [id, count] of Object.entries(added)) {
    const p = addable(id);
    if (!p) continue;
    lines.push({
      product_id: p.id,
      slug: p.slug,
      name: lang === "hi" ? p.hi : p.en,
      qty: count,
      step: stepOf(p.qty, p.unit, lang),
      unit_price_paise: p.price,
      total_paise: count * p.price,
      reason_code: "added_by_you",
      reason_params: null,
      source: "customer",
      replaces: null,
    });
  }
  const total = lines.reduce((t, l) => t + l.total_paise, 0);
  const lock = budgetLock(
    MONTH,
    (preview ? 0 : EARLIER_SPEND) + (status === "skipped" ? 0 : total),
  );
  return {
    id: preview ? null : uid("cycle-2026-10-05"),
    household_id: uid("household-priya"),
    week_start: "2026-10-05",
    delivery_date: "2026-10-06",
    slot: {
      id: uid("slot-morning"),
      label: lang === "hi" ? "सुबह 8-10" : "Morning 8-10",
      start: "08:00:00",
      end: "10:00:00",
    },
    cutoff_at: "2026-10-05T15:30:00Z",
    // awaiting_approval → approved | skipped; at the cut-off approved → locked (mirrored from the
    // order, which is payment_failed when the wallet is short). Edits stay open until the cut-off.
    status: preview ? "preview" : status === "payment_failed" ? "approved" : status,
    version: preview ? 0 : version,
    meal_plan: {
      source: "ml",
      days: DISHES.map((day, i) => {
        const date = `2026-10-${String(5 + i).padStart(2, "0")}`;
        return {
          date,
          meals: SLOTS.map((slot, k) => {
            const swapped = meals[`${date}:${slot}`];
            const booked = bookedTiffins().some((b) => b.delivery_date === date && b.slot === slot);
            const tiffin = slot !== "breakfast" && (i === TIFFIN_DAY || booked);
            const name = booked
              ? pick(TIFFIN_OFFER.dish, lang)
              : swapped
                ? pick(swapped, lang)
                : pick(day[k], lang);
            return {
              slot,
              // A no-cook tiffin slot has no recipe; a booked tiffin replaces the cooked meal.
              recipe_id: tiffin ? null : uid(`recipe-${swapped ? swapped[0] : day[k][0]}`),
              name: tiffin && !booked ? null : name,
              tiffin,
              sides: slot === "breakfast" || tiffin ? [] : [lang === "hi" ? "चपाती" : "Chapati"],
            };
          }),
        };
      }),
    },
    lines,
    total_paise: total,
    highlights: [
      { code: "in_season", params: { item: lang === "hi" ? "मेथी" : "Methi" } },
      {
        code: "tiffin_day",
        params: { weekday: TIFFIN_DAY, cook: lang === "hi" ? "लता ताई" : "Lata tai" },
      },
      { code: "health_tag", params: { tag: "low_sugar", member: "adults-2" } },
    ],
    // A skipped week spends nothing from the Budget Lock.
    budget_lock: lock,
    captain: preview ? null : { name: lang === "hi" ? "मीना ताई" : "Meena tai", flat: "B-402" },
    order_id:
      !preview && ["approved", "locked", "payment_failed"].includes(status)
        ? uid("order-box-2026-10-06")
        : null,
    generated_at: preview ? null : "2026-10-02T12:30:00Z",
    approved_at: preview ? null : approved_at,
  };
}

const langOf = (/** @type {Request} */ r) =>
  (r.headers.get("accept-language") ?? "").startsWith("hi") ? "hi" : "en";
const problem = (
  /** @type {number} */ status,
  /** @type {string} */ code,
  /** @type {string} */ title,
  /** @type {Record<string, unknown>} */ extra = {},
) =>
  HttpResponse.json(
    { type: `urn:gharbasket:problem:${code}`, title, status, code, ...extra },
    { status, headers: { "content-type": "application/problem+json" } },
  );
const noCycle = () => problem(404, "subscriptions.no_cycle", "No week has been planned yet.");
/** The version in an If-Match, quoted or not. @param {string | null} header */
const versionOf = (header) => Number(String(header).replace(/"/g, ""));

const OP_FIELDS = /** @type {Record<string, string[]>} */ ({
  remove: ["op", "product_id"],
  add: ["op", "product_id", "qty"],
  qty: ["op", "product_id", "qty"],
  substitute: ["op", "product_id", "substitute_id"],
});

/** The 422 request.invalid for a PATCH body that is not { ops: [Op] }, or null. @param {{ ops?: any[] }} body */
function invalidOps(body) {
  /** @type {{ loc: (string | number)[], message: string, type: string }[]} */
  const errors = [];
  const err = (
    /** @type {(string | number)[]} */ loc,
    /** @type {string} */ message,
    type = "value_error",
  ) => errors.push({ loc: ["body", ...loc], message, type });
  for (const k of Object.keys(body))
    if (k !== "ops") err([k], "Extra inputs are not permitted", "extra_forbidden");
  if (!Array.isArray(body.ops) || body.ops.length < 1 || body.ops.length > 20)
    err(["ops"], "Send 1 to 20 ops");
  else
    body.ops.forEach((op, i) => {
      const fields = OP_FIELDS[op?.op];
      if (!fields) {
        err(["ops", i, "op"], "Input should be remove, add, qty or substitute", "literal_error");
        return;
      }
      for (const k of Object.keys(op))
        if (!fields.includes(k))
          err(["ops", i, k], "Extra inputs are not permitted", "extra_forbidden");
      for (const k of fields)
        if (op[k] === undefined) err(["ops", i, k], "Field required", "missing");
      if (op.qty !== undefined && !Number.isInteger(op.qty))
        err(["ops", i, "qty"], "Input should be a valid integer", "int_type");
    });
  return errors.length
    ? problem(422, "request.invalid", "Request validation failed", { errors })
    : null;
}

/**
 * One op on a draft of the state: a Response when it is refused, else null. A line is named by its
 * product_id; once swapped, that is the substitute's.
 * @param {CycleState} st @param {any} op
 */
function applyOp(st, op) {
  const slug = slugOf(op.product_id);
  const swaps = /** @type {Record<string, any>} */ (st.swaps);
  const qty = /** @type {Record<string, number>} */ (st.qty);
  const added = /** @type {Record<string, number>} */ (st.added);
  const removed = /** @type {string[]} */ (st.removed);
  /** The box line this product is, by the slug it was planned under. */
  const base = inBox(slug) ? slug : Object.keys(swaps).find((k) => swaps[k].slug === slug);
  const unknown = () =>
    problem(422, "subscriptions.product_unknown", "That item is not in the box");
  if ((op.op === "add" || op.op === "qty") && !(op.qty >= 1 && op.qty <= 20))
    return problem(422, "subscriptions.qty_invalid", "Pick between 1 and 20");
  if (op.op === "remove") {
    if (base && !removed.includes(base)) {
      removed.push(base);
      delete swaps[base];
    } else if (added[op.product_id] !== undefined || added[slug] !== undefined)
      delete added[added[op.product_id] !== undefined ? op.product_id : slug];
    else return unknown();
  } else if (op.op === "qty") {
    if (base && !removed.includes(base)) qty[base] = op.qty;
    else if (added[op.product_id] !== undefined) added[op.product_id] = op.qty;
    else return unknown();
  } else if (op.op === "add") {
    if (inBox(slug)) {
      // Putting back a planned line (Undo, or Keep tomato's twin): it returns as planned.
      st.removed = removed.filter((r) => r !== slug);
      delete swaps[slug];
      qty[slug] = op.qty;
    } else if (addable(op.product_id)) added[op.product_id] = op.qty;
    else return unknown();
  } else if (op.op === "substitute") {
    if (!base || removed.includes(base)) return unknown();
    const sub = (SUBSTITUTES[base] ?? DEFAULT_SUBS).find(([id]) => uid(id) === op.substitute_id);
    if (!sub)
      return problem(422, "subscriptions.substitute_unknown", "That substitute is not available");
    const [id, en, hi, sq, unit, price] = sub;
    swaps[base] = { slug: id, en, hi, qty: sq, unit, price };
  }
  return null;
}

/** Which status each action may start from (M3 cycle lifecycle; edits close at the cut-off). */
const FROM = /** @type {Record<string, string[]>} */ ({
  approve: ["awaiting_approval", "approved"],
  skip: ["awaiting_approval", "approved"],
  unskip: ["skipped"],
});

/**
 * POST approve|skip (M3 §3.1, PR #102): an Idempotency-Key, and for approve If-Match with the
 * current version. A week past its cut-off (locked, or a box whose payment failed) answers
 * `subscriptions.cutoff_passed`; a skipped one `subscriptions.not_editable`. `unskip` is our
 * proposal.
 */
const transition = (/** @type {keyof typeof FROM} */ action) =>
  http.post(`*/v1/cycles/:id/${action}`, ({ request }) => {
    if (!request.headers.get("idempotency-key"))
      return problem(400, "request.idempotency_key_required", "Idempotency-Key header is required");
    const state = loadState();
    const ifMatch = request.headers.get("if-match");
    if (action === "approve" && !ifMatch)
      return problem(
        428,
        "request.precondition_required",
        "Send If-Match with the version you saw",
      );
    if (ifMatch && versionOf(ifMatch) !== state.version)
      return problem(412, "subscriptions.version_conflict", "The box changed; reload it");
    if (action !== "unskip" && (state.status === "locked" || state.status === "payment_failed"))
      return problem(409, "subscriptions.cutoff_passed", "The cut-off has passed for this week");
    if (!FROM[action].includes(state.status))
      return problem(409, "subscriptions.not_editable", `Can't ${action} a ${state.status} week`);
    /** @type {Partial<CycleState>} */
    let next = {};
    if (action === "approve")
      next = { status: "approved", approved_at: state.approved_at ?? new Date().toISOString() };
    if (action === "skip") next = { status: "skipped" };
    if (action === "unskip") next = { status: "awaiting_approval", approved_at: undefined };
    saveState({ ...state, ...next, version: state.version + 1 });
    return HttpResponse.json(cycle(langOf(request)));
  });

/** The failed box order of the payment-failed week (OrderSummaryOut, M4), or null. */
function failedOrder() {
  const state = loadState();
  if (state.status !== "payment_failed") return null;
  const c = cycle("en");
  return {
    id: uid("order-box-2026-10-06"),
    kind: "box",
    status: "payment_failed",
    cycle_id: c.id,
    household_id: c.household_id,
    hub_id: HUB_ID,
    society_id: SOCIETY_ID,
    delivery_date: c.delivery_date,
    slot: c.slot,
    cutoff_at: c.cutoff_at,
    subtotal_paise: c.total_paise,
    fees_paise: 0,
    discount_paise: 0,
    total_paise: c.total_paise,
    version: 1,
    created_at: state.approved_at ?? "2026-10-01T13:42:00Z",
    locked_at: null,
  };
}

/**
 * A top-up inside the grace period re-runs the debit for a failed order (PR #103): when the wallet
 * covers it now, the order is paid and locked. Called whenever the orders are read.
 */
function retryDebit() {
  const order = failedOrder();
  if (order && chargeBox(order.total_paise, order.delivery_date)) {
    saveState({ ...loadState(), status: "locked" });
  }
}

/** Demo scenario for 98000 00004: the cut-off found the wallet short (design Core-D4). */
export const seedPaymentFailed = () =>
  saveState({
    status: "payment_failed",
    version: 1,
    swaps: {},
    approved_at: "2026-10-01T19:12:00+05:30",
  });

/** Dev helper: put the mock cycle back to "awaiting_approval". */
export const resetCycle = () => saveState(FRESH);
/** Dev helper: a household before its first generation (current answers 404 `subscriptions.no_cycle`). */
export const setNoCycle = (/** @type {boolean} */ on) =>
  on ? localStorage.setItem(NO_CYCLE_KEY, "1") : localStorage.removeItem(NO_CYCLE_KEY);

const BUDGET_MIN = 50000;
/** What this month's boxes spent: the earlier ones plus this week's, unless it is skipped. */
const spentSoFar = () =>
  EARLIER_SPEND + (loadState().status === "skipped" ? 0 : cycle("en").total_paise);

export const subscriptionHandlers = [
  // Three ranked substitutes, with the price difference per step and why (M2).
  http.get("*/v1/products/:id/substitutes", ({ request, params }) => {
    const lang = langOf(request);
    const slug = slugOf(String(params.id));
    const own = LINES.find(([l]) => l === slug);
    const items = (SUBSTITUTES[slug] ?? DEFAULT_SUBS)
      .slice(0, 3)
      .map(([id, en, hi, sq, unit, p, code, rp], i, all) => ({
        product_id: uid(id),
        slug: id,
        name: lang === "hi" ? hi : en,
        step: stepOf(sq, unit, lang),
        price_paise: p,
        price_delta_paise: own ? p - own[5] : null,
        score: Number((0.95 - (0.1 * i) / all.length).toFixed(3)),
        reason_code: code,
        reason_params: rp,
      }));
    return HttpResponse.json({ items });
  }),

  // PATCH lines (M3 §3.1, PR #102): { ops: [ remove | add | qty | substitute ] } with If-Match,
  // 1 to 20 ops applied in order. Anything else in the body is refused; one bad op changes nothing.
  http.patch("*/v1/cycles/:id/lines", async ({ request }) => {
    const state = loadState();
    const header = request.headers.get("if-match");
    if (!header)
      return problem(
        428,
        "request.precondition_required",
        "Send If-Match with the version you saw",
      );
    if (versionOf(header) !== state.version)
      return problem(412, "subscriptions.version_conflict", "The box changed; reload it");
    if (state.status !== "awaiting_approval" && state.status !== "approved")
      return problem(409, "subscriptions.not_editable", "This week can no longer be edited");
    const body = /** @type {{ ops?: any[] }} */ (await request.json());
    const bad = invalidOps(body);
    if (bad) return bad;
    const draft = structuredClone({ qty: {}, removed: [], added: {}, swaps: {}, ...state });
    for (const op of /** @type {any[]} */ (body.ops)) {
      const failed = applyOp(draft, op);
      if (failed) return failed;
    }
    // With Budget Lock on, an edit that adds to the box may not take the month past the limit.
    const lock = budgetLock(MONTH, EARLIER_SPEND);
    const now = cycleWith(draft).total_paise;
    if (lock && now > cycleWith(state).total_paise && lock.remaining_paise - now < 0)
      return problem(422, "subscriptions.budget_exceeded", "That goes over your Budget Lock", {
        over_paise: now - lock.remaining_paise,
      });
    saveState({ ...draft, version: state.version + 1 });
    return HttpResponse.json(cycle(langOf(request)));
  }),

  // PATCH meals: { date, slot } with If-Match: swaps that meal for the next alternative (our
  // proposal for M3; the spec has no meal edits).
  http.patch("*/v1/cycles/:id/meals", async ({ request }) => {
    const state = loadState();
    if (versionOf(request.headers.get("if-match")) !== state.version)
      return problem(412, "subscriptions.version_conflict", "The plan changed; reload it");
    if (state.status !== "awaiting_approval" && state.status !== "approved")
      return problem(409, "subscriptions.cutoff_passed", "Edits are closed for this week");
    const { date, slot } = /** @type {{ date: string, slot: string }} */ (await request.json());
    const meals = { ...(state.meals ?? {}) };
    const key = `${date}:${slot}`;
    const at = ALT_DISHES.findIndex(([en]) => en === meals[key]?.[0]);
    meals[key] = ALT_DISHES[(at + 1) % ALT_DISHES.length];
    saveState({ ...state, meals, version: state.version + 1 });
    return HttpResponse.json(cycle(langOf(request)));
  }),

  http.get("*/v1/cycles/current", ({ request }) =>
    localStorage.getItem(NO_CYCLE_KEY) ? noCycle() : HttpResponse.json(cycle(langOf(request))),
  ),
  // The first week for onboarding: from the household's rules and today's prices, nothing saved.
  http.get("*/v1/cycles/preview", ({ request }) => HttpResponse.json(cycle(langOf(request), true))),

  // @proposal (not in the contract yet) GET /v1/tiffin-offers?from=&to=: a home kitchen's lunch
  // tiffin for each day that is still cooked at home (designs Core-W2, HcTiffin).
  http.get("*/v1/tiffin-offers", ({ request }) => {
    const lang = langOf(request);
    const url = new URL(request.url);
    const [from, to] = [url.searchParams.get("from") ?? "", url.searchParams.get("to") ?? "~"];
    const items = DISHES.map((_, i) => `2026-10-${String(5 + i).padStart(2, "0")}`)
      .filter((date, i) => i !== TIFFIN_DAY && date >= from && date <= to)
      .filter((date) => !bookedTiffins().some((b) => b.delivery_date === date))
      .map((date) => ({
        date,
        slot: TIFFIN_OFFER.slot,
        kitchen_id: TIFFIN_OFFER.kitchen_id,
        cook: { name: pick(TIFFIN_OFFER.cook, lang) },
        dish: { name: pick(TIFFIN_OFFER.dish, lang), items: pick(TIFFIN_OFFER.items, lang) },
        price_paise: TIFFIN_OFFER.price_paise,
        order_by: "2026-10-05T15:30:00Z",
      }));
    return HttpResponse.json({ items });
  }),

  // GET /v1/budget-lock?month=: this month's limit and what the boxes spent, or 404 while it is off.
  http.get("*/v1/budget-lock", ({ request }) => {
    const month = new URL(request.url).searchParams.get("month") ?? MONTH;
    const lock = budgetLock(month, month === MONTH ? spentSoFar() : 0);
    return lock
      ? HttpResponse.json(lock)
      : problem(404, "subscriptions.no_budget_lock", "No Budget Lock for that month");
  }),

  // PUT /v1/budget-lock (Idempotency-Key): { month, budget_paise | null }. null switches it off.
  http.put("*/v1/budget-lock", async ({ request }) => {
    if (!request.headers.get("idempotency-key"))
      return problem(400, "request.idempotency_key_required", "Idempotency-Key header is required");
    const body = /** @type {Record<string, any>} */ (await request.json());
    // Every request schema forbids extra fields (additionalProperties: false).
    const extra = Object.keys(body).filter((k) => k !== "month" && k !== "budget_paise");
    if (extra.length)
      return problem(422, "request.invalid", "Request validation failed", {
        errors: extra.map((k) => ({
          loc: ["body", k],
          message: "Extra inputs are not permitted",
          type: "extra_forbidden",
        })),
      });
    const { month, budget_paise: budget } = body;
    if (typeof month !== "string" || budget === undefined)
      return problem(422, "request.invalid", "Send a month and a budget");
    // This month or next, at least ₹500 (M3 §3.1); anything else is subscriptions.budget_invalid.
    if (
      ![MONTH, NEXT_MONTH].includes(month) ||
      (budget !== null && (!Number.isInteger(budget) || budget < BUDGET_MIN))
    )
      return problem(422, "subscriptions.budget_invalid", "Pick this month or next, at least ₹500");
    localStorage.setItem(BUDGET_KEY, JSON.stringify({ ...budgets(), [month]: budget }));
    // null switches it off: 204, nothing to show.
    if (budget === null) return new HttpResponse(null, { status: 204 });
    const state = loadState();
    const spent = EARLIER_SPEND + (state.status === "skipped" ? 0 : cycle("en").total_paise);
    return HttpResponse.json(budgetLock(month, spent));
  }),

  // @proposal (not in the contract yet) GET/PUT /v1/budget-lock/preferences: what Autopilot does
  // when a week would go over the limit ("swap" is the only behaviour in M3 today).
  http.get("*/v1/budget-lock/preferences", () =>
    HttpResponse.json({ when_over: localStorage.getItem(WHEN_OVER_KEY) ?? "swap" }),
  ),
  http.put("*/v1/budget-lock/preferences", async ({ request }) => {
    const { when_over: mode } = /** @type {{ when_over?: string }} */ (await request.json());
    if (!mode || !["swap", "trim", "ask"].includes(mode))
      return problem(422, "request.invalid", "Pick swap, trim or ask");
    localStorage.setItem(WHEN_OVER_KEY, mode);
    return HttpResponse.json({ when_over: mode });
  }),

  transition("approve"),
  transition("skip"),
  transition("unskip"),

  // GET /v1/orders?status=payment_failed (M4): the box order the cut-off couldn't charge. Other
  // order reads are the orders mock's.
  http.get("*/v1/orders", ({ request }) => {
    if (new URL(request.url).searchParams.get("status") !== "payment_failed") return;
    retryDebit();
    const order = failedOrder();
    return HttpResponse.json({ items: order ? [order] : [], next_cursor: null });
  }),

  // @proposal (not in the contract yet) POST /v1/orders/{id}/cancel while payment_failed: lets the
  // family let the unpaid box go, with nothing charged. M4 allows cancel only while approved.
  http.post("*/v1/orders/:id/cancel", ({ request, params }) => {
    if (params.id !== uid("order-box-2026-10-06") || !failedOrder()) return;
    if (!request.headers.get("idempotency-key"))
      return problem(400, "request.idempotency_key_required", "Idempotency-Key header is required");
    const state = loadState();
    const order = failedOrder();
    // orders.cancelled with reason "payment" is mirrored onto the cycle as cancelled.
    saveState({ ...state, status: "cancelled", version: state.version + 1 });
    return HttpResponse.json({ ...order, status: "cancelled" });
  }),
];

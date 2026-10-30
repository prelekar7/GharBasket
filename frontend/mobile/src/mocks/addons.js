// M4 add-ons on an order (POST /v1/orders/{id}/addons, docs/modules/M4-orders.md §3.1) and M3 meal
// feedback (POST /v1/cycles/{id}/feedback, M3 §3.1), with the contract's refusals. The orders mock
// shows one approved box two days out, whose add-on lines live here; the cut-off is 21:00 the day
// before, so it is always open unless a test closes it.
import { HttpResponse, http } from "msw";
import { PRODUCTS } from "./catalog.js";
import { caller } from "./handlers.js";
import { fullOrder } from "./orders.js";
import { deliveryStage } from "./tracking.js";

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

const boxSlot = {
  id: "0199a000-0000-7000-8000-00000000c001",
  label: "Morning 8-10",
  start: "08:00:00",
  end: "10:00:00",
};
const langOf = (/** @type {Request} */ r) =>
  (r.headers.get("accept-language") ?? "").startsWith("hi") ? "hi" : "en";

export const ADDON_BOX_ID = "0199b000-0000-7000-8000-0000000000d1";
const ADDONS_KEY = "gb-mock-addons";
const MAX_QTY = 20;
const BOX_SLUGS = ["tomato", "methi", "rice"];

/** @typedef {{ version: number, qty: Record<string, number>, closed: boolean, keys: Record<string, number> }} AddonState */
/** @returns {AddonState} */
const load = () => ({
  version: 1,
  qty: {},
  closed: false,
  keys: {},
  ...JSON.parse(localStorage.getItem(ADDONS_KEY) ?? "{}"),
});
/** After this the cut-off has passed: the next change is 409 `orders.cutoff_passed`. */
export const closeAddons = () =>
  localStorage.setItem(ADDONS_KEY, JSON.stringify({ ...load(), closed: true }));
export const clearAddons = () => localStorage.removeItem(ADDONS_KEY);

const product = (/** @type {string} */ id) => PRODUCTS.find((p) => p[0] === id);
const boxIds = () => BOX_SLUGS.map((s) => PRODUCTS.find((p) => p[8] === s)?.[0]);

/** An India date `days` from today. @param {number} days */
const istDay = (days) =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(
    Date.now() + days * 86_400_000,
  );

/**
 * The approved box two days out as OrderOut, with its box lines and the add-ons so far.
 * @param {{ id: string, label: string, start: string, end: string }} slot @param {"en" | "hi"} lang
 */
export function addonBox(slot, lang) {
  const state = load();
  const i = lang === "hi" ? 2 : 1;
  /** @param {string} id @param {"box" | "addon"} kind @param {number} qty */
  const line = (id, kind, qty) => {
    const p = /** @type {NonNullable<ReturnType<typeof product>>} */ (product(id));
    return {
      product_id: id,
      slug: p[8],
      name: p[i],
      kind,
      qty,
      step: { qty: p[4], unit: p[5], label: `${p[4]} ${lang === "hi" ? p[6] : p[5]}` },
      unit_price_paise: p[7],
      total_paise: qty * p[7],
      reason_code: null,
      reason_params: null,
      substituted_from: null,
      shortage_qty: 0,
    };
  };
  const lines = [
    ...boxIds().map((id) => line(/** @type {string} */ (id), "box", 2)),
    ...Object.entries(state.qty).map(([id, qty]) => line(id, "addon", qty)),
  ];
  const date = istDay(2);
  const at = new Date(Date.now() - 3_600_000).toISOString();
  return {
    ...fullOrder({
      id: ADDON_BOX_ID,
      kind: "box",
      status: "approved",
      delivery_date: date,
      slot,
      total_paise: lines.reduce((n, l) => n + l.total_paise, 0),
      cycle_id: "0199b000-0000-7000-8000-0000000000c7",
      lines,
      timeline: [{ status: "approved", at, actor: "customer" }],
      created_at: at,
    }),
    version: state.version,
  };
}

/** Product ids of the seeded delivered boxes (orders.js BOX_LINES: …c000-…-000000000001 to 14). */
const deliveredBoxIds = new Set(
  Array.from(
    { length: 14 },
    (_, n) => `0199c000-0000-7000-8000-${String(n + 1).padStart(12, "0")}`,
  ),
);
const FEEDBACK_KEY = "gb-mock-feedback";
const SIGNALS = ["ran_out", "wasted", "just_right"];

export const addonHandlers = [
  http.post("*/v1/orders/:id/addons", async ({ request, params }) => {
    if (!caller(request)) return problem(401, "auth.invalid_token", "Sign in again");
    const key = request.headers.get("idempotency-key");
    if (!key)
      return problem(400, "request.idempotency_key_required", "Idempotency-Key header is required");
    const header = request.headers.get("if-match");
    if (!header)
      return problem(
        428,
        "request.precondition_required",
        "Send If-Match with the version you saw",
      );
    if (params.id !== ADDON_BOX_ID) return problem(404, "orders.not_found", "No such order");
    const state = load();
    if (state.keys[key] !== undefined) return HttpResponse.json(addonBox(boxSlot, langOf(request)));
    if (Number(header.replaceAll('"', "")) !== state.version)
      return problem(412, "request.version_mismatch", "The order changed; reload it.");
    if (state.closed)
      return problem(409, "orders.cutoff_passed", "The cut-off has passed for this order.");
    const body = /** @type {{ items?: { product_id: string, qty: number }[] }} */ (
      await request.json()
    );
    const items = body.items ?? [];
    if (items.length < 1 || items.length > MAX_QTY)
      return problem(422, "request.invalid", "Send 1 to 20 items");
    if (items.some((i) => !Number.isInteger(i.qty) || i.qty < 0 || i.qty > MAX_QTY))
      return problem(422, "orders.qty_invalid", "Quantity must be 0 to 20.");
    if (items.some((i) => boxIds().includes(i.product_id)))
      return problem(422, "orders.qty_invalid", "Box lines are edited in the week's box.");
    const unknown = items.filter((i) => i.qty > 0 && !product(i.product_id));
    if (unknown.length)
      return problem(422, "orders.product_unknown", "An add-on names an unknown product.", {
        product_ids: unknown.map((i) => i.product_id),
      });
    const qty = { ...state.qty };
    for (const i of items) {
      if (i.qty === 0) delete qty[i.product_id];
      else qty[i.product_id] = i.qty;
    }
    if (Object.keys(qty).length > MAX_QTY)
      return problem(422, "orders.qty_invalid", "At most 20 add-on lines.");
    localStorage.setItem(
      ADDONS_KEY,
      JSON.stringify({
        ...state,
        qty,
        version: state.version + 1,
        keys: { ...state.keys, [key]: 1 },
      }),
    );
    return HttpResponse.json(addonBox(boxSlot, langOf(request)));
  }),

  // Feedback: the seeded box delivered last week is always open; today's box once the van has
  // delivered it. Any other cycle is not delivered yet, which the API also answers with 422.
  http.post("*/v1/cycles/:id/feedback", async ({ request, params }) => {
    if (!caller(request)) return problem(401, "auth.invalid_token", "Sign in again");
    const key = request.headers.get("idempotency-key");
    if (!key)
      return problem(400, "request.idempotency_key_required", "Idempotency-Key header is required");
    const id = String(params.id);
    const delivered =
      id === "0199b000-0000-7000-8000-0000000000c5" ||
      (id === "0199b000-0000-7000-8000-0000000000c6" && deliveryStage().status === "delivered");
    if (!id.startsWith("0199b000-")) return problem(404, "subscriptions.no_cycle", "No such week");
    if (!delivered)
      return problem(
        422,
        "subscriptions.feedback_window_closed",
        "Tell us from delivery day for 14 days.",
      );
    const body = /** @type {{ items?: { product_id: string, signal: string }[] }} */ (
      await request.json()
    );
    const items = body.items ?? [];
    if (items.length < 1 || items.length > 50 || items.some((i) => !SIGNALS.includes(i.signal)))
      return problem(422, "request.invalid", "Send 1 to 50 items, each with a signal");
    const unknown = items.filter((i) => !deliveredBoxIds.has(i.product_id));
    if (unknown.length)
      return problem(422, "subscriptions.product_unknown", "That item was not in this box.", {
        product_ids: unknown.map((i) => i.product_id),
      });
    // One row per product, the latest tap wins.
    const all = JSON.parse(localStorage.getItem(FEEDBACK_KEY) ?? "{}");
    all[id] = { ...all[id], ...Object.fromEntries(items.map((i) => [i.product_id, i.signal])) };
    localStorage.setItem(FEEDBACK_KEY, JSON.stringify(all));
    return HttpResponse.json({ recorded: items.length }, { status: 201 });
  }),
];

/** What the household told us for a cycle, product id → signal (for tests). @param {string} cycleId */
export const feedbackOf = (cycleId) =>
  JSON.parse(localStorage.getItem(FEEDBACK_KEY) ?? "{}")[cycleId] ?? {};

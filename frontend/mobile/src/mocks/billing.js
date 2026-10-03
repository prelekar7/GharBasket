// M5 billing mocks (docs/modules/M5-billing.md §3): wallet, top-ups, mandates, plus M3
// subscriptions. The spec lists endpoints, not fields; these shapes are our proposal. Money is
// integer paise. A top-up order is only "paid" through the mock-only /v1/dev/razorpay/pay, which
// stands in for Razorpay Checkout and its webhook until spike S-1.
import { HttpResponse, http } from "msw";
import { caller, isNewUser } from "./handlers.js";

const KEY = "gb-mock-billing";
const MAX_BALANCE_PAISE = 1_000_000; // ₹10,000 wallet cap

/** @returns {{ balance_paise: number, entries: any[], orders: Record<string, any>, mandate: any, subscription: any }} */
const load = () => {
  try {
    return (
      JSON.parse(localStorage.getItem(KEY) ?? "null") ?? {
        balance_paise: 0,
        entries: [],
        orders: {},
        mandate: null,
        subscription: null,
      }
    );
  } catch {
    return { balance_paise: 0, entries: [], orders: {}, mandate: null, subscription: null };
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
const needsKey = (/** @type {Request} */ r) =>
  r.headers.get("idempotency-key")
    ? null
    : problem(400, "platform.idempotency_key_required", "Idempotency-Key header is required");

/** Fills a demo household's wallet the first time it is read (designs: ₹1,310). @param {Request} request */
function seed(request) {
  // A demo household starts with the designs’ wallet (₹1,310); a new one at ₹0.
  if (!localStorage.getItem(KEY) && !isNewUser(caller(request) ?? "")) {
    save({
      balance_paise: 131000,
      entries: [
        { id: "led-1", kind: "topup", amount_paise: 200000, at: "2026-10-02T10:15:00+05:30" },
        {
          id: "led-2",
          kind: "box_debit",
          amount_paise: -69000,
          ref: "2026-10-06",
          at: "2026-10-05T21:00:00+05:30",
        },
      ],
      orders: {},
      mandate: null,
      subscription: { id: "sub-1", status: "active", plan: "autopilot", payment_mode: "wallet" },
    });
  }
}

export const billingHandlers = [
  // A month of the ledger for the wallet (M5 §3: GET /v1/statements?period=YYYY-MM, JSON here).
  http.get("*/v1/statements", ({ request }) => {
    const period = new URL(request.url).searchParams.get("period") ?? "";
    if (!/^\d{4}-\d{2}$/.test(period))
      return problem(422, "billing.period_invalid", "period must be YYYY-MM");
    seed(request);
    const s = load();
    const inPeriod = (/** @type {any} */ e) => (e.ref ?? e.at).slice(0, 7) === period;
    const before = (/** @type {any} */ e) => (e.ref ?? e.at).slice(0, 7) < period;
    const entries = s.entries.filter(inPeriod);
    const opening = s.entries.filter(before).reduce((t, e) => t + e.amount_paise, 0);
    const credits = entries
      .filter((e) => e.amount_paise > 0)
      .reduce((t, e) => t + e.amount_paise, 0);
    const debits = entries
      .filter((e) => e.amount_paise < 0)
      .reduce((t, e) => t + e.amount_paise, 0);
    return HttpResponse.json({
      period,
      opening_paise: opening,
      credits_paise: credits,
      debits_paise: debits,
      closing_paise: opening + credits + debits,
      entries: [...entries].reverse(),
    });
  }),

  // Kindness points are not money: a separate balance with its own expiry (M5, M9).
  http.get("*/v1/kindness/balance", () =>
    HttpResponse.json({
      points: 340,
      goal: { points: 500, code: "child_meal" },
      expiring: [{ points: 120, on: "2026-12-31" }],
    }),
  ),

  http.get("*/v1/wallet", ({ request }) => {
    seed(request);
    const s = load();
    return HttpResponse.json({
      balance_paise: s.balance_paise,
      entries: s.entries.slice(-20).reverse(),
    });
  }),

  http.post("*/v1/wallet/topups", async ({ request }) => {
    const missing = needsKey(request);
    if (missing) return missing;
    const s = load();
    const key = /** @type {string} */ (request.headers.get("idempotency-key"));
    if (s.orders[key]) return HttpResponse.json(s.orders[key], { status: 201 });
    const { amount_paise: amount } = /** @type {any} */ (await request.json());
    if (!Number.isInteger(amount) || amount < 10_000)
      return problem(422, "billing.topup_too_small", "Top up at least ₹100");
    if (s.balance_paise + amount > MAX_BALANCE_PAISE)
      return problem(422, "billing.wallet_limit", "Wallet balance can be at most ₹10,000");
    const order = {
      id: `top-${Date.now()}`,
      razorpay_order_id: `order_mock_${Date.now()}`,
      key_id: "rzp_test_mock",
      amount_paise: amount,
      currency: "INR",
      status: "created",
    };
    s.orders[key] = order;
    save(s);
    return HttpResponse.json(order, { status: 201 });
  }),

  // Mock only: what Razorpay Checkout + the captured-payment webhook would do.
  http.post("*/v1/dev/razorpay/pay", async ({ request }) => {
    const { razorpay_order_id: id } = /** @type {any} */ (await request.json());
    const s = load();
    const order = Object.values(s.orders).find((o) => o.razorpay_order_id === id);
    if (!order) return problem(404, "billing.order_not_found", "Order not found");
    if (order.status !== "paid") {
      order.status = "paid";
      s.balance_paise += order.amount_paise;
      s.entries.push({
        id: `led-${s.entries.length + 1}`,
        kind: "topup",
        amount_paise: order.amount_paise,
        at: new Date().toISOString(),
      });
      save(s);
    }
    return HttpResponse.json({ razorpay_payment_id: `pay_mock_${Date.now()}`, status: "captured" });
  }),

  http.post("*/v1/mandates", ({ request }) => {
    const missing = needsKey(request);
    if (missing) return missing;
    const s = load();
    s.mandate = { id: `man-${Date.now()}`, status: "active", notice_hours: 24 };
    save(s);
    return HttpResponse.json(s.mandate, { status: 201 });
  }),

  http.post("*/v1/subscriptions", async ({ request }) => {
    const missing = needsKey(request);
    if (missing) return missing;
    const body = /** @type {any} */ (await request.json());
    const s = load();
    s.subscription = {
      id: `sub-${Date.now()}`,
      status: "active",
      plan: body.plan ?? "autopilot",
      payment_mode: body.payment_mode,
    };
    save(s);
    return HttpResponse.json(s.subscription, { status: 201 });
  }),
];

/** Dev helper: empty the mock wallet. */
export const resetBilling = () => localStorage.removeItem(KEY);

/**
 * Takes a box payment from the wallet (the cut-off charge, M5 W3); false when the balance is short.
 * @param {number} amountPaise @param {string} ref delivery date of the box
 */
export function chargeBox(amountPaise, ref) {
  const s = load();
  if (s.balance_paise < amountPaise) return false;
  s.balance_paise -= amountPaise;
  s.entries.push({
    id: `led-${s.entries.length + 1}`,
    kind: "box_debit",
    amount_paise: -amountPaise,
    ref,
    at: new Date().toISOString(),
  });
  save(s);
  return true;
}

/** The wallet balance the cut-off would charge from. */
export const walletBalance = () => load().balance_paise;

/** Demo scenario for 98000 00004: ₹410 in the wallet, short of the ₹690 box (design Core-D4). */
export function seedShortWallet() {
  save({
    balance_paise: 41000,
    entries: [{ id: "led-1", kind: "topup", amount_paise: 41000, at: "2026-10-01T10:15:00+05:30" }],
    orders: {},
    mandate: null,
    subscription: { id: "sub-1", status: "active", plan: "autopilot", payment_mode: "wallet" },
  });
}

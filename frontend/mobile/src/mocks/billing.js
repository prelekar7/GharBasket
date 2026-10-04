// M5 billing mocks (docs/modules/M5-billing.md §3): wallet, top-ups, mandates, plus M3
// subscriptions. The spec lists endpoints, not fields; these shapes are our proposal. Money is
// integer paise. A top-up order is only "paid" through the mock-only /v1/dev/razorpay/pay, which
// stands in for Razorpay Checkout and its webhook until spike S-1.
import { HttpResponse, http } from "msw";

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
    : problem(400, "request.idempotency_key_required", "Idempotency-Key header is required");

export const billingHandlers = [
  http.get("*/v1/wallet", () => {
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

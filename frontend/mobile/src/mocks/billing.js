// M5 billing mocks (docs/modules/M5-billing.md §3.1): wallet, top-ups in the frozen shapes, plus
// mandates, statements and M3 subscriptions (not in the contract yet). Money is integer paise,
// times are UTC. A top-up is only "paid" through /v1/dev/razorpay/pay, which the real API also
// serves in dev and test: it stands in for Razorpay Checkout and its webhook.
import { HttpResponse, http } from "msw";
import { caller, isNewUser } from "./handlers.js";

const KEY = "gb-mock-billing";
const CAP_PAISE = 1_000_000; // ₹10,000 wallet cap (ADR-0004)
const MONTH_PAISE = 2_000_000; // ₹20,000 of top-ups a calendar month
const UNPAID_MS = 30 * 60_000; // a top-up holds its amount for 30 minutes, then expires
const WINDOW_MS = 60 * 60_000; // more than 10 top-ups an hour: 429
const uuid = () => crypto.randomUUID();

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
  /** @type {Record<string, unknown>} */ extra = {},
) =>
  HttpResponse.json(
    { type: "about:blank", title, status, code, ...extra },
    { status, headers: { "content-type": "application/problem+json" } },
  );
const needsKey = (/** @type {Request} */ r) =>
  r.headers.get("idempotency-key")
    ? null
    : problem(400, "request.idempotency_key_required", "Idempotency-Key header is required");

/** One ledger entry in the real Entry shape. @param {string} kind @param {number} amount @param {string} at @param {object | null} ref */
const entry = (kind, amount, at, ref) => ({ id: uuid(), kind, amount_paise: amount, ref, at });
/** What a box debit points at. @param {string} deliveryDate */
const order = (deliveryDate) => ({ type: "order", id: uuid(), delivery_date: deliveryDate });

/** A top-up as the API shows it: the stored order, expired when unpaid for 30 minutes. */
const topUp = (/** @type {any} */ o) => ({
  id: o.id,
  razorpay_order_id: o.razorpay_order_id,
  key_id: o.key_id,
  amount_paise: o.amount_paise,
  currency: o.currency,
  status:
    o.status === "created" && Date.now() - Date.parse(o.created_at) > UNPAID_MS
      ? "expired"
      : o.status,
  created_at: o.created_at,
});

/** What can still be added now: under the cap, unpaid top-ups and this month's allowance. @param {ReturnType<typeof load>} s */
function room(s) {
  const month = new Date().toISOString().slice(0, 7);
  const orders = Object.values(s.orders).map(topUp);
  const unpaid = orders
    .filter((o) => o.status === "created")
    .reduce((t, o) => t + o.amount_paise, 0);
  const thisMonth = orders
    .filter((o) => ["created", "paid"].includes(o.status) && o.created_at.startsWith(month))
    .reduce((t, o) => t + o.amount_paise, 0);
  return {
    cap: Math.max(0, CAP_PAISE - s.balance_paise - unpaid),
    month: Math.max(0, MONTH_PAISE - thisMonth),
  };
}

/** A mandate as the API shows it, without the idempotency key the mock keeps beside it. */
const shown = (/** @type {any} */ { key, ...m }) => m;

/** Fills a demo household's wallet the first time it is read (designs: ₹1,310). @param {Request} request */
function seed(request) {
  // A demo household starts with the designs’ wallet (₹1,310); a new one at ₹0.
  if (!localStorage.getItem(KEY) && !isNewUser(caller(request) ?? "")) {
    save({
      balance_paise: 131000,
      entries: [
        entry("topup", 200000, "2026-10-02T04:45:00Z", { type: "topup", id: uuid() }),
        entry("box_debit", -69000, "2026-10-05T15:30:00Z", order("2026-10-06")),
      ],
      orders: {},
      mandate: null,
      subscription: { id: "sub-1", status: "active", plan: "autopilot", payment_mode: "wallet" },
    });
  }
}

export const billingHandlers = [
  // @proposal (not in the contract yet): a month of the ledger for the wallet (M5 §3: GET
  // /v1/statements?period=YYYY-MM, JSON here), entries in the real Entry shape.
  http.get("*/v1/statements", ({ request }) => {
    const period = new URL(request.url).searchParams.get("period") ?? "";
    if (!/^\d{4}-\d{2}$/.test(period))
      return problem(422, "billing.period_invalid", "period must be YYYY-MM");
    seed(request);
    const s = load();
    const month = (/** @type {any} */ e) =>
      new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" })
        .format(new Date(e.at))
        .slice(0, 7);
    const inPeriod = (/** @type {any} */ e) => month(e) === period;
    const before = (/** @type {any} */ e) => month(e) < period;
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

  // WalletOut: balance, the cap, what can still be added now, and the latest 20 entries.
  http.get("*/v1/wallet", ({ request }) => {
    seed(request);
    const s = load();
    const r = room(s);
    return HttpResponse.json({
      balance_paise: s.balance_paise,
      cap_paise: CAP_PAISE,
      topup_room_paise: Math.min(r.cap, r.month),
      entries: s.entries.slice(-20).reverse(),
    });
  }),

  // POST /v1/wallet/topups (Idempotency-Key, one per amount): the same key replays the same order.
  http.post("*/v1/wallet/topups", async ({ request }) => {
    const missing = needsKey(request);
    if (missing) return missing;
    seed(request);
    const s = load();
    const key = /** @type {string} */ (request.headers.get("idempotency-key"));
    const body = /** @type {any} */ (await request.json().catch(() => null));
    const extra = Object.keys(body ?? {}).filter((k) => k !== "amount_paise");
    const amount = body?.amount_paise;
    if (!Number.isInteger(amount) || amount < 1 || amount > 10_000_000 || extra.length)
      return problem(422, "request.invalid", "Unprocessable Content", {
        errors: [
          {
            loc: ["body", extra[0] ?? "amount_paise"],
            message: extra.length
              ? "Extra inputs are not permitted"
              : "Input should be a valid integer",
            type: extra.length ? "extra_forbidden" : "int_type",
          },
        ],
      });
    const before = s.orders[key];
    if (before)
      return before.amount_paise === amount
        ? HttpResponse.json(topUp(before), { status: 201 })
        : problem(422, "request.idempotency_mismatch", "That key was used with a different amount");
    if (amount < 10_000 || amount % 100 !== 0)
      return problem(422, "billing.topup_too_small", "Top up at least ₹100, in whole rupees.");
    const recent = Object.values(s.orders).filter(
      (o) => Date.now() - Date.parse(o.created_at) < WINDOW_MS,
    );
    if (recent.length >= 10)
      return problem(429, "billing.too_many_topups", "Too many top-ups started in an hour");
    const r = room(s);
    if (amount > r.cap)
      return problem(422, "billing.wallet_limit", "The wallet can hold at most ₹10,000.", {
        room_paise: Math.min(r.cap, r.month),
      });
    if (amount > r.month)
      return problem(
        422,
        "billing.monthly_topup_limit",
        "This month's top-ups would pass ₹20,000.",
        {
          room_paise: Math.min(r.cap, r.month),
        },
      );
    const hex = uuid().replaceAll("-", "");
    const order = {
      id: uuid(),
      razorpay_order_id: `order_dev_${hex.slice(0, 14)}`,
      key_id: "rzp_test_dev",
      amount_paise: amount,
      currency: "INR",
      status: "created",
      created_at: new Date().toISOString(),
    };
    s.orders[key] = order;
    save(s);
    return HttpResponse.json(topUp(order), { status: 201 });
  }),

  // GET /v1/wallet/topups/{id}: polled after Checkout until "paid".
  http.get("*/v1/wallet/topups/:id", ({ params }) => {
    const order = Object.values(load().orders).find((o) => o.id === params.id);
    return order
      ? HttpResponse.json(topUp(order))
      : problem(404, "billing.topup_not_found", "Top-up not found");
  }),

  // POST /v1/dev/razorpay/pay (dev and test only, not in openapi.json): what Razorpay Checkout
  // and the captured-payment webhook would do, with the same processing as a real payment.
  http.post("*/v1/dev/razorpay/pay", async ({ request }) => {
    const { razorpay_order_id: id } = /** @type {any} */ (await request.json());
    const s = load();
    const order = Object.values(s.orders).find((o) => o.razorpay_order_id === id);
    if (!order) return problem(404, "billing.topup_not_found", "Top-up not found");
    if (order.status === "created") {
      // A payment that no longer fits under the cap goes back to its source.
      if (s.balance_paise + order.amount_paise > CAP_PAISE) order.status = "refunded";
      else {
        order.status = "paid";
        s.balance_paise += order.amount_paise;
        s.entries.push(
          entry("topup", order.amount_paise, new Date().toISOString(), {
            type: "topup",
            id: order.id,
          }),
        );
      }
      save(s);
    }
    return HttpResponse.json({
      razorpay_payment_id: `pay_dev_${uuid().replaceAll("-", "").slice(0, 14)}`,
      status: "captured",
    });
  }),

  // POST /v1/mandates (M5, Idempotency-Key): UPI Autopay up to `max_paise` a month. The mock
  // skips the UPI app's approval and makes the mandate active at once; boxes then charge to UPI.
  http.post("*/v1/mandates", async ({ request }) => {
    const missing = needsKey(request);
    if (missing) return missing;
    const s = load();
    const key = /** @type {string} */ (request.headers.get("idempotency-key"));
    if (s.mandate?.key === key) return HttpResponse.json(shown(s.mandate), { status: 201 });
    const { max_paise: max = 500000 } = /** @type {{ max_paise?: number }} */ (
      await request.json().catch(() => ({}))
    );
    if (!Number.isInteger(max) || max < 10_000 || max > 1_500_000)
      return problem(422, "billing.mandate_invalid", "Mandate limit must be ₹100 to ₹15,000");
    s.mandate = {
      id: `man-${Date.now()}`,
      key,
      status: "active",
      max_paise: max,
      notice_hours: 24,
    };
    if (s.subscription) s.subscription.payment_mode = "autopay";
    save(s);
    return HttpResponse.json(shown(s.mandate), { status: 201 });
  }),

  // GET /v1/mandates/current (our proposal beside M5's GET /v1/mandates/{id}): the caller's live
  // mandate, or 404 when boxes are paid from the wallet.
  http.get("*/v1/mandates/current", ({ request }) => {
    seed(request);
    const m = load().mandate;
    return m && m.status === "active"
      ? HttpResponse.json(shown(m))
      : problem(404, "billing.no_mandate", "No UPI Autopay mandate");
  }),

  // DELETE /v1/mandates/{id} (M5): cancel; boxes are paid from the wallet again.
  http.delete("*/v1/mandates/:id", ({ params }) => {
    const s = load();
    if (!s.mandate || s.mandate.id !== params.id)
      return problem(404, "billing.no_mandate", "No such mandate");
    s.mandate = null;
    if (s.subscription) s.subscription.payment_mode = "wallet";
    save(s);
    return new HttpResponse(null, { status: 204 });
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
  s.entries.push(entry("box_debit", -amountPaise, new Date().toISOString(), order(ref)));
  save(s);
  return true;
}

/** The wallet balance the cut-off would charge from. */
export const walletBalance = () => load().balance_paise;

/** Demo scenario for 98000 00004: ₹410 in the wallet, short of the ₹690 box (design Core-D4). */
export function seedShortWallet() {
  save({
    balance_paise: 41000,
    entries: [entry("topup", 41000, "2026-10-01T04:45:00Z", { type: "topup", id: uuid() })],
    orders: {},
    mandate: null,
    subscription: { id: "sub-1", status: "active", plan: "autopilot", payment_mode: "wallet" },
  });
}

// M5 billing mocks (docs/modules/M5-billing.md): wallet, top-ups, statements, refund to source
// (§3.3) and Kindness points (§3.6) in the frozen shapes, plus mandates (§3.7: no contract, a
// design-review mock). Money is integer paise, times are UTC. A top-up is only "paid" through /v1/dev/razorpay/pay, which the real API also
// serves in dev and test: it stands in for Razorpay Checkout and its webhook.
import { HttpResponse, http } from "msw";
import { caller, isNewUser } from "./handlers.js";

const KEY = "gb-mock-billing";
const CAP_PAISE = 1_000_000; // ₹10,000 wallet cap (ADR-0004)
const MONTH_PAISE = 2_000_000; // ₹20,000 of top-ups a calendar month
const UNPAID_MS = 30 * 60_000; // a top-up holds its amount for 30 minutes, then expires
const WINDOW_MS = 60 * 60_000; // more than 10 top-ups an hour: 429
const uuid = () => crypto.randomUUID();

/** @returns {{ balance_paise: number, entries: any[], orders: Record<string, any>, mandate: any, subscription: any, refunds?: any[] }} */
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

const istDay = (/** @type {string} */ at) =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date(at));
const SETTLE_MS = 10_000; // the send_refunds job settles a dev refund; the mock does it after 10 s
const REFUNDS_PER_DAY = 3;

/** A cursor page of newest-first rows: the cursor is an offset, limit 1 to 100 (default 50). @param {Request} request @param {any[]} rows */
function pageOf(request, rows) {
  const q = new URL(request.url).searchParams;
  const limit = q.has("limit") ? Number(q.get("limit")) : 50;
  const at = q.has("cursor") ? Number(q.get("cursor")) : 0;
  if (!Number.isInteger(limit) || limit < 1 || limit > 100 || !Number.isInteger(at) || at < 0)
    return problem(422, "request.invalid", "Unprocessable Content", {
      errors: [
        {
          loc: ["query", q.has("limit") && !(limit >= 1 && limit <= 100) ? "limit" : "cursor"],
          message: "Input should be valid",
          type: "value_error",
        },
      ],
    });
  const next = at + limit;
  return HttpResponse.json({
    items: rows.slice(at, next),
    next_cursor: next < rows.length ? String(next) : null,
  });
}

/** What POST /v1/wallet/refund-to-source can send back now: the balance, up to what paid top-ups can still take. @param {ReturnType<typeof load>} s */
function refundable(s) {
  const topped = s.entries
    .filter((e) => e.kind === "topup")
    .reduce((t, e) => t + e.amount_paise, 0);
  const taken = (s.refunds ?? []).reduce((t, r) => t + r.amount_paise, 0);
  return Math.max(0, Math.min(s.balance_paise, topped - taken));
}

/** A refund as the API shows it: pending, then processed once the job has run. @param {any} r */
function refundOut(r) {
  const done = Date.now() - Date.parse(r.created_at) > SETTLE_MS;
  return {
    id: r.id,
    amount_paise: r.amount_paise,
    reason: "customer",
    status: done ? "processed" : "pending",
    parts: r.parts.map((/** @type {any} */ p) => ({
      ...p,
      status: done ? "processed" : "pending",
      razorpay_refund_id: done ? `rfnd_dev_${p.topup_id.replaceAll("-", "").slice(0, 14)}` : null,
    })),
    created_at: r.created_at,
    settled_at: done ? new Date(Date.parse(r.created_at) + SETTLE_MS).toISOString() : null,
  };
}

// ---- Kindness points (M5 §3.6): a separate currency in lots that expire after 365 days.
const POINTS_KEY = "gb-mock-points";
const GOAL = { points: 500, code: "child_meal" };
const SOON_DAYS = 90;
/** @typedef {{ id: string, kind: "points_earned" | "points_expired" | "points_donated", points: number, ref: { type: "donation", id: string } | null, expires_on: string | null, at: string }} PointsEntry */
/** @returns {{ lots: { points: number, on: string }[], entries: PointsEntry[], keys: Record<string, any> }} */
const loadPoints = () => {
  try {
    const s = JSON.parse(localStorage.getItem(POINTS_KEY) ?? "null");
    if (s) return s;
  } catch {
    // fall through to the seed
  }
  return {
    lots: [
      { points: 120, on: "2026-12-31" },
      { points: 220, on: "2027-09-29" },
    ],
    entries: [
      {
        id: uuid(),
        kind: "points_earned",
        points: 220,
        ref: { type: "donation", id: "don-1" },
        expires_on: "2027-09-29",
        at: "2026-09-29T11:00:00Z",
      },
      {
        id: uuid(),
        kind: "points_earned",
        points: 120,
        ref: null,
        expires_on: "2026-12-31",
        at: "2025-12-31T10:00:00Z",
      },
    ],
    keys: {},
  };
};
const savePoints = (/** @type {ReturnType<typeof loadPoints>} */ s) =>
  localStorage.setItem(POINTS_KEY, JSON.stringify(s));
const totalPoints = (/** @type {ReturnType<typeof loadPoints>} */ s) =>
  s.lots.reduce((t, l) => t + l.points, 0);

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
  // GET /v1/statements?period=YYYY-MM (M5 §3.2): a month of the wallet ledger, newest first, with
  // the running balance after each entry. A month after the current one (IST) is refused.
  http.get("*/v1/statements", ({ request }) => {
    const period = new URL(request.url).searchParams.get("period") ?? "";
    if (
      !/^\d{4}-(0[1-9]|1[0-2])$/.test(period) ||
      period > istDay(new Date().toISOString()).slice(0, 7)
    )
      return problem(422, "billing.period_invalid", "period must be YYYY-MM, not after this month");
    seed(request);
    const s = load();
    const month = (/** @type {any} */ e) => istDay(e.at).slice(0, 7);
    let running = 0;
    const all = s.entries.map((e) => {
      running += e.amount_paise;
      return { ...e, balance_after_paise: running };
    });
    const entries = all.filter((e) => month(e) === period);
    const opening = all.filter((e) => month(e) < period).reduce((t, e) => t + e.amount_paise, 0);
    const credits = entries
      .filter((e) => e.amount_paise > 0)
      .reduce((t, e) => t + e.amount_paise, 0);
    const debits = entries
      .filter((e) => e.amount_paise < 0)
      .reduce((t, e) => t + e.amount_paise, 0);
    return HttpResponse.json({
      period,
      currency: "INR",
      opening_paise: opening,
      credits_paise: credits,
      debits_paise: debits,
      closing_paise: opening + credits + debits,
      entries: [...entries].reverse(),
      generated_at: new Date().toISOString(),
    });
  }),

  // GET /v1/kindness/balance (M5 §3.6): points apart from money, what expires in the next 90 days
  // (at most 3 dates, soonest first) and the points one donated meal takes.
  http.get("*/v1/kindness/balance", () => {
    const s = loadPoints();
    const horizon = istDay(new Date(Date.now() + SOON_DAYS * 86_400_000).toISOString());
    const soon = [...s.lots]
      .filter((l) => l.on <= horizon)
      .sort((x, y) => x.on.localeCompare(y.on))
      .slice(0, 3)
      .map((l) => ({ points: l.points, on: l.on }));
    return HttpResponse.json({ points: totalPoints(s), expiring: soon, goal: GOAL });
  }),

  // GET /v1/kindness/entries (M5 §3.6): earned, expired and donated points, newest first.
  http.get("*/v1/kindness/entries", ({ request }) => pageOf(request, loadPoints().entries)),

  // POST /v1/kindness/donations (Idempotency-Key): points become meals for children, taken from the
  // oldest lots first. No money is touched; fewer points than meals × goal is 409.
  http.post("*/v1/kindness/donations", async ({ request }) => {
    const missing = needsKey(request);
    if (missing) return missing;
    const key = /** @type {string} */ (request.headers.get("idempotency-key"));
    const body = /** @type {any} */ (await request.json().catch(() => null));
    const meals = body?.meals;
    if (!Number.isInteger(meals) || meals < 1 || meals > 10)
      return problem(422, "request.invalid", "Unprocessable Content", {
        errors: [
          { loc: ["body", "meals"], message: "Input should be 1 to 10", type: "value_error" },
        ],
      });
    const s = loadPoints();
    const before = s.keys[key];
    if (before)
      return before.meals === meals
        ? HttpResponse.json(before, { status: 201 })
        : problem(422, "request.idempotency_mismatch", "That key was used with different meals");
    const needed = meals * GOAL.points;
    const points = totalPoints(s);
    if (points < needed)
      return problem(409, "billing.not_enough_points", "Not enough Kindness points yet", {
        points,
        needed,
      });
    let left = needed;
    s.lots.sort((x, y) => x.on.localeCompare(y.on));
    for (const lot of s.lots) {
      const take = Math.min(lot.points, left);
      lot.points -= take;
      left -= take;
    }
    s.lots = s.lots.filter((l) => l.points > 0);
    const done = {
      id: uuid(),
      meals,
      points: needed,
      code: GOAL.code,
      at: new Date().toISOString(),
    };
    s.entries.unshift({
      id: done.id,
      kind: "points_donated",
      points: -needed,
      ref: null,
      expires_on: null,
      at: done.at,
    });
    s.keys[key] = done;
    savePoints(s);
    return HttpResponse.json(done, { status: 201 });
  }),

  // WalletOut: balance, the cap, what can still be added now, what can go back to the bank, and
  // the latest 20 entries.
  http.get("*/v1/wallet", ({ request }) => {
    seed(request);
    const s = load();
    const r = room(s);
    return HttpResponse.json({
      balance_paise: s.balance_paise,
      cap_paise: CAP_PAISE,
      topup_room_paise: Math.min(r.cap, r.month),
      refundable_paise: refundable(s),
      entries: s.entries.slice(-20).reverse(),
    });
  }),

  // GET /v1/wallet/entries?cursor=&limit=: Page[Entry], newest first.
  http.get("*/v1/wallet/entries", ({ request }) => {
    seed(request);
    return pageOf(request, [...load().entries].reverse());
  }),

  // POST /v1/wallet/refund-to-source (M5 §3.3, Idempotency-Key): unused balance back to the paying
  // UPI or card. The wallet is debited at once; the refund is processed later by a job.
  http.post("*/v1/wallet/refund-to-source", async ({ request }) => {
    const missing = needsKey(request);
    if (missing) return missing;
    seed(request);
    const s = load();
    s.refunds ??= [];
    const key = /** @type {string} */ (request.headers.get("idempotency-key"));
    const body = /** @type {any} */ (await request.json().catch(() => null));
    const asked = body?.amount_paise ?? null;
    if (asked !== null && !Number.isInteger(asked))
      return problem(422, "request.invalid", "Unprocessable Content", {
        errors: [
          {
            loc: ["body", "amount_paise"],
            message: "Input should be a valid integer",
            type: "int_type",
          },
        ],
      });
    const before = s.refunds.find((r) => r.key === key);
    if (before)
      return before.asked === asked
        ? HttpResponse.json(refundOut(before), { status: 202 })
        : problem(422, "request.idempotency_mismatch", "That key was used with a different amount");
    const open = s.refunds.find((r) => refundOut(r).status === "pending");
    if (open)
      return problem(409, "billing.refund_in_progress", "An earlier refund is still on its way", {
        refund_id: open.id,
      });
    const today = istDay(new Date().toISOString());
    if (s.refunds.filter((r) => istDay(r.created_at) === today).length >= REFUNDS_PER_DAY)
      return problem(429, "billing.too_many_refunds", "Too many refund requests today");
    const can = refundable(s);
    const amount = asked ?? can;
    if (amount < 100) return problem(422, "billing.refund_too_small", "Refund at least ₹1");
    if (amount > can)
      return problem(
        422,
        "billing.refund_exceeds_refundable",
        "That is more than can go back to your bank",
        { refundable_paise: can },
      );
    const topup = [...s.entries].reverse().find((e) => e.kind === "topup");
    const refund = {
      id: uuid(),
      key,
      asked,
      amount_paise: amount,
      created_at: new Date().toISOString(),
      parts: [
        {
          topup_id: topup?.ref?.id ?? uuid(),
          razorpay_payment_id: `pay_dev_${uuid().replaceAll("-", "").slice(0, 14)}`,
          amount_paise: amount,
        },
      ],
    };
    s.refunds.push(refund);
    s.balance_paise -= amount;
    s.entries.push(
      entry("refund_to_source", -amount, refund.created_at, { type: "refund", id: refund.id }),
    );
    save(s);
    return HttpResponse.json(refundOut(refund), { status: 202 });
  }),

  // GET /v1/wallet/refunds[/{id}]: status of refunds to source, newest first.
  http.get("*/v1/wallet/refunds", ({ request }) =>
    pageOf(request, [...(load().refunds ?? [])].reverse().map(refundOut)),
  ),
  http.get("*/v1/wallet/refunds/:id", ({ params }) => {
    const r = (load().refunds ?? []).find((x) => x.id === params.id);
    return r
      ? HttpResponse.json(refundOut(r))
      : problem(404, "billing.refund_not_found", "Refund not found");
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
export const resetBilling = () => {
  localStorage.removeItem(KEY);
  localStorage.removeItem(POINTS_KEY);
};

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

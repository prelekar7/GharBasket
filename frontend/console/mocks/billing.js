// Money-side console mocks (M5): reconciliation and refunds review. Only
// `GET /v1/admin/reconciliation` is in the spec (§3); everything else here is a proposal for the console:
//   POST /v1/admin/reconciliation/check            run the hourly job now
//   POST /v1/admin/reconciliation/{id}/credit      post the missing wallet credit (a correcting journal entry)
//   POST /v1/admin/reconciliation/{id}/investigate mark a mismatch as being looked at
//   GET  /v1/admin/refunds (read-only: refunds are automatic, M5 §3.3)
// All money is integer paise. Nothing is edited or deleted: every fix would be a new ledger entry.
import { HttpResponse, http } from "msw";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { SOCIETY_ID } from "./ids.js";
import { STORY_NOW } from "./orders.js";
import { idempotent, mockStore, mockUuid, problem, roleOf } from "./util.js";

/** @param {Request} request @param {("admin" | "hub_manager")[]} roles */
const allowed = (request, roles) => roles.includes(/** @type {any} */ (roleOf(request)));

// ---------- Reconciliation ----------

/**
 * @typedef {{ id: string, provider_ref: string, at: string, razorpay_paise: number, ledger_paise: number | null,
 *   kind: "no_wallet_entry" | "amount_differs" | "no_payment", payer: string, note: string, investigating: boolean }} Mismatch
 */

const recon = () =>
  mockStore("reconciliation", () => ({
    checked_at: "2026-10-05T11:00:00+05:30",
    matched: 128,
    /** @type {Mismatch[]} */
    items: [
      {
        id: "rec-1",
        provider_ref: "pay_Nq4xK7mT2vLb91",
        at: "2026-10-05T10:42:00+05:30",
        razorpay_paise: 100000,
        ledger_paise: null,
        kind: "no_wallet_entry",
        payer: "Priya Sharma, Sunrise Towers",
        note: "Captured, but the webhook wasn’t processed, so the wallet wasn’t credited",
        investigating: false,
      },
      {
        id: "rec-2",
        provider_ref: "pay_Nq3Zc8rWp5Yd07",
        at: "2026-10-05T09:15:00+05:30",
        razorpay_paise: 200000,
        ledger_paise: 20000,
        kind: "amount_differs",
        payer: "Anil Kulkarni, Krishna Kunj",
        note: "Amounts differ by ₹1,800",
        investigating: false,
      },
    ],
  }));

const reconBody = () => {
  const r = recon();
  const n = (/** @type {Mismatch["kind"]} */ k) => r.items.filter((i) => i.kind === k).length;
  return {
    checked_at: r.checked_at,
    as_of: STORY_NOW,
    window_hours: 24,
    counts: {
      matched: r.matched,
      no_wallet_entry: n("no_wallet_entry"),
      amount_differs: n("amount_differs"),
      no_payment: n("no_payment"),
    },
    items: r.items,
  };
};

// ---------- Refunds ----------

/**
 * One order refund, the real RefundRow of `GET /v1/admin/refunds` (M5 §3.8, backend/api/openapi.json).
 * @typedef {{ id: string, order_id: string, order_kind: "box" | "tiffin" | "addon", delivery_date: string,
 *   society: { id: string, name: string }, flat_label: string | null,
 *   reason: "shortage" | "issue" | "undelivered" | "no_kitchen" | "carry_over" | "other",
 *   product_id: string | null, issue_id: string | null, amount_paise: number, to: "wallet" | "source",
 *   refund_id: string | null, status: "credited" | "pending" | "processing" | "processed" | "partly_failed" | "failed",
 *   created_at: string }} RefundRow
 */

const REFUND_REASONS = ["shortage", "issue", "undelivered", "no_kitchen", "carry_over", "other"];
const SUNRISE = { id: SOCIETY_ID["sunrise-towers"], name: "Sunrise Towers" };
const KRISHNA = { id: SOCIETY_ID["krishna-kunj"], name: "Krishna Kunj" };

/** @param {number} n @returns {RefundRow} a wallet refund, newest first by n */
const walletRefund = (n) => ({
  id: mockUuid("b901", n),
  order_id: mockUuid("b902", n),
  order_kind: n % 4 === 0 ? "addon" : "box",
  delivery_date: n % 3 === 0 ? "2026-10-05" : "2026-10-03",
  society: n % 2 ? SUNRISE : KRISHNA,
  flat_label: n % 5 === 0 ? null : `${"AB"[n % 2]} wing, ${"AB"[n % 2]}-${100 + n}`,
  reason: /** @type {RefundRow["reason"]} */ (REFUND_REASONS[n % 3 === 0 ? 4 : n % 2 ? 0 : 1]),
  product_id: n % 2 ? mockUuid("b903", n) : null,
  issue_id: n % 2 ? null : mockUuid("b904", n),
  amount_paise: 1500 + (n % 7) * 500,
  to: "wallet",
  refund_id: null,
  status: "credited",
  created_at: new Date(Date.parse("2026-10-05T18:00:00+05:30") - n * 3_600_000).toISOString(),
});

/** 57 rows (so the default page of 50 has a next page): a few to the bank, one stuck, the rest in the wallet. */
const refunds = () =>
  mockStore("refunds", () => {
    const rows = Array.from({ length: 57 }, (_, i) => walletRefund(i + 1));
    Object.assign(rows[1], {
      reason: "undelivered",
      order_kind: "tiffin",
      to: "source",
      amount_paise: 41000,
      refund_id: mockUuid("b905", 2),
      status: "processing",
    });
    Object.assign(rows[3], {
      to: "source",
      refund_id: mockUuid("b905", 4),
      status: "failed",
      amount_paise: 9000,
    });
    return rows;
  });

/** The opaque cursor is the offset into the list, base64 like the API's. @param {number} n */
const toCursor = (n) => Buffer.from(`o:${n}`).toString("base64url");
/** @param {string} c @returns {number | null} */
const fromCursor = (c) => {
  const m = /^o:(\d+)$/.exec(Buffer.from(c, "base64url").toString());
  return m ? Number(m[1]) : null;
};

export const billingHandlers = [
  // GET /v1/admin/reconciliation (M5 §3, admin)
  http.get("*/v1/admin/reconciliation", ({ request }) =>
    allowed(request, ["admin"])
      ? HttpResponse.json(reconBody())
      : problem(403, "auth.forbidden", "Admins only"),
  ),

  http.post("*/v1/admin/reconciliation/check", ({ request }) =>
    idempotent(request, () => {
      if (!allowed(request, ["admin"])) return problem(403, "auth.forbidden", "Admins only");
      recon().checked_at = STORY_NOW;
      return HttpResponse.json(reconBody());
    }),
  ),

  // Credit the wallet: posts a correcting journal entry. Only for "paid, no wallet entry".
  http.post("*/v1/admin/reconciliation/:id/credit", ({ request, params }) =>
    idempotent(request, () => {
      if (!allowed(request, ["admin"])) return problem(403, "auth.forbidden", "Admins only");
      const r = recon();
      const i = r.items.findIndex((x) => x.id === params.id);
      if (i < 0) return problem(404, "billing.mismatch_not_found", "Already resolved or not found");
      if (r.items[i].kind !== "no_wallet_entry")
        return problem(409, "billing.not_creditable", "This mismatch can't be fixed by a credit");
      const [done] = r.items.splice(i, 1);
      r.matched += 1;
      return HttpResponse.json({ credited_paise: done.razorpay_paise });
    }),
  ),

  http.post("*/v1/admin/reconciliation/:id/investigate", ({ request, params }) =>
    idempotent(request, () => {
      if (!allowed(request, ["admin"])) return problem(403, "auth.forbidden", "Admins only");
      const item = recon().items.find((x) => x.id === params.id);
      if (!item) return problem(404, "billing.mismatch_not_found", "Already resolved or not found");
      item.investigating = true;
      return HttpResponse.json(item);
    }),
  ),

  // GET /v1/admin/refunds?hub_id=&from=&to=&reason=&destination=&cursor=&limit= (M5 §3.8): read-only, newest first
  http.get("*/v1/admin/refunds", ({ request }) => {
    const role = roleOf(request);
    if (role !== "admin" && role !== "hub_manager")
      return problem(403, "auth.forbidden", "Not allowed");
    const q = new URL(request.url).searchParams;
    const hub = q.get("hub_id");
    if (!hub && role !== "admin")
      return problem(422, "orders.hub_required", "Say which hub: hub_id is required for hub staff");
    if (hub && hub !== KHARGHAR_HUB_ID && role !== "admin")
      return problem(403, "auth.forbidden", "That hub is not yours");
    const reason = q.get("reason");
    const destination = q.get("destination");
    const from = q.get("from");
    const to = q.get("to");
    const limit = q.has("limit") ? Number(q.get("limit")) : 50;
    const offset = q.has("cursor") ? fromCursor(q.get("cursor") ?? "") : 0;
    const days = from && to ? (Date.parse(to) - Date.parse(from)) / 86_400_000 : 0;
    if (
      (reason && !REFUND_REASONS.includes(reason)) ||
      (destination && destination !== "wallet" && destination !== "source") ||
      !(limit >= 1 && limit <= 100) ||
      days > 31
    )
      return problem(422, "request.invalid", "A window over 31 days or an unknown value");
    if (offset === null)
      return problem(422, "request.invalid_cursor", "That page link is not valid");
    const rows = refunds()
      .filter((r) => (!reason || r.reason === reason) && (!from || r.delivery_date >= from))
      .filter((r) => (!to || r.delivery_date <= to) && (!destination || r.to === destination));
    const items = rows.slice(offset, offset + limit);
    return HttpResponse.json({
      items,
      next_cursor: offset + limit < rows.length ? toCursor(offset + limit) : null,
    });
  }),
];

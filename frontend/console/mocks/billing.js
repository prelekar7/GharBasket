// Money-side console mocks (M5): reconciliation, refunds review and partner payouts. Only
// `GET /v1/admin/reconciliation` is in the spec (§3); everything else here is a proposal for the console:
//   POST /v1/admin/reconciliation/check            run the hourly job now
//   POST /v1/admin/reconciliation/{id}/credit      post the missing wallet credit (a correcting journal entry)
//   POST /v1/admin/reconciliation/{id}/investigate mark a mismatch as being looked at
//   GET  /v1/admin/refunds, POST …/{id}/approve | reject | reopen
//   GET  /v1/admin/payouts?week=, POST …/{id}/retry
// All money is integer paise. Nothing is edited or deleted: every fix would be a new ledger entry.
import { HttpResponse, http } from "msw";
import { STORY_NOW } from "./orders.js";
import { idempotent, mockStore, problem, roleOf } from "./util.js";

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
 * @typedef {{ id: string, flat: string, society: string, order_label: string, why: string, what_happened: string,
 *   reported_by: string, amount_paise: number, to: "wallet", wallet_before_paise: number,
 *   status: "pending" | "approved" | "rejected" }} Refund
 */

const refunds = () =>
  mockStore(
    "refunds",
    () =>
      /** @type {Refund[]} */ ([
        {
          id: "ref-1",
          flat: "B-402",
          society: "Sunrise Towers",
          order_label: "Box · Tue 6 Oct",
          why: "Short: methi, 2 judi (packer)",
          what_happened: "was short 2 judi methi on Tue 6 Oct",
          reported_by: "Packing, Kharghar hub",
          amount_paise: 4000,
          to: "wallet",
          wallet_before_paise: 41000,
          status: "pending",
        },
        {
          id: "ref-2",
          flat: "A-101",
          society: "Krishna Kunj",
          order_label: "Box · Tue 6 Oct",
          why: "Delivery failed: no one home after 2 tries",
          what_happened: "had a failed delivery on Tue 6 Oct (no one home after 2 tries)",
          reported_by: "Captain",
          amount_paise: 41000,
          to: "wallet",
          wallet_before_paise: 13000,
          status: "pending",
        },
        {
          id: "ref-3",
          flat: "A-1203",
          society: "Sunrise Towers",
          order_label: "Add-on · Tue 6 Oct",
          why: "Damaged: eggs, 6 piece",
          what_happened: "reported damaged eggs (6 piece) on Tue 6 Oct, with a photo",
          reported_by: "Customer, with photo",
          amount_paise: 4500,
          to: "wallet",
          wallet_before_paise: 8000,
          status: "pending",
        },
      ]),
  );

// ---------- Payouts ----------

/**
 * @typedef {{ id: string, name: string, masked_account: string, role: string, work: string, amount_paise: number,
 *   kyc: "verified" | "pending", status: "paid" | "failed" | "held", failure: string | null }} Payout
 */

const payouts = () =>
  mockStore(
    "payouts",
    () =>
      /** @type {Payout[]} */ ([
        {
          id: "po-lata",
          name: "Lata Shinde",
          masked_account: "••••7731",
          role: "Kitchen",
          work: "47 tiffins",
          amount_paise: 329000,
          kyc: "verified",
          status: "paid",
          failure: null,
        },
        {
          id: "po-kavita",
          name: "Kavita More",
          masked_account: "••••2208",
          role: "Packer",
          work: "3 packing shifts",
          amount_paise: 210000,
          kyc: "verified",
          status: "paid",
          failure: null,
        },
        {
          id: "po-meena",
          name: "Meena Jadhav",
          masked_account: "••••5190",
          role: "Society Captain",
          work: "3 delivery days · 128 boxes",
          amount_paise: 128000,
          kyc: "verified",
          status: "paid",
          failure: null,
        },
        {
          id: "po-arjun",
          name: "Arjun Pawar",
          masked_account: "••••0943",
          role: "Rider",
          work: "3 routes",
          amount_paise: 178000,
          kyc: "verified",
          status: "failed",
          failure: "name mismatch",
        },
      ]),
  );

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

  // GET /v1/admin/refunds?status=pending
  http.get("*/v1/admin/refunds", ({ request }) => {
    if (!allowed(request, ["admin", "hub_manager"]))
      return problem(403, "auth.forbidden", "Not allowed");
    const status = new URL(request.url).searchParams.get("status") ?? "pending";
    return HttpResponse.json({
      items: refunds().filter((r) => r.status === status),
      next_cursor: null,
    });
  }),

  // Approve pays the wallet at once (a journal entry); reject and reopen only change the review state.
  .../** @type {const} */ (["approve", "reject", "reopen"]).map((action) =>
    http.post(`*/v1/admin/refunds/:id/${action}`, ({ request, params }) =>
      idempotent(request, () => {
        if (!allowed(request, ["admin", "hub_manager"]))
          return problem(403, "auth.forbidden", "Not allowed");
        const r = refunds().find((x) => x.id === params.id);
        if (!r) return problem(404, "billing.refund_not_found", "No such refund");
        const from = action === "reopen" ? ["rejected"] : ["pending"];
        if (!from.includes(r.status))
          return problem(409, "billing.refund_already_decided", "This refund was already decided");
        r.status = action === "approve" ? "approved" : action === "reject" ? "rejected" : "pending";
        return HttpResponse.json(r);
      }),
    ),
  ),

  // GET /v1/admin/payouts?week= : the latest run unless a week is given.
  http.get("*/v1/admin/payouts", ({ request }) => {
    if (!allowed(request, ["admin"])) return problem(403, "auth.forbidden", "Admins only");
    const items = payouts();
    const sum = (/** @type {Payout["status"]} */ s) =>
      items.filter((p) => p.status === s).reduce((t, p) => t + p.amount_paise, 0);
    return HttpResponse.json({
      week_start: "2026-09-28",
      paid_on: "2026-10-05",
      next_run: "2026-10-12T10:00:00+05:30",
      test_mode: true,
      totals: {
        paid_paise: sum("paid"),
        paid_partners: items.filter((p) => p.status === "paid").length,
        held_paise: sum("held"),
        failed_paise: sum("failed"),
        failed_count: items.filter((p) => p.status === "failed").length,
      },
      items,
    });
  }),

  http.post("*/v1/admin/payouts/:id/retry", ({ request, params }) =>
    idempotent(request, () => {
      if (!allowed(request, ["admin"])) return problem(403, "auth.forbidden", "Admins only");
      const p = payouts().find((x) => x.id === params.id);
      if (!p) return problem(404, "billing.payout_not_found", "No such payout");
      if (p.status !== "failed")
        return problem(409, "billing.payout_not_failed", "Only a failed payout can be retried");
      p.status = "paid";
      p.failure = null;
      return HttpResponse.json(p);
    }),
  ),
];

// Money-side console mocks (M5): reconciliation and refunds review.
// Reconciliation: `GET /v1/admin/reconciliation` (the page's own shape, see `reconBody`) and the real
//   POST /v1/admin/reconciliation/{item_id}/resolve   body { note } (1 to 500 characters), admin, audit-logged;
//   it closes an open item and moves no money (a correction is a separate manual adjustment entry);
//   404 billing.recon_not_found, 409 billing.recon_not_open, 422 request.invalid.
// There is no "check now" (the Razorpay check is an hourly job) and no credit or investigate call.
// Refunds: GET /v1/admin/refunds (read-only: refunds are automatic, M5 §3.3).
// All money is integer paise. Nothing is edited or deleted: a fix would be a new ledger entry.
import { HttpResponse, http } from "msw";
import { STORY_NOW } from "./orders.js";
import { fieldError, idempotent, mockStore, mockUuid, problem, roleOf } from "./util.js";

/** @param {Request} request @param {("admin" | "hub_manager")[]} roles */
const allowed = (request, roles) => roles.includes(/** @type {any} */ (roleOf(request)));

// ---------- Reconciliation ----------

/**
 * @typedef {{ id: string, provider_ref: string, at: string, razorpay_paise: number, ledger_paise: number | null,
 *   kind: "no_wallet_entry" | "amount_differs" | "no_payment", payer: string, note: string }} Mismatch
 * An item the admin closed: what the real ReconItem keeps in `resolution`.
 * @typedef {{ note: string, at: string, by: string }} Resolution
 */

const recon = () =>
  mockStore("reconciliation", () => ({
    checked_at: "2026-10-05T11:00:00+05:30",
    matched: 128,
    /** @type {(Mismatch & { resolution: Resolution | null })[]} */
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
        resolution: null,
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
        resolution: null,
      },
    ],
  }));

/** The items still open, the way the page reads them (a resolved item leaves the list). */
const open = () =>
  recon()
    .items.filter((i) => !i.resolution)
    .map(({ resolution: _resolution, ...item }) => item);

const reconBody = () => {
  const r = recon();
  const items = open();
  const n = (/** @type {Mismatch["kind"]} */ k) => items.filter((i) => i.kind === k).length;
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
    items,
  };
};

const REAL_KIND = /** @type {const} */ ({
  no_wallet_entry: "captured_not_credited",
  amount_differs: "amount_mismatch",
  no_payment: "credited_not_captured",
});

/** The real ReconItem (M5 §3.4) of a stored item, with its `resolution`. @param {Mismatch & { resolution: Resolution | null }} m */
const reconItem = (m) => ({
  id: m.id,
  kind: REAL_KIND[m.kind],
  status: m.resolution ? "resolved" : "open",
  razorpay_ref: m.provider_ref,
  topup_id: null,
  refund_part_id: null,
  user_id: null,
  expected_paise: m.ledger_paise,
  actual_paise: m.razorpay_paise,
  first_seen_at: m.at,
  last_seen_at: m.at,
  resolution: m.resolution,
});

// ---------- Refunds ----------

/**
 * @typedef {{ id: string, flat: string, society: string, order_label: string, why: string,
 *   reported_by: string, amount_paise: number, to: "wallet",
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
          reported_by: "Packing, Kharghar hub",
          amount_paise: 4000,
          to: "wallet",
          status: "pending",
        },
        {
          id: "ref-2",
          flat: "A-101",
          society: "Krishna Kunj",
          order_label: "Box · Tue 6 Oct",
          why: "Delivery failed: no one home after 2 tries",
          reported_by: "Captain",
          amount_paise: 41000,
          to: "wallet",
          status: "pending",
        },
        {
          id: "ref-3",
          flat: "A-1203",
          society: "Sunrise Towers",
          order_label: "Add-on · Tue 6 Oct",
          why: "Damaged: eggs, 6 piece",
          reported_by: "Customer, with photo",
          amount_paise: 4500,
          to: "wallet",
          status: "pending",
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

  // POST /v1/admin/reconciliation/{item_id}/resolve (M5 §3.4, admin): close an open item with a note.
  // It moves no money. → 200 ReconItem with `resolution`; 404 billing.recon_not_found;
  // 409 billing.recon_not_open (already resolved); 422 request.invalid (note missing, blank or over 500).
  http.post("*/v1/admin/reconciliation/:id/resolve", async ({ request, params }) => {
    if (!allowed(request, ["admin"])) return problem(403, "auth.forbidden", "Admins only");
    const body = /** @type {{ note?: unknown } | null} */ (await request.json().catch(() => null));
    const note = typeof body?.note === "string" ? body.note : "";
    if (note.length < 1 || note.length > 500)
      return problem(422, "request.invalid", "Check the highlighted fields", {
        errors: [fieldError("note", "Write a note of 1 to 500 characters")],
      });
    const item = recon().items.find((x) => x.id === params.id);
    if (!item) return problem(404, "billing.recon_not_found", "No such item");
    if (item.resolution)
      return problem(409, "billing.recon_not_open", "This item is not open", {
        status: "resolved",
      });
    item.resolution = { note, at: new Date().toISOString(), by: mockUuid("a000", 1) };
    return HttpResponse.json(reconItem(item));
  }),

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
];

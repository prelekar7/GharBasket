// Money-side console mocks (M5): reconciliation and refunds review.
// Reconciliation: GET /v1/admin/reconciliation?status=&kind= → ReconPage { items, next_cursor, last_run }, and
//   POST /v1/admin/reconciliation/{item_id}/resolve   body { note } (1 to 500 characters), admin, audit-logged;
//   it closes an open item and moves no money (a correction is a separate manual adjustment entry);
//   404 billing.recon_not_found, 409 billing.recon_not_open, 422 request.invalid.
// There is no "check now" (the Razorpay check is an hourly job) and no credit or investigate call.
// Refunds: GET /v1/admin/refunds (read-only: refunds are automatic, M5 §3.3).
// All money is integer paise. Nothing is edited or deleted: a fix would be a new ledger entry.
import { HttpResponse, http } from "msw";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { SOCIETY_ID } from "./ids.js";
import { fieldError, mockStore, mockUuid, problem, roleOf } from "./util.js";

/** @param {Request} request @param {("admin" | "hub_manager")[]} roles */
const allowed = (request, roles) => roles.includes(/** @type {any} */ (roleOf(request)));

// ---------- Reconciliation ----------

/** @typedef {import("../app/(console)/(admin)/reconciliation/page.jsx").ReconItem} ReconItem */

const recon = () =>
  mockStore("reconciliation", () => ({
    /** @type {import("../app/(console)/(admin)/reconciliation/page.jsx").ReconRun} */
    last_run: {
      scheduled_for: "2026-10-05T11:17:00+05:30",
      status: "ok",
      checked: 130,
      healed: 1,
      opened: 2,
    },
    /** @type {ReconItem[]} newest first, like the API */
    items: [
      {
        id: "rec-2",
        kind: "amount_mismatch",
        status: "open",
        razorpay_ref: "pay_Nq3Zc8rWp5Yd07",
        topup_id: mockUuid("b910", 2),
        refund_part_id: null,
        user_id: mockUuid("b911", 2),
        expected_paise: 20000,
        actual_paise: 200000,
        first_seen_at: "2026-10-05T10:17:00+05:30",
        last_seen_at: "2026-10-05T11:17:00+05:30",
        resolution: null,
      },
      {
        id: "rec-1",
        kind: "captured_not_credited",
        status: "open",
        razorpay_ref: "pay_Nq4xK7mT2vLb91",
        topup_id: mockUuid("b910", 1),
        refund_part_id: null,
        user_id: mockUuid("b911", 1),
        expected_paise: null,
        actual_paise: 100000,
        first_seen_at: "2026-10-05T09:17:00+05:30",
        last_seen_at: "2026-10-05T11:17:00+05:30",
        resolution: null,
      },
    ],
  }));

const RECON_STATUSES = ["open", "healed", "resolved"];

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
  // GET /v1/admin/reconciliation?status=&kind= (M5 §3.4, admin): newest first, with the last run
  http.get("*/v1/admin/reconciliation", ({ request }) => {
    if (!allowed(request, ["admin"])) return problem(403, "auth.forbidden", "Admins only");
    const q = new URL(request.url).searchParams;
    const status = q.get("status");
    if (status && !RECON_STATUSES.includes(status))
      return problem(422, "request.invalid", "Check the highlighted fields", {
        errors: [fieldError("status", "Unknown status")],
      });
    const kind = q.get("kind");
    const r = recon();
    return HttpResponse.json({
      items: r.items.filter((i) => (!status || i.status === status) && (!kind || i.kind === kind)),
      next_cursor: null,
      last_run: r.last_run,
    });
  }),

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
    if (item.status !== "open")
      return problem(409, "billing.recon_not_open", "This item is not open", {
        status: item.status,
      });
    item.status = "resolved";
    item.resolution = { note, at: new Date().toISOString(), by: mockUuid("a000", 1) };
    return HttpResponse.json(item);
  }),

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

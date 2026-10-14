"use server";

import { ApiProblem, apiPost } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { fieldMessages } from "@/lib/problem-fields.js";

/** What the person is told for each failure; the `code` decides, never the server's text. @type {Record<string, string>} */
const MESSAGES = {
  "hub_ops.po_not_receivable":
    "This order can't take a receipt (it is not sent, or it is closed). Reload the page.",
  "hub_ops.po_not_found": "That purchase order is gone. Reload the page.",
  "hub_ops.vendor_not_found": "That vendor is not at this hub. Reload the page.",
  "hub_ops.vendor_inactive": "That vendor is inactive. Choose another one.",
  "hub_ops.product_not_stocked": "That item is not bought for stock.",
  "hub_ops.qty_invalid":
    "Use a quantity above 0 with up to 3 decimals (whole for pieces, bunches and packs).",
};
/** The `reason` of `hub_ops.receipt_invalid`. @type {Record<string, string>} */
const INVALID = {
  accepted_over_received: "You can't accept more than you received.",
  reject_reason_missing: "Say why part of it was sent back.",
  vendor_mismatch: "That vendor is not the one on the purchase order.",
  duplicate_product: "That item is on the receipt twice.",
  received_at: "The receiving time must be within the last 24 hours.",
  best_before: "The best-before must be after receiving and within a year.",
};

/**
 * Records one goods receipt (M6 §3.4): the weighed line of a sent purchase order, or a market buy
 * with no order. The receipt creates a stock lot when anything is accepted; what is not accepted goes
 * back to the vendor. A PO takes several receipts, so one line per save is fine.
 * @param {{ purchaseOrderId: string | null, vendorId: string | null, line: { product_id: string,
 *   received_qty: number, accepted_qty: number, grade: string, reject_reason?: string,
 *   unit_cost_paise?: number | null, note?: string } }} receipt
 * @param {string} idempotencyKey one per line being received, kept if the save is retried
 * @returns {Promise<{ ok: true, data: any } | { ok: false, message: string, fields: Record<string, string> }>}
 */
export async function saveReceipt({ purchaseOrderId, vendorId, line }, idempotencyKey) {
  const hub = await currentHub();
  try {
    const data = await apiPost(
      `/v1/hubs/${hub.id}/goods-receipts`,
      {
        purchase_order_id: purchaseOrderId,
        ...(vendorId && { vendor_id: vendorId }),
        lines: [line],
      },
      { idempotencyKey },
    );
    return { ok: true, data };
  } catch (e) {
    if (!(e instanceof ApiProblem)) throw e;
    const reason = e.extra.reason;
    return {
      ok: false,
      fields: fieldMessages(e.errors),
      message:
        (e.code === "hub_ops.receipt_invalid" && typeof reason === "string" && INVALID[reason]) ||
        MESSAGES[e.code] ||
        "That did not work. Please try again.",
    };
  }
}

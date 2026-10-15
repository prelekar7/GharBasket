"use server";

import { ApiProblem, apiPatch, apiPost } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { fieldMessages } from "@/lib/problem-fields.js";

/** What the person is told for each failure; the `code` decides, never the server's text. @type {Record<string, string>} */
const MESSAGES = {
  "hub_ops.plan_not_editable": "This plan is closed, so it can no longer change.",
  "hub_ops.plan_not_found": "That plan is no longer current. Reload the page.",
  "hub_ops.line_not_found": "That item is not on this plan. Reload the page.",
  "hub_ops.line_ordered": "This item is on a sent purchase order. Cancel that order first.",
  "hub_ops.reason_required": "Say why the quantity changed.",
  "hub_ops.qty_invalid":
    "Use a quantity from 0 with up to 3 decimals (whole for pieces, bunches and packs).",
  "hub_ops.vendor_inactive": "That vendor is inactive. Choose another one.",
  "hub_ops.vendor_not_found": "That vendor is not at this hub. Reload the page.",
  "hub_ops.vendor_missing": "Choose a vendor for every item you are ordering first.",
  "hub_ops.nothing_to_order": "Nothing is left to order.",
  "hub_ops.po_not_draft": "Only a draft purchase order can be sent.",
  "hub_ops.po_stale": "The plan changed after this order was made. Generate the orders again.",
  "hub_ops.po_not_cancellable": "This order already has goods received, so it can't be cancelled.",
  "hub_ops.po_not_found": "That purchase order is gone. Reload the page.",
  "request.version_mismatch": "Someone changed the plan. Reload the page and try again.",
  "hub_ops.date_out_of_range": "That day is outside the days the plan can be built for.",
};

/**
 * @template T
 * @param {() => Promise<T>} run
 * @returns {Promise<{ ok: true, data: T } | { ok: false, message: string, fields: Record<string, string>, productIds: string[] }>}
 */
async function attempt(run) {
  try {
    return { ok: true, data: await run() };
  } catch (e) {
    if (!(e instanceof ApiProblem)) throw e;
    const ids = e.extra.product_ids;
    return {
      ok: false,
      message: MESSAGES[e.code] ?? "That did not work. Please try again.",
      fields: fieldMessages(e.errors),
      productIds: Array.isArray(ids) ? ids : [],
    };
  }
}

/**
 * Changes one line of the plan (M6 §3.3). `If-Match` carries the plan's version; the answer is the
 * whole plan at its next version. An `order_qty` change needs a `reason`; `order_qty: null` goes back to
 * the suggestion.
 * @param {string} planId @param {number} version @param {string} productId
 * @param {{ order_qty?: number | null, vendor_id?: string | null, unit_cost_paise?: number | null, note?: string | null, reason?: string }} patch
 */
export async function editLine(planId, version, productId, patch) {
  return attempt(() =>
    apiPatch(`/v1/procurement-plans/${planId}/lines/${productId}`, patch, {
      headers: { "if-match": `"${version}"` },
    }),
  );
}

/** Builds or rebuilds the plan for a day (the 21:10 job, on demand). @param {string} date @param {string} idempotencyKey */
export async function buildPlan(date, idempotencyKey) {
  const hub = await currentHub();
  return attempt(() =>
    apiPost(`/v1/hubs/${hub.id}/procurement-plans/build`, { date }, { idempotencyKey }),
  );
}

/** Makes one draft purchase order per vendor. @param {string} planId @param {string} idempotencyKey one per press */
export async function generateOrders(planId, idempotencyKey) {
  return attempt(() =>
    apiPost(`/v1/procurement-plans/${planId}/purchase-orders`, undefined, { idempotencyKey }),
  );
}

/** Marks a draft purchase order as sent (the hub manager shares it herself). @param {string} id @param {string} idempotencyKey */
export async function sendOrder(id, idempotencyKey) {
  return attempt(() => apiPost(`/v1/purchase-orders/${id}/send`, undefined, { idempotencyKey }));
}

/** @param {string} id @param {string} reason @param {string} idempotencyKey */
export async function cancelOrder(id, reason, idempotencyKey) {
  return attempt(() => apiPost(`/v1/purchase-orders/${id}/cancel`, { reason }, { idempotencyKey }));
}

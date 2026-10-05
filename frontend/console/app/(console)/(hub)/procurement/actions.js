"use server";

import { ApiProblem, apiPatch, apiPost } from "@/lib/api.js";

/** @type {Record<string, string>} */
const MESSAGES = {
  "procurement.plan_sent": "The purchase orders are already sent, so quantities are locked.",
  "procurement.plan_not_found": "That plan is no longer current. Reload the page.",
  "procurement.line_not_found": "That item is not on this plan. Reload the page.",
  "request.invalid": "Use a whole number from 0 to 999.",
};

/**
 * @template T
 * @param {() => Promise<T>} run
 * @returns {Promise<{ ok: true, data: T } | { ok: false, message: string }>}
 */
async function attempt(run) {
  try {
    return { ok: true, data: await run() };
  } catch (e) {
    if (e instanceof ApiProblem)
      return { ok: false, message: MESSAGES[e.code] ?? "That did not work. Please try again." };
    throw e;
  }
}

/**
 * Changes what to order for one item. The API logs who changed it.
 * @param {string} planId @param {string} productId @param {number} orderQty
 */
export async function setOrderQty(planId, productId, orderQty) {
  return attempt(() =>
    apiPatch(`/v1/procurement-plans/${planId}/lines/${productId}`, { order_qty: orderQty }),
  );
}

/**
 * Makes and sends one purchase order per vendor. Sending it twice with the same key does nothing new.
 * @param {string} planId @param {string} idempotencyKey one per press of the confirm button
 */
export async function sendPurchaseOrders(planId, idempotencyKey) {
  return attempt(() =>
    apiPost(`/v1/procurement-plans/${planId}/purchase-orders`, {}, { idempotencyKey }),
  );
}

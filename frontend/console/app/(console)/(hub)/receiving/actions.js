"use server";

import { ApiProblem, apiPost } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";

/** @type {Record<string, string>} */
const MESSAGES = {
  "receiving.already_received": "This line was already received. Reload the page.",
  "receiving.line_not_found": "That item is not on today’s orders. Reload the page.",
  "request.invalid": "Check the quantity (a whole number) and the grade.",
};

/**
 * Records the goods receipt for one line: weight, grade and a photo if there is one. The receipt
 * makes stock lots with expiry and any shortage goes to packing.
 * @param {{ productId: string, receivedQty: number, grade: string, hasPhoto: boolean }} line
 * @param {string} idempotencyKey one per line being received, kept if the save is retried
 * @returns {Promise<{ ok: true, data: any } | { ok: false, message: string }>}
 */
export async function saveReceipt({ productId, receivedQty, grade, hasPhoto }, idempotencyKey) {
  const hub = await currentHub();
  try {
    const data = await apiPost(
      `/v1/hubs/${hub.id}/grn`,
      { product_id: productId, received_qty: receivedQty, grade, has_photo: hasPhoto },
      { idempotencyKey },
    );
    return { ok: true, data };
  } catch (e) {
    if (e instanceof ApiProblem)
      return { ok: false, message: MESSAGES[e.code] ?? "That did not work. Please try again." };
    throw e;
  }
}

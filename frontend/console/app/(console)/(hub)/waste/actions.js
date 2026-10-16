"use server";

import { ApiProblem, apiPost } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { fieldMessages } from "@/lib/problem-fields.js";

/** What the person is told for each failure; the `code` decides, never the server's text. @type {Record<string, string>} */
const MESSAGES = {
  "hub_ops.qty_invalid":
    "Use an amount above 0 with up to 3 decimals (whole for pieces, bunches and packs).",
  "hub_ops.qty_exceeds_stock": "That is more than the lot holds.",
  "hub_ops.offer_settled": "Someone already settled this offer. Reload the page.",
  "hub_ops.offer_not_found": "That offer is gone. Reload the page.",
  "hub_ops.taker_invalid": "Choose an active kitchen partner, or name the NGO.",
};

/**
 * @template T
 * @param {() => Promise<T>} run
 * @returns {Promise<{ ok: true, data: T } | { ok: false, message: string, fields: Record<string, string> }>}
 */
async function attempt(run) {
  try {
    return { ok: true, data: await run() };
  } catch (e) {
    if (!(e instanceof ApiProblem)) throw e;
    return {
      ok: false,
      fields: fieldMessages(e.errors),
      message: MESSAGES[e.code] ?? "That did not work. Please check the details and try again.",
    };
  }
}

/**
 * Writes off stock at the hub (M6 §3.5): spoiled, damaged, expired, pest or other. It goes to the bin.
 * @param {{ productId: string, qty: number, reason: string, note: string }} log
 * @param {string} idempotencyKey one per time the form is opened, kept if the save is retried
 */
export async function logWaste({ productId, qty, reason, note }, idempotencyKey) {
  const hub = await currentHub();
  return attempt(() =>
    apiPost(
      `/v1/hubs/${hub.id}/waste-logs`,
      { product_id: productId, qty, reason, ...(note.trim() && { note: note.trim() }) },
      { idempotencyKey },
    ),
  );
}

/**
 * Settles an open surplus offer after the phone call: taken (by a kitchen partner or an NGO) or declined.
 * What is not taken moves on down the cascade.
 * @param {string} offerId
 * @param {{ outcome: "taken" | "declined", qty?: number, taken_by?: { kind: "kitchen", partner_id: string } | { kind: "ngo", name: string }, note?: string }} body
 * @param {string} idempotencyKey
 */
export async function settleOffer(offerId, body, idempotencyKey) {
  return attempt(() => apiPost(`/v1/surplus-offers/${offerId}/settle`, body, { idempotencyKey }));
}

/** Runs the 12:30 cascade for this hub now (draw-down, close the day's buying, offers). @param {string} idempotencyKey */
export async function runCascade(idempotencyKey) {
  const hub = await currentHub();
  return attempt(() => apiPost(`/v1/hubs/${hub.id}/surplus/run`, undefined, { idempotencyKey }));
}

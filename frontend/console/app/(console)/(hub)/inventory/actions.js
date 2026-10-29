"use server";

import { ApiProblem, apiGet, apiPost } from "@/lib/api.js";
import { fieldMessages } from "@/lib/problem-fields.js";

/** What the person is told for each failure; the `code` decides, never the server's text. @type {Record<string, string>} */
const MESSAGES = {
  "hub_ops.lot_not_found": "That lot is gone. Reload the page.",
  "hub_ops.qty_invalid":
    "Use 0 or more with up to 3 decimals (whole for pieces, bunches and packs).",
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

/** A lot with its stock moves, oldest first (M6 §3.4). @param {string} lotId */
export async function loadLot(lotId) {
  return attempt(() => apiGet(`/v1/lots/${lotId}`));
}

/**
 * Sets a lot's stock after a physical count (M6 §3.4): writes a `count` move for the difference with the
 * reason, and the count is audited. The quantity is absolute and in the unit the lot uses.
 * @param {string} lotId @param {number} qtyOnHand @param {string} reason 3 to 200 characters
 * @param {string} idempotencyKey one per count, kept if the save is retried
 */
export async function countLot(lotId, qtyOnHand, reason, idempotencyKey) {
  return attempt(() =>
    apiPost(`/v1/lots/${lotId}/count`, { qty_on_hand: qtyOnHand, reason }, { idempotencyKey }),
  );
}

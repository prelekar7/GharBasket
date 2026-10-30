"use server";

import { ApiProblem, apiPost } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { fieldMessages } from "@/lib/problem-fields.js";

/** What the person is told for each failure; the `code` decides, never the server's text. @type {Record<string, string>} */
const MESSAGES = {
  "hub_ops.kept_item_not_found": "That item is gone. Reload the page.",
  "hub_ops.kept_item_settled":
    "That item was already packed or released, so it can't be marked lost. Reload the page.",
  "hub_ops.kept_item_moved": "The item moved to another box a moment ago. Please try again.",
  "orders.carry_not_refundable":
    "Its box is already packed, so it can't be refunded here. Reload the page.",
  "billing.wallet_limit": "The refund could not be paid back. Ask an admin to look at it.",
  "hub_ops.bag_unknown": "No packed bag of this hub has that label. Check the code and try again.",
  "hub_ops.bag_not_returned":
    "That bag is not back yet. After a first miss it waits for the next van; nothing is unpacked.",
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
 * A kept item is lost (M6 §3.5): M4 refunds the household at the item's paid price, no fee, and the
 * hub manager's note is audited. Works until the household's box is packed.
 * @param {string} itemId @param {string} note 1 to 500 characters
 * @param {string} idempotencyKey one per time the sheet is opened, kept if the save is retried
 */
export async function markLost(itemId, note, idempotencyKey) {
  const hub = await currentHub();
  return attempt(() =>
    apiPost(`/v1/hubs/${hub.id}/kept-items/${itemId}/lost`, { note }, { idempotencyKey }),
  );
}

/**
 * Unpacks a bag that came back after a second miss: the household's items become kept items, the rest
 * becomes grade C lots for the 12:30 run. Scanning the same bag again answers the same.
 * @param {string} bagQr the label's `GB1:…` code
 * @param {string} idempotencyKey one per bag being recorded
 */
export async function recordReturnedBag(bagQr, idempotencyKey) {
  const hub = await currentHub();
  return attempt(() =>
    apiPost(`/v1/hubs/${hub.id}/returned-bags`, { bag_qr: bagQr }, { idempotencyKey }),
  );
}

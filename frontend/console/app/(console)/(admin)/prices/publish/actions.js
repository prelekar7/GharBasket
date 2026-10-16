"use server";

import { ApiProblem, apiPost } from "@/lib/api.js";
import { describeProblem, todayIso } from "@/lib/catalog.js";
import { currentHub } from "@/lib/hub.js";
import { messagesOf } from "@/lib/problem-fields.js";

/**
 * @typedef {{ product_id: string, price_paise: number, mrp_paise: number | null }} PriceItem
 * @typedef {{ ok: true, status: "current" | "scheduled" }
 *   | { ok: false, message: string, details: string[], productIds: string[] }} PublishResult
 * `productIds` are the products a `catalog.price_above_mrp` or `catalog.product_unknown` problem names
 * (its `product_ids`), for the page to show by name.
 */

const MESSAGES = {
  "auth.forbidden": "Only an admin or the hub manager can publish prices.",
  "catalog.price_list_exists":
    "This hub already has a price list starting that day. Pick another date.",
  "catalog.valid_from_past": "That start date has passed. Pick today or a later date.",
  "catalog.price_above_mrp": "These products are priced above their MRP. Fix them in the file.",
  "catalog.product_unknown":
    "These products are not in the catalog or are switched off. Remove them from the file.",
  "request.idempotency_mismatch":
    "This publish was already sent with other prices. Press Publish again.",
};

/**
 * Publishes a price list for the selected hub (M2: POST /v1/hubs/{id}/price-lists, body
 * `{ valid_from, items }`, the whole list). A person starts it and confirms it in a Dialog; the
 * Idempotency-Key is made once per confirmed publish, so a retry cannot publish twice.
 * @param {string} validFrom ISO date the list starts
 * @param {PriceItem[]} items
 * @param {string} idempotencyKey
 * @returns {Promise<PublishResult>}
 */
export async function publishPriceList(validFrom, items, idempotencyKey) {
  const hub = await currentHub();
  try {
    await apiPost(
      `/v1/hubs/${hub.id}/price-lists`,
      { valid_from: validFrom, items },
      { idempotencyKey },
    );
    // The response has no status: a list that starts today is in use, a later one waits.
    return { ok: true, status: validFrom <= todayIso() ? "current" : "scheduled" };
  } catch (error) {
    if (!(error instanceof ApiProblem)) throw error;
    const { message } = describeProblem(error, MESSAGES);
    const ids = error.extra.product_ids;
    return {
      ok: false,
      message,
      details: messagesOf(error.errors),
      productIds: Array.isArray(ids) ? ids.map(String) : [],
    };
  }
}

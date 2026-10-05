"use server";

import { ApiProblem, apiPost } from "@/lib/api.js";
import { describeProblem } from "@/lib/catalog.js";
import { currentHub } from "@/lib/hub.js";

/**
 * @typedef {{ product_id: string, price_paise: number, mrp_paise?: number | null }} PriceLine
 * @typedef {{ ok: true, status: "current" | "scheduled" } | { ok: false, message: string, details: string[] }} PublishResult
 */

const MESSAGES = {
  "auth.forbidden": "Only an admin or the hub manager can publish prices.",
  "request.idempotency_conflict":
    "This publish was already sent with other prices. Press Publish again.",
};

/**
 * Publishes a price list for the selected hub (M2: POST /v1/hubs/{id}/price-lists). A person starts
 * it and confirms it in a Dialog; the Idempotency-Key is made once per confirmed publish, so a
 * retry cannot publish twice.
 * @param {string} validFrom ISO date the list starts
 * @param {PriceLine[]} lines
 * @param {string} idempotencyKey
 * @returns {Promise<PublishResult>}
 */
export async function publishPriceList(validFrom, lines, idempotencyKey) {
  const hub = await currentHub();
  try {
    const list = await apiPost(
      `/v1/hubs/${hub.id}/price-lists`,
      { valid_from: validFrom, lines },
      { idempotencyKey },
    );
    return { ok: true, status: list.status };
  } catch (error) {
    if (!(error instanceof ApiProblem)) throw error;
    const { message, fields } = describeProblem(error, MESSAGES);
    return { ok: false, message, details: Object.values(fields) };
  }
}

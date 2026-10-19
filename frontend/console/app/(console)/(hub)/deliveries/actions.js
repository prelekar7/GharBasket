"use server";

import { refresh } from "next/cache";
import { ApiProblem, apiPost } from "@/lib/api.js";
import { fieldErrors, humanMessage } from "@/lib/ops-errors.js";

/**
 * Settles an undelivered order (`POST /v1/deliveries/{order_id}/resolve`, M7 §3.4): redeliver it tomorrow (only after a
 * first miss) or refund it. A refund is the whole amount that is left; the note is kept with the decision.
 * @param {string} orderId
 * @param {string} idempotencyKey one per decision, made when the panel opens
 * @param {{ outcome: "redeliver" | "refund", note: string }} input
 * @returns {Promise<{ ok: true, status: string } | { ok: false, message: string, fields: Record<string, string> }>}
 */
export async function resolveDelivery(orderId, idempotencyKey, input) {
  try {
    const delivery = await apiPost(`/v1/deliveries/${encodeURIComponent(orderId)}/resolve`, input, {
      idempotencyKey,
    });
    refresh();
    return { ok: true, status: delivery.status };
  } catch (e) {
    if (e instanceof ApiProblem && e.code === "logistics.not_undelivered") refresh();
    const fields = fieldErrors(e);
    return {
      ok: false,
      fields,
      message: Object.keys(fields).length
        ? ""
        : humanMessage(e, {
            "logistics.not_undelivered":
              "Someone already settled this order. The list is up to date now.",
            "logistics.refund_invalid": "That is more than is left to refund.",
          }),
    };
  }
}

"use server";

import { ApiProblem, apiPost } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";

/**
 * Logs waste or surplus at the hub. A 422 comes back as field messages for the form.
 * @param {{ productId: string, qty: number, unit: string, reason: string, note: string }} log
 * @param {string} idempotencyKey one per time the form is opened, kept if the save is retried
 * @returns {Promise<{ ok: true, data: any } | { ok: false, message: string, fields: Record<string, string> }>}
 */
export async function logWaste({ productId, qty, unit, reason, note }, idempotencyKey) {
  const hub = await currentHub();
  try {
    const data = await apiPost(
      `/v1/hubs/${hub.id}/waste-logs`,
      { product_id: productId, qty, unit, reason, note },
      { idempotencyKey },
    );
    return { ok: true, data };
  } catch (e) {
    if (!(e instanceof ApiProblem)) throw e;
    /** @type {Record<string, string>} */
    const fields = {};
    for (const err of e.errors) if (err?.field) fields[err.field] = err.message;
    return {
      ok: false,
      fields,
      message: "That did not work. Please check the details and try again.",
    };
  }
}

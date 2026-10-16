"use server";

import { revalidatePath } from "next/cache";
import { apiPost } from "@/lib/api.js";
import { humanMessage } from "@/lib/ops-errors.js";

/**
 * @param {string} id
 * @param {"approve" | "reject" | "reopen"} action
 * @param {string} idempotencyKey one per user intent
 * @returns {Promise<{ ok: true } | { ok: false, message: string }>}
 */
export async function decideRefund(id, action, idempotencyKey) {
  try {
    await apiPost(`/v1/admin/refunds/${id}/${action}`, {}, { idempotencyKey });
    revalidatePath("/refunds");
    return { ok: true };
  } catch (error) {
    return {
      ok: false,
      message: humanMessage(error, {
        "billing.refund_already_decided":
          "Someone already decided this refund. The list is up to date now.",
      }),
    };
  }
}

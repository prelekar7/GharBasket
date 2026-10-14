"use server";

import { revalidatePath } from "next/cache";
import { apiPost } from "@/lib/api.js";
import { humanMessage } from "@/lib/ops-errors.js";

/**
 * Retries a held or failed partner payout once its reason is fixed (`POST /v1/admin/payouts/{id}/retry`, admin only). The
 * payout goes back to `pending`; the money stays in the partner's ledger earnings until it is paid.
 * @param {string} id
 * @param {string} idempotencyKey one per confirmation, so a double click retries once
 * @returns {Promise<{ ok: true } | { ok: false, message: string }>}
 */
export async function retryPayout(id, idempotencyKey) {
  try {
    await apiPost(`/v1/admin/payouts/${encodeURIComponent(id)}/retry`, {}, { idempotencyKey });
    revalidatePath("/payouts");
    return { ok: true };
  } catch (error) {
    return {
      ok: false,
      message: humanMessage(error, {
        "billing.payout_not_retryable":
          "That payout is no longer failed. The list is up to date now.",
      }),
    };
  }
}

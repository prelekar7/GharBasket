"use server";

import { revalidatePath } from "next/cache";
import { apiPost } from "@/lib/api.js";
import { humanMessage } from "@/lib/ops-errors.js";

/**
 * Retries a failed partner payout (`POST /v1/admin/payouts/{id}/retry`). The money comes from the
 * partner's ledger earnings, which stay put while the payout is failed.
 * @param {string} id
 * @param {string} idempotencyKey one per confirmation, so a double click pays once
 * @returns {Promise<{ ok: true } | { ok: false, message: string }>}
 */
export async function retryPayout(id, idempotencyKey) {
  try {
    await apiPost(`/v1/admin/payouts/${id}/retry`, {}, { idempotencyKey });
    revalidatePath("/payouts");
    return { ok: true };
  } catch (error) {
    return {
      ok: false,
      message: humanMessage(error, {
        "billing.payout_not_failed": "That payout is no longer failed. The list is up to date now.",
      }),
    };
  }
}

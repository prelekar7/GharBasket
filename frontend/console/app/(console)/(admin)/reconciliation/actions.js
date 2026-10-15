"use server";

import { revalidatePath } from "next/cache";
import { apiPost } from "@/lib/api.js";
import { humanMessage } from "@/lib/ops-errors.js";

/** @typedef {{ ok: true } | { ok: false, message: string }} Result */

/**
 * @param {string} path
 * @param {string} idempotencyKey
 * @returns {Promise<Result>}
 */
async function post(path, idempotencyKey) {
  try {
    await apiPost(path, {}, { idempotencyKey });
    revalidatePath("/reconciliation");
    return { ok: true };
  } catch (error) {
    return {
      ok: false,
      message: humanMessage(error, {
        "billing.mismatch_not_found": "Someone already resolved this. The list is up to date now.",
        "billing.not_creditable": "This one can't be fixed by a credit. Investigate it instead.",
      }),
    };
  }
}

/** Runs the hourly Razorpay check now. @param {string} key @returns {Promise<Result>} */
export async function checkNow(key) {
  return post("/v1/admin/reconciliation/check", key);
}

/**
 * Credits the wallet for a payment Razorpay captured but our ledger never saw. It posts a new,
 * correcting journal entry; nothing is edited or deleted.
 * @param {string} id @param {string} key @returns {Promise<Result>}
 */
export async function creditWallet(id, key) {
  return post(`/v1/admin/reconciliation/${id}/credit`, key);
}

/** @param {string} id @param {string} key @returns {Promise<Result>} */
export async function markInvestigating(id, key) {
  return post(`/v1/admin/reconciliation/${id}/investigate`, key);
}

"use server";

import { revalidatePath } from "next/cache";
import { apiPost } from "@/lib/api.js";
import { fieldErrors, humanMessage } from "@/lib/ops-errors.js";

/** @typedef {{ ok: true } | { ok: false, message: string, fields: Record<string, string> }} Result */

/**
 * Closes an open reconciliation item with a note: `POST /v1/admin/reconciliation/{item_id}/resolve`
 * with `{ note }` (M5 §3.4, admin; answers the ReconItem with its `resolution`, audit-logged). This is
 * the only action the API has. It moves no money: a wallet credit or any other money correction is a
 * separate, manual adjustment entry, and the note is where the admin says what was decided. There is no
 * Idempotency-Key; a second resolve of the same item is 409 `billing.recon_not_open`.
 * @param {string} id the item's id
 * @param {string} note 1 to 500 characters
 * @returns {Promise<Result>}
 */
export async function resolveMismatch(id, note) {
  try {
    await apiPost(`/v1/admin/reconciliation/${encodeURIComponent(id)}/resolve`, {
      note: note.trim(),
    });
    revalidatePath("/reconciliation");
    return { ok: true };
  } catch (error) {
    return {
      ok: false,
      message: humanMessage(error, {
        "billing.recon_not_found": "This item no longer exists. The list is up to date now.",
        "billing.recon_not_open": "Someone already closed this. The list is up to date now.",
        "request.invalid": "Write a note of 1 to 500 characters.",
      }),
      fields: fieldErrors(error),
    };
  }
}

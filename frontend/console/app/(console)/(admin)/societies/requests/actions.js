"use server";

import { revalidatePath } from "next/cache";
import { safely } from "@/lib/admin/safe.js";
import { apiPatch, apiPost } from "@/lib/api.js";

/**
 * "Add as prospect": the request becomes a prospect society.
 * @param {string} idempotencyKey one per click, so a retry cannot add it twice
 * @param {string} id the request
 */
export async function addAsProspect(idempotencyKey, id) {
  return safely(async () => {
    const { society } = await apiPost(
      `/v1/admin/society-requests/${encodeURIComponent(id)}/accept`,
      {},
      { idempotencyKey },
    );
    revalidatePath("/societies/requests");
    revalidatePath("/societies");
    return { societyId: /** @type {string} */ (society.id) };
  });
}

/**
 * Marks a request as a duplicate of a society we already list.
 * @param {string} idempotencyKey
 * @param {string} id the request
 * @param {string} societyId the society it repeats
 */
export async function markDuplicate(idempotencyKey, id, societyId) {
  return safely(async () => {
    await apiPost(
      `/v1/admin/society-requests/${encodeURIComponent(id)}/duplicate`,
      { society_id: societyId },
      { idempotencyKey },
    );
    revalidatePath("/societies/requests");
    return { id };
  });
}

/**
 * Undo of "Duplicate": the request goes back to New.
 * @param {string} id
 */
export async function reopenRequest(id) {
  return safely(async () => {
    await apiPatch(`/v1/admin/society-requests/${encodeURIComponent(id)}`, { status: "received" });
    revalidatePath("/societies/requests");
    return { id };
  });
}

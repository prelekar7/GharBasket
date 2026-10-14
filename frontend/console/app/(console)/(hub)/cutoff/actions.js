"use server";

import { revalidatePath } from "next/cache";
import { apiPost } from "@/lib/api.js";
import { humanMessage } from "@/lib/ops-errors.js";

/**
 * Re-runs the cut-off for a hub (M4 `POST /v1/hubs/{id}/cutoff/run`). Safe to repeat: it locks only
 * orders not yet locked and never charges twice. The key is made once per confirmation, so a retry of
 * the same click replays instead of running twice.
 * @param {string} hubId
 * @param {string} idempotencyKey
 * @returns {Promise<{ ok: true, newlyLocked: number, alreadyLocked: number } | { ok: false, message: string }>}
 */
export async function rerunCutoff(hubId, idempotencyKey) {
  try {
    const r = await apiPost(`/v1/hubs/${hubId}/cutoff/run`, {}, { idempotencyKey });
    revalidatePath("/cutoff");
    revalidatePath("/orders");
    return { ok: true, newlyLocked: r.newly_locked, alreadyLocked: r.already_locked };
  } catch (error) {
    return { ok: false, message: humanMessage(error) };
  }
}

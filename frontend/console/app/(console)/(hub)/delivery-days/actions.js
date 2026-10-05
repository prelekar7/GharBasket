"use server";

import { revalidatePath } from "next/cache";
import { apiPut } from "@/lib/api.js";
import { fieldErrors, humanMessage } from "@/lib/ops-errors.js";

/**
 * Saves one society's delivery days and slot (`PUT /v1/admin/societies/{id}/delivery-days`).
 * Applies from the next week Autopilot plans, never to a week already drafted.
 * @param {string} societyId
 * @param {{ slotId: string, weekdays: number[] }} value
 * @param {string} idempotencyKey one per save (an Undo is a new save with its own key)
 * @returns {Promise<{ ok: true } | { ok: false, message: string }>}
 */
export async function saveDeliveryDays(societyId, value, idempotencyKey) {
  try {
    await apiPut(
      `/v1/admin/societies/${societyId}/delivery-days`,
      { slot_id: value.slotId, weekdays: value.weekdays },
      { idempotencyKey },
    );
    revalidatePath("/delivery-days");
    return { ok: true };
  } catch (error) {
    return { ok: false, message: fieldErrors(error).weekdays ?? humanMessage(error) };
  }
}

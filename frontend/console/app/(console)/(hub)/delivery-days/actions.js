"use server";

import { revalidatePath } from "next/cache";
import { apiPut } from "@/lib/api.js";
import { fieldErrors, humanMessage } from "@/lib/ops-errors.js";

const MESSAGES = {
  "subscriptions.delivery_days_invalid":
    "Pick 1 to 3 different days, each with a slot that is switched on and carries boxes.",
  "subscriptions.society_unknown": "This society has no hub yet, so it can’t get delivery days.",
};

/**
 * Saves one society's delivery days (`PUT /v1/admin/societies/{id}/delivery-days`, body
 * `{ items: [{ weekday, slot_id }] }`, weekday 0 = Monday, 1 to 3 items). Applies from the next week
 * Autopilot plans, never to a week already drafted.
 * @param {string} societyId
 * @param {import("./page.jsx").Day[]} days
 * @param {string} idempotencyKey one per save (an Undo is a new save with its own key)
 * @returns {Promise<{ ok: true } | { ok: false, message: string }>}
 */
export async function saveDeliveryDays(societyId, days, idempotencyKey) {
  try {
    await apiPut(
      `/v1/admin/societies/${societyId}/delivery-days`,
      { items: days.map(({ weekday, slot_id }) => ({ weekday, slot_id })) },
      { idempotencyKey },
    );
    revalidatePath("/delivery-days");
    return { ok: true };
  } catch (error) {
    const fields = fieldErrors(error);
    return { ok: false, message: fields.items ?? humanMessage(error, MESSAGES) };
  }
}

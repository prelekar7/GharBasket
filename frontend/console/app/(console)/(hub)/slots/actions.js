"use server";

import { revalidatePath } from "next/cache";
import { apiPost, apiPut } from "@/lib/api.js";
import { fieldErrors, humanMessage } from "@/lib/ops-errors.js";

/**
 * @typedef {{ id?: string, hubId: string, start: string, end: string, capacity: number, serves?: string }} SlotInput
 * @typedef {{ ok: true, label: string } | { ok: false, message: string, fields: Record<string, string> }} SlotResult
 */

/**
 * Creates a slot (`POST /v1/admin/slots`, idempotent) or edits one (`PUT /v1/admin/slots/{id}`).
 * @param {SlotInput} input
 * @param {string} idempotencyKey one per open form, so a double tap creates one slot
 * @returns {Promise<SlotResult>}
 */
export async function saveSlot(input, idempotencyKey) {
  const body = {
    hub_id: input.hubId,
    start_time: input.start,
    end_time: input.end,
    capacity: input.capacity,
    serves: input.serves,
  };
  try {
    const slot = input.id
      ? await apiPut(`/v1/admin/slots/${input.id}`, body)
      : await apiPost("/v1/admin/slots", body, { idempotencyKey });
    revalidatePath("/slots");
    return { ok: true, label: slot.label };
  } catch (error) {
    return { ok: false, message: humanMessage(error), fields: fieldErrors(error) };
  }
}

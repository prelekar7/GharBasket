"use server";

import { revalidatePath } from "next/cache";
import { apiPatch, apiPost } from "@/lib/api.js";
import { fieldErrors, humanMessage } from "@/lib/ops-errors.js";

/**
 * @typedef {{ hubId: string, label: string, kind: "box" | "tiffin", start: string, end: string, capacity: number }} NewSlot
 * @typedef {{ capacity: number, active: boolean }} SlotChange
 * @typedef {{ ok: true, label: string } | { ok: false, message: string, fields: Record<string, string> }} SlotResult
 */

const MESSAGES = {
  "orders.slot_exists": "This hub already has a slot with that name. Pick another name.",
  "orders.slot_invalid": "The slot must end after it starts.",
};

/**
 * Creates a slot (`POST /v1/admin/slots`, idempotent): `{ hub_id, label, kind, start, end, capacity }`.
 * @param {NewSlot} input
 * @param {string} idempotencyKey one per open form, so a double tap creates one slot
 * @returns {Promise<SlotResult>}
 */
export async function createSlot(input, idempotencyKey) {
  const body = {
    hub_id: input.hubId,
    label: input.label,
    kind: input.kind,
    start: input.start,
    end: input.end,
    capacity: input.capacity,
  };
  try {
    const slot = await apiPost("/v1/admin/slots", body, { idempotencyKey });
    revalidatePath("/slots");
    return { ok: true, label: slot.label };
  } catch (error) {
    return { ok: false, message: humanMessage(error, MESSAGES), fields: fieldErrors(error) };
  }
}

/**
 * Changes a slot's capacity or switches it off (`PATCH /v1/admin/slots/{id}`, `{ capacity?, active? }`).
 * The times of a slot cannot change: add a new slot instead.
 * @param {string} id
 * @param {Partial<SlotChange>} change
 * @returns {Promise<SlotResult>}
 */
export async function changeSlot(id, change) {
  try {
    const slot = await apiPatch(`/v1/admin/slots/${id}`, change);
    revalidatePath("/slots");
    return { ok: true, label: slot.label };
  } catch (error) {
    return { ok: false, message: humanMessage(error, MESSAGES), fields: fieldErrors(error) };
  }
}

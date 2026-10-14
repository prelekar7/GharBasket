"use server";

import { revalidatePath } from "next/cache";
import { safely } from "@/lib/admin/safe.js";
import { apiPatch, apiPost } from "@/lib/api.js";

/**
 * @typedef {object} SocietyInput
 * @property {string} [name]
 * @property {string} [area]
 * @property {string | null} [hub_id]
 * @property {string} [status]
 * @property {number} [unlock_threshold]
 * @property {number | null} [flats]
 * @property {{ id?: string, label: string }[]} [towers]
 * @property {import("@/lib/admin/geo.js").GeoPolygon | null} [boundary]
 */

/**
 * Adds a society (a prospect unless the form says otherwise). One idempotency key per Save click.
 * @param {string} idempotencyKey
 * @param {SocietyInput} input
 */
export async function createSociety(idempotencyKey, input) {
  return safely(async () => {
    const society = await apiPost("/v1/admin/societies", input, { idempotencyKey });
    revalidatePath("/societies");
    return { id: /** @type {string} */ (society.id) };
  });
}

/**
 * Saves changes to a society: its details, or only its boundary.
 * @param {string} id
 * @param {SocietyInput} patch
 */
export async function updateSociety(id, patch) {
  return safely(async () => {
    await apiPatch(`/v1/admin/societies/${encodeURIComponent(id)}`, patch);
    revalidatePath("/societies");
    return { id };
  });
}

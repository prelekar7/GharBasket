"use server";

import { revalidatePath } from "next/cache";
import { safely } from "@/lib/admin/safe.js";
import { createBody, patchBody } from "@/lib/admin/societies.js";
import { apiPatch, apiPost } from "@/lib/api.js";

/**
 * Adds a society (M9 §3.1 as built). It is always a prospect: no `status` is sent. One idempotency key per
 * Save click. `lib/admin/societies.js` keeps the body to SocietyCreateIn.
 * @param {string} idempotencyKey
 * @param {{ name: string, area?: string, hub_id?: string, unlock_threshold?: number, flats?: number,
 *   towers?: { id?: string, label: string }[] }} input
 */
export async function createSociety(idempotencyKey, input) {
  return safely(async () => {
    const society = await apiPost("/v1/admin/societies", createBody(input), { idempotencyKey });
    revalidatePath("/societies");
    return { id: /** @type {string} */ (society.id) };
  });
}

/**
 * Saves changes to a society's unlock threshold, status, towers or boundary (M9 §3.6 as built; no
 * Idempotency-Key or If-Match). Never `name`, `area`, `hub_id`, `flats` or status "prospect": those are 422.
 * @param {string} id
 * @param {{ unlock_threshold?: number, unlock_threshold_pinned?: boolean, status?: "unlocking" | "paused" | "live",
 *   boundary?: import("@/lib/admin/geo.js").GeoPolygon | null, towers?: { id?: string, label: string }[] }} patch
 */
export async function updateSociety(id, patch) {
  return safely(async () => {
    await apiPatch(`/v1/admin/societies/${encodeURIComponent(id)}`, patchBody(patch));
    revalidatePath("/societies");
    return { id };
  });
}

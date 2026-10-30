"use server";

import { revalidatePath } from "next/cache";
import { safely } from "@/lib/admin/safe.js";
import { apiPatch, apiPost } from "@/lib/api.js";

/**
 * Adds a hub. It starts with no service area, until the area is drawn.
 * @param {string} idempotencyKey one per distinct request
 * @param {{ name: string, address: string }} input
 */
export async function createHub(idempotencyKey, input) {
  return safely(async () => {
    const hub = await apiPost("/v1/admin/hubs", input, { idempotencyKey });
    revalidatePath("/hubs");
    return { id: /** @type {string} */ (hub.id) };
  });
}

/**
 * Saves (or clears, with null) a hub's service area. `If-Match` carries the version the screen read;
 * a stale one is 412 and the person reloads.
 * @param {string} id
 * @param {import("@/lib/admin/geo.js").GeoPolygon | null} serviceArea
 * @param {number} version the hub's version
 */
export async function saveServiceArea(id, serviceArea, version) {
  return safely(async () => {
    const hub = await apiPatch(
      `/v1/admin/hubs/${encodeURIComponent(id)}`,
      { service_area: serviceArea },
      { headers: { "if-match": `"${version}"` } },
    );
    revalidatePath("/hubs");
    return { id, version: /** @type {number} */ (hub.version) };
  });
}

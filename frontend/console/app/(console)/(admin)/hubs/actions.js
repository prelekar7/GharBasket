"use server";

import { revalidatePath } from "next/cache";
import { safely } from "@/lib/admin/safe.js";
import { apiGet, apiPatch, apiPost } from "@/lib/api.js";

/**
 * Adds a hub. It starts as "planned", with no service area, until the area is drawn and the
 * franchise owner and manager are set.
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
 * Saves (or clears, with null) a hub's service area. The change needs `If-Match` (M6 §3.1), so the
 * hub's current version is read first.
 * @param {string} id
 * @param {import("@/lib/admin/geo.js").GeoPolygon | null} serviceArea
 */
export async function saveServiceArea(id, serviceArea) {
  return safely(async () => {
    const { version } = await apiGet(`/v1/hubs/${encodeURIComponent(id)}`);
    await apiPatch(
      `/v1/admin/hubs/${encodeURIComponent(id)}`,
      { service_area: serviceArea },
      { headers: { "if-match": `"${version}"` } },
    );
    revalidatePath("/hubs");
    return { id };
  });
}

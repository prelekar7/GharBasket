"use server";

import { apiGet } from "@/lib/api.js";

/**
 * The van's latest position, polled by the live map every 15 seconds. Returns null when it could not
 * be read, so the map keeps the last position instead of breaking.
 * @param {string} routeId
 * @returns {Promise<import("./data.js").VanPosition | null>}
 */
export async function vanPosition(routeId) {
  try {
    return await apiGet(`/v1/routes/${encodeURIComponent(routeId)}/position`);
  } catch {
    return null;
  }
}

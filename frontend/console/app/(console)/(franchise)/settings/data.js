import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";

/**
 * GET /v1/hubs/{id} (M6 §3.1, built; hub staff of that hub and admins read it). `version` is the ETag
 * the change must send back as `If-Match`. Money is integer paise; times are IST `HH:MM`.
 * @typedef {{ receiving: string, packing: string, dispatch: string, surplus: string }} Timings
 * @typedef {{ id: string, slug: string, name: string, status: "active" | "paused", address: string,
 *   timings: Timings, procurement_buffer_pct: number, pre_live_box_fee_paise: number,
 *   green_saving_share_pct: number, green_saving_max_paise: number, version: number }} Hub
 * The fields the page changes (PATCH /v1/admin/hubs/{id}, HubPatch); send only the ones that changed.
 * @typedef {Partial<Pick<Hub, "address" | "status" | "procurement_buffer_pct" | "pre_live_box_fee_paise">> & { timings?: Partial<Timings> }} HubChange
 */

/** @returns {Promise<Hub>} */
export async function loadHub() {
  const hub = await currentHub();
  return apiGet(`/v1/hubs/${hub.id}`);
}

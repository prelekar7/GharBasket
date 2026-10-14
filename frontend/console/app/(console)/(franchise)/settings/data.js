import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";

/**
 * GET /v1/hubs/{id}/settings (proposed; admin only, M6 `hubs:write`): the hub's delivery-day clock and
 * the switches that change what families see. Every PATCH is logged with who and when.
 * @typedef {{ ai_planner: boolean, auto_approve: boolean, tiffins: boolean, upi_autopay: boolean }} Switches
 * @typedef {"en" | "hi" | "mr"} Language
 * @typedef {"22-07" | "21-08" | "23-06" | "off"} QuietHours
 * @typedef {{ switches: Switches, languages: Language[], quiet_hours: QuietHours }} Editable
 * @typedef {Editable & { hub_id: string, hub_name: string,
 *   clock: { key: string, what: string, when: string, module: string }[],
 *   changes: { at: string, by: string, change: string }[] }} HubSettings
 */

/** @returns {Promise<HubSettings>} */
export async function loadSettings() {
  const hub = await currentHub();
  return apiGet(`/v1/hubs/${hub.id}/settings`);
}

import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";

/**
 * One person on the hub's team, employed ("staff") or a partner. No phone number: it is visible only
 * to the hub manager, on a person's profile.
 * @typedef {{ id: string, name: string, role: string, works_as: "staff" | "partner", society?: string,
 *   today: string, last_week_paise: number | null,
 *   status: "active" | "verified" | "fix_bank_details" | "invited" }} StaffMember
 */

/**
 * GET /v1/hubs/{hub}/staff (proposed).
 * @returns {Promise<StaffMember[]>}
 */
export async function loadStaff() {
  const hub = await currentHub();
  const { items } = await apiGet(`/v1/hubs/${hub.id}/staff`);
  return items;
}

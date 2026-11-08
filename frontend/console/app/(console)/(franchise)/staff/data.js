import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";

/**
 * One role grant on the hub's team (M1 section 3.6, HubStaff): staff, or a partner (kitchen, captain, rider,
 * packer). The phone is masked; hub managers see it in clear on M8's partner profile. An invited number has
 * no account yet, so no user id or name.
 * @typedef {{ grant_id: string, user_id: string | null, name: string | null, phone_masked: string,
 *   role: string, works_as: "staff" | "partner", society: { id: string, name: string } | null,
 *   status: "applied" | "kyc_pending" | "verified" | "suspended" | "invited", since: string | null }} StaffMember
 */

/**
 * GET /v1/hubs/{hub_id}/staff: by role, then name; at most 200 a page, and a hub's team fits in one.
 * @returns {Promise<StaffMember[]>}
 */
export async function loadStaff() {
  const hub = await currentHub();
  const { items } = await apiGet(`/v1/hubs/${hub.id}/staff?limit=200`);
  return items;
}

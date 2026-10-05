import { apiGet } from "@/lib/api.js";
import { getRole } from "@/lib/auth.js";
import { currentHub } from "@/lib/hub.js";

/**
 * Shape of GET /v1/hubs/{id}/fairness (a proposal; the numbers come from M12's `mv_partner_earnings_distribution`
 * and the weight from M10's fair assignment). Statistics are computed by the API, never in the browser.
 * @typedef {object} Kitchen
 * @property {string | null} partner_id  null for kitchens that are not partners in this hub yet
 * @property {string} name
 * @property {number} tiffins
 * @property {number} capacity
 * @property {number} earned_paise
 * @property {number} rating
 *
 * @typedef {object} Fairness
 * @property {string} week_start  ISO date of the Monday
 * @property {number} gini  0 is perfectly even
 * @property {number[]} gini_trend  recent weeks, oldest first
 * @property {number} gini_change  since the last week
 * @property {string} gini_change_note
 * @property {number} top_share_pct  the biggest kitchen's share of all tiffins
 * @property {number} top_share_change_pts
 * @property {number} kitchens_under_half  kitchens below half their capacity
 * @property {number} lambda  fairness weight of the nightly assignment, 0 to 1
 * @property {string | null} lambda_changed_by
 * @property {string | null} lambda_changed_at  ISO
 * @property {boolean} fallback_active  the assignment service is down and tiffins go to the nearest kitchen with space
 * @property {Kitchen[]} kitchens
 */

/**
 * @returns {Promise<{ fairness: Fairness, canEdit: boolean }>}
 */
export async function getFairness() {
  const [hub, role] = await Promise.all([currentHub(), getRole()]);
  const fairness = await apiGet(`/v1/hubs/${hub.id}/fairness`);
  return { fairness, canEdit: role === "admin" };
}

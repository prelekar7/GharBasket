import { apiGet } from "@/lib/api.js";
import { apiSource, parseSource } from "@/lib/data-source.js";
import { currentHub } from "@/lib/hub.js";

/**
 * What the page shows. The Gini and its trend come from M12 (`GET /v1/analytics/partner-earnings?kind=kitchen`,
 * section 3.5: `gini_bp` per week, null under 5 paid kitchens). The rest is the shape of
 * GET /v1/hubs/{id}/fairness, a proposal: no spec gives the per-kitchen table, the biggest kitchen's share or
 * the weight λ (M8 reads it from PARTNERS_FAIRNESS_LAMBDA; there is no way to change it in the console). Statistics are
 * computed by the API, never in the browser.
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
 * @property {number | null} gini  0 is perfectly even; null until 5 kitchens are paid
 * @property {number[]} gini_trend  recent weeks with a value, oldest first
 * @property {number | null} gini_change  since the last week with a value
 * @property {string} gini_change_note
 * @property {number} top_share_pct  the biggest kitchen's share of all tiffins
 * @property {number} top_share_change_pts
 * @property {number} kitchens_under_half  kitchens below half their capacity
 * @property {number} lambda  fairness weight of the nightly assignment (PARTNERS_FAIRNESS_LAMBDA, set offline), 0 to 1
 * @property {boolean} fallback_active  the assignment service is down and tiffins go to the nearest kitchen with space
 * @property {Kitchen[]} kitchens
 */

/**
 * @param {string | undefined} source "simulator" (default) or "pilot" (from the URL)
 * @returns {Promise<Fairness>}
 */
export async function getFairness(source) {
  const hub = await currentHub();
  const to = new Date(Date.now() + 5.5 * 3_600_000);
  const [proposed, earnings] = await Promise.all([
    apiGet(`/v1/hubs/${hub.id}/fairness`),
    apiGet("/v1/analytics/partner-earnings", {
      searchParams: {
        hub_id: hub.id,
        kind: "kitchen",
        from: new Date(to.getTime() - 84 * 86_400_000).toISOString().slice(0, 10),
        to: to.toISOString().slice(0, 10),
        data_source: apiSource(parseSource(source)),
      },
    }),
  ]);
  const ginis = /** @type {number[]} */ (
    earnings.weeks
      .map((/** @type {{ gini_bp: number | null }} */ w) => w.gini_bp)
      .filter((/** @type {number | null} */ bp) => bp !== null)
      .map((/** @type {number} */ bp) => bp / 10_000)
  );
  return {
    ...proposed,
    gini: ginis.at(-1) ?? null,
    gini_trend: ginis,
    gini_change:
      ginis.length > 1 ? Math.round(((ginis.at(-1) ?? 0) - (ginis.at(-2) ?? 0)) * 100) / 100 : null,
    gini_change_note: "vs last week",
  };
}

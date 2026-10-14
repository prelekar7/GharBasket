import { apiAll } from "@/lib/api-pages.js";
import { recentWeeks } from "@/lib/payout-weeks.js";

/**
 * @typedef {import("@/mocks/payouts.js").Payout} Payout
 */

/** How many past weeks the week picker offers. */
const WEEKS = 8;

/**
 * One week of the hub's partner payouts (`GET /v1/admin/payouts?week=&hub_id=`, docs/modules/M5-billing.md §3.5). The week
 * is an ISO week; the default is the one the latest Monday run paid.
 * @param {string} hubId
 * @param {string | undefined} asked  `?week=2026-W41`
 */
export async function getPayouts(hubId, asked) {
  const weeks = recentWeeks(Date.now(), WEEKS);
  const week = weeks.find((w) => w.week === asked) ?? weeks[0];
  const { items } = await apiAll("/v1/admin/payouts", { week: week.week, hub_id: hubId });
  return { week, weeks, items: /** @type {Payout[]} */ (items) };
}

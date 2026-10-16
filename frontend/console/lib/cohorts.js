import { mulDivRound } from "./money.js";

/**
 * Weekly retention cohorts (M12 US-4). A cohort is the households that joined in a week; `retention_bp[n-1]`
 * is the share still getting a box n weeks later in basis points, or null while that week has not
 * happened yet. Simulator and pilot cohorts are different data sources and are never mixed: every
 * function here takes the cohorts of one source.
 * @typedef {{ week: string, households: number, retention_bp: (number | null)[] }} Cohort
 */

/**
 * Average retention by weeks since joining, weighted by household count and counting only the cohorts
 * old enough to have a value for that week. Null when no cohort has reached that week.
 * @param {Cohort[]} cohorts
 * @param {number} weeks how many weeks to cover
 * @returns {(number | null)[]} basis points
 */
export function averageCurve(cohorts, weeks) {
  return Array.from({ length: weeks }, (_, i) => {
    const reached = cohorts.filter((c) => c.retention_bp[i] != null);
    const total = reached.reduce((n, c) => n + c.households, 0);
    if (total === 0) return null;
    const retained = reached.reduce((n, c) => n + c.households * (c.retention_bp[i] ?? 0), 0);
    return mulDivRound(retained, 1, total);
  });
}

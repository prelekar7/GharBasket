import { apiGet } from "@/lib/api.js";
import { apiSource, parseSource } from "@/lib/data-source.js";

/** How many weeks of retention the table shows (the API allows 4 to 26). */
export const WEEKS = 8;

/**
 * GET /v1/analytics/cohorts?data_source=&weeks= (M12 section 3.5, admin): weekly signup cohorts of one data
 * source. The simulator (`synthetic`) and the pilot (`real`) are separate; this page asks for one at a time.
 * `small` marks a cohort under 5 households.
 * @typedef {"synthetic" | "real"} DataSource
 * @typedef {{ data_source: DataSource, hub_id: string | null, weeks: number, as_of: string,
 *   cohorts: (import("@/lib/cohorts.js").Cohort & { small: boolean })[] }} CohortReport
 */

/**
 * @param {string | undefined} source "simulator" (default) or "pilot" (from the URL)
 * @returns {Promise<CohortReport>}
 */
export function loadCohorts(source) {
  return apiGet("/v1/analytics/cohorts", {
    searchParams: { data_source: apiSource(parseSource(source)), weeks: WEEKS },
  });
}

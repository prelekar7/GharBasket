import { apiGet } from "@/lib/api.js";

/**
 * GET /v1/analytics/cohorts?data_source= (M12 section 3): weekly signup cohorts of one data source.
 * The simulator and the pilot are separate; this page asks for exactly one at a time.
 * @typedef {"sim" | "pilot"} DataSource
 * @typedef {{ data_source: DataSource, weeks: number, cohorts: import("@/lib/cohorts.js").Cohort[] }} CohortReport
 */

/**
 * @param {string | undefined} source "sim" (default) or "pilot"
 * @returns {Promise<CohortReport>}
 */
export function loadCohorts(source) {
  return apiGet("/v1/analytics/cohorts", {
    searchParams: { data_source: source === "pilot" ? "pilot" : "sim" },
  });
}

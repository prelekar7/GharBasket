import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";

/**
 * What `GET /v1/hubs/{hub_id}/fairness` answers (M8 §3.8, backend/api/openapi.json `FairnessOut`): the weight
 * the nightly assignment uses and the hub's latest assignment runs, newest first. The statistics are the
 * API's own (each run's `summary`), never computed in the browser.
 * @typedef {object} FairnessRun
 * @property {string} run_id
 * @property {string} date  the delivery date the run assigned
 * @property {"lunch" | "dinner"} meal
 * @property {number} round
 * @property {"ml" | "fallback"} source
 * @property {string} model  e.g. "greedy-m8" when M8 assigned without the ML service
 * @property {string} status  M10's answer, or "fallback" for M8's own
 * @property {number} portions
 * @property {number} kitchens_used
 * @property {number} unassigned
 * @property {number | null} gini  of the earnings of every kitchen in the request
 * @property {number | null} gini_eligible  of the kitchens that could take an order
 * @property {number | null} km_per_portion
 * @property {number | null} lambda_paise  the λ the run sent; null for earlier runs
 * @property {string} at
 *
 * @typedef {object} Fairness
 * @property {string} hub_id
 * @property {number} lambda_paise  PARTNERS_FAIRNESS_LAMBDA, set offline: paise of cost per portion of earnings imbalance
 * @property {boolean} fallback_active  the newest run's model is greedy-m8
 * @property {FairnessRun[]} runs
 */

/** @returns {Promise<Fairness>} */
export async function getFairness() {
  const hub = await currentHub();
  return apiGet(`/v1/hubs/${hub.id}/fairness`, { searchParams: { runs: 28 } });
}

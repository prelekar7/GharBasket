// Intelligence mocks. AI health is M12 section 3.8's GET /v1/analytics/ai-health (admin; built on
// feat/M10-forecast-v1): every model's newest metrics per data source, drift per hub over 7 days, and how often
// each kind of prediction came from the ML service or the fallback. Its data sources are the built API's
// `synthetic | pilot | live | mixed`. Experiments and model promotions (`/v1/intel/experiments`) are our
// proposal: M12 cut `/v1/analytics/experiments/{key}` from S9 (nothing assigns experiments yet) and M10 names
// only the internal `GET /models`. Numbers are the seeded story: the Kharghar hub on Tue 6 Oct 2026, simulator
// data. The pilot has no data yet (ADR-0008, around March 2027).
import { HttpResponse, http } from "msw";
import { KHARGHAR_HUB_ID, TALOJA_HUB_ID } from "./hubs.js";
import { idempotent, mockStore, problem, roleOf } from "./util.js";

/**
 * @typedef {object} ModelRun One champion/challenger run (`intel.model_metrics` plus MLflow ids).
 * @property {string} id
 * @property {string} model
 * @property {string} metric
 * @property {"lower" | "higher"} better
 * @property {"pct" | "score"} unit
 * @property {number | null} champion null when there is no champion yet
 * @property {number} challenger
 * @property {"synthetic" | "pilot"} data_source
 * @property {"passed" | "not_better" | "needs_pilot" | "promoted"} gate
 * @property {string | null} promoted_at
 * @property {number} [previous_champion] the value to restore on rollback
 * @property {{ mlflow_run: string, snapshot: string, seed: number, holdout: string, trained: string, reproduce: string }} facts
 * @property {{ title: string, subtitle: string, max?: number }} chart
 *
 * @typedef {object} AbTest A product A/B test (M12 `GET /v1/analytics/experiments/{key}`).
 * @property {string} id
 * @property {string} name
 * @property {string} metric
 * @property {string} control
 * @property {string} variant
 * @property {number} households
 * @property {"synthetic" | "pilot"} data_source
 * @property {"too_early" | "variant_better" | "no_difference"} result
 */

/** @type {ModelRun[]} */
const seedRuns = [
  {
    id: "forecast",
    model: "Demand forecast",
    metric: "WAPE (lower)",
    better: "lower",
    unit: "pct",
    champion: 14.2,
    challenger: 12.9,
    data_source: "synthetic",
    gate: "passed",
    promoted_at: null,
    facts: {
      mlflow_run: "a41c7e90b2d34f6c",
      snapshot: "snap-2026-10-04-0200",
      seed: 42,
      holdout: "last 4 weeks, by time",
      trained: "Sun 4 Oct, 03:00",
      reproduce: "just eval",
    },
    chart: { title: "WAPE on the holdout", subtitle: "Lower is better · simulator", max: 20 },
  },
  {
    id: "ranker",
    model: "Next-basket ranker",
    metric: "Recall@20 (higher)",
    better: "higher",
    unit: "score",
    champion: 0.61,
    challenger: 0.6,
    data_source: "synthetic",
    gate: "not_better",
    promoted_at: null,
    facts: {
      mlflow_run: "7be20d15c9a84e31",
      snapshot: "snap-2026-10-04-0200",
      seed: 42,
      holdout: "last 4 weeks, by time",
      trained: "Sun 4 Oct, 03:00",
      reproduce: "just eval",
    },
    chart: { title: "Recall@20 on the holdout", subtitle: "Higher is better · simulator", max: 1 },
  },
  {
    id: "churn",
    model: "Churn risk",
    metric: "AUC (higher)",
    better: "higher",
    unit: "score",
    champion: null,
    challenger: 0.71,
    data_source: "synthetic",
    gate: "needs_pilot",
    promoted_at: null,
    facts: {
      mlflow_run: "d03f58a61e7b4c92",
      snapshot: "snap-2026-10-04-0200",
      seed: 42,
      holdout: "last 4 weeks, by time",
      trained: "Sun 4 Oct, 03:00",
      reproduce: "just eval",
    },
    chart: { title: "AUC on the holdout", subtitle: "Higher is better · simulator", max: 1 },
  },
];

/** @type {AbTest[]} */
const seedAb = [
  {
    id: "reason-chips",
    name: "Reason chips on box lines",
    metric: "Box lines kept as planned",
    control: "84%",
    variant: "87%",
    households: 58,
    data_source: "synthetic",
    result: "too_early",
  },
];

const state = () =>
  mockStore("intel", () => ({
    runs: /** @type {ModelRun[]} */ (structuredClone(seedRuns)),
    ab: /** @type {AbTest[]} */ (structuredClone(seedAb)),
  }));

/** @param {Request} request */
const adminOnly = (request) =>
  roleOf(request) === "admin"
    ? null
    : problem(403, "auth.forbidden", "Only admins can see model health");

const AT = "2026-10-06T03:00:00+05:30";

/**
 * @param {string} metric @param {number} value @param {string} split @param {string} version
 * @param {string | null} [hub]
 */
const metric = (metric, value, split, version, hub = null) => ({
  metric,
  value,
  split,
  data_source: "synthetic",
  version,
  computed_at: AT,
  hub_id: hub,
});

/** The health of the simulator week: Kharghar is steady, Taloja (new, small) drifted on two days. */
const AI_HEALTH = {
  generated_at: "2026-10-06T11:10:00+05:30",
  window_days: 7,
  models: [
    {
      model_name: "forecast_lightgbm",
      metrics: [
        metric("wape", 0.129, "holdout", "v12"),
        metric("wape", 0.131, "backtest", "v12", KHARGHAR_HUB_ID),
        metric("wape", 0.142, "live", "v12", KHARGHAR_HUB_ID),
        metric("wape", 0.17, "backtest", "v12", TALOJA_HUB_ID),
        metric("wape", 0.31, "live", "v12", TALOJA_HUB_ID),
      ],
      drift: true,
      hubs: [
        {
          hub_id: KHARGHAR_HUB_ID,
          data_source: "synthetic",
          drift: false,
          drifted_days: 0,
          scored_days: 7,
          for_date: "2026-10-05",
          wape: 0.142,
          backtest_wape: 0.131,
        },
        {
          hub_id: TALOJA_HUB_ID,
          data_source: "synthetic",
          drift: true,
          drifted_days: 2,
          scored_days: 5,
          for_date: "2026-10-05",
          wape: 0.31,
          backtest_wape: 0.17,
        },
      ],
    },
    {
      model_name: "ranker_lightgbm",
      metrics: [metric("ndcg_at_10", 0.61, "holdout", "v9")],
      drift: false,
      hubs: [],
    },
    {
      model_name: "planner_ml",
      metrics: [metric("acceptance", 0.91, "live", "v3")],
      drift: false,
      hubs: [],
    },
    {
      model_name: "planner_fallback",
      metrics: [metric("acceptance", 0.78, "live", "rules")],
      drift: false,
      hubs: [],
    },
  ],
  predictions: [
    { kind: "weekly_plan", source: "ml", count: 412 },
    { kind: "weekly_plan", source: "fallback", count: 9 },
    { kind: "forecast", source: "ml", count: 14 },
    { kind: "basket_rank", source: "ml", count: 398 },
    { kind: "basket_rank", source: "fallback", count: 3 },
    { kind: "route", source: "ml", count: 12 },
  ],
};

export const intelHandlers = [
  // GET /v1/analytics/ai-health (admin, no-store): no parameters; the page picks its source and hub.
  http.get("*/v1/analytics/ai-health", ({ request }) => {
    const role = roleOf(request);
    if (!role) return problem(401, "auth.unauthenticated", "Sign in again");
    if (role !== "admin") return problem(403, "auth.forbidden", "Only admins can see model health");
    return HttpResponse.json(AI_HEALTH, { headers: { "cache-control": "no-store" } });
  }),

  // Runs and tests of one data source; the two sources are never returned together.
  http.get("*/v1/intel/experiments", ({ request }) => {
    const denied = adminOnly(request);
    if (denied) return denied;
    const source =
      new URL(request.url).searchParams.get("data_source") === "real" ? "pilot" : "synthetic";
    const { runs, ab } = state();
    return HttpResponse.json({
      data_source: source,
      model_runs: runs.filter((r) => r.data_source === source),
      ab_tests: ab.filter((t) => t.data_source === source),
    });
  }),

  // Promote a challenger: only if it beat the champion on the time-based holdout (US-7).
  http.post("*/v1/intel/experiments/:id/promote", ({ request, params }) => {
    const denied = adminOnly(request);
    if (denied) return denied;
    return idempotent(request, () => {
      const run = state().runs.find((r) => r.id === params.id);
      if (!run) return problem(404, "intel.run_not_found", "No such run");
      if (run.gate === "promoted")
        return problem(409, "intel.already_champion", "This challenger is already the champion");
      if (run.gate !== "passed")
        return problem(409, "intel.gate_not_passed", "The challenger did not pass the gate");
      run.previous_champion = run.champion ?? undefined;
      run.champion = run.challenger;
      run.gate = "promoted";
      run.promoted_at = "2026-10-06T11:12:00+05:30";
      return HttpResponse.json(run);
    });
  }),

  // Move the MLflow alias back to the previous champion (the undo of a promotion).
  http.post("*/v1/intel/experiments/:id/rollback", ({ request, params }) => {
    const denied = adminOnly(request);
    if (denied) return denied;
    return idempotent(request, () => {
      const run = state().runs.find((r) => r.id === params.id);
      if (!run) return problem(404, "intel.run_not_found", "No such run");
      if (run.gate !== "promoted")
        return problem(409, "intel.not_promoted", "This run is not the champion");
      run.champion = run.previous_champion ?? null;
      run.previous_champion = undefined;
      run.gate = "passed";
      run.promoted_at = null;
      return HttpResponse.json(run);
    });
  }),
];

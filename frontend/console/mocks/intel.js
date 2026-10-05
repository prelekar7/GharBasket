// Intelligence mocks (M10 AI health, experiments and model promotions). Admin only: the spec gives
// `models:read` to the admin role. These paths are our proposal; M10 itself only names the internal
// `GET /models` and `GET /health/ai` that the API would call. Numbers are the seeded story: the
// Kharghar hub on Tue 6 Oct 2026, simulator data. The pilot has no data yet (ADR-0008, around March 2027).
import { HttpResponse, http } from "msw";
import { idempotent, mockStore, problem, roleOf } from "./util.js";

/**
 * @typedef {object} AiHealth GET /v1/intel/health?hub_id=&data_source=
 * @property {"synthetic" | "pilot"} data_source
 * @property {boolean} available false while the source has no data (the pilot before it starts)
 * @property {object} [kpis]
 * @property {number} kpis.wape_pct forecast error, lower is better
 * @property {number} kpis.wape_delta_pts change since last week, in points
 * @property {number[]} kpis.wape_trend
 * @property {number} kpis.kept_pct box lines the family neither edited nor swapped
 * @property {number} kpis.kept_delta_pts
 * @property {number} kpis.fallback_pct plans where the rule-based box was sent instead
 * @property {number} kpis.fallback_delta_pts
 * @property {string} kpis.fallback_note
 * @property {number} kpis.route_solve_s
 * @property {number} kpis.route_solve_limit_s
 * @property {{ item: string, unit: string, labels: string[], forecast_q50: number[], approved: number[], note: string }} [forecast_vs_actual]
 * @property {{ labels: string[], values: number[], threshold_pct: number }} [wape_by_week]
 * @property {{ key: string, name: string, does: string, live_version: string, metric: string, fallback: string, state: "healthy" | "fallback_up" | "degraded" }[]} [models]
 *
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

/** @type {AiHealth} */
const SIMULATOR_HEALTH = {
  data_source: "synthetic",
  available: true,
  kpis: {
    wape_pct: 14.2,
    wape_delta_pts: -1.1,
    wape_trend: [17.8, 16.4, 15.3, 14.2],
    kept_pct: 86,
    kept_delta_pts: 3,
    fallback_pct: 2.1,
    fallback_delta_pts: 0.8,
    fallback_note: "Sunday’s slow run",
    route_solve_s: 11,
    route_solve_limit_s: 30,
  },
  forecast_vs_actual: {
    item: "methi",
    unit: "bunches",
    labels: ["Tue 29", "Fri 2", "Sun 4", "Tue 6"],
    forecast_q50: [27, 29, 30, 31],
    approved: [26, 30, 29, 30],
    note: "The gap on Tue 6 Oct is the vendor short-delivery, not a forecast miss: families approved 30.",
  },
  wape_by_week: {
    labels: ["7 Sep", "14 Sep", "21 Sep", "28 Sep"],
    values: [17.8, 16.4, 15.3, 14.2],
    threshold_pct: 20,
  },
  models: [
    {
      key: "forecast",
      name: "Demand forecast",
      does: "Quantities with uncertainty per item",
      live_version: "v12 · retrained Sun 03:00",
      metric: "WAPE 14.2%",
      fallback: "Last 4 weeks’ average + buffer",
      state: "healthy",
    },
    {
      key: "ranker",
      name: "Next-basket ranker",
      does: "What a family is likely to need",
      live_version: "v9",
      metric: "Recall@20 0.61",
      fallback: "Last box repeated",
      state: "healthy",
    },
    {
      key: "planner",
      name: "Meal planner (CP-SAT)",
      does: "Builds the week within Budget Lock and food rules",
      live_version: "solver",
      metric: "Feasible 100%",
      fallback: "Rule-based box",
      state: "fallback_up",
    },
    {
      key: "routes",
      name: "Route solver (OR-Tools)",
      does: "Routes with time windows and pickups",
      live_version: "solver",
      metric: "Solve 11 s of 30",
      fallback: "Nearest-neighbour route",
      state: "healthy",
    },
    {
      key: "assignment",
      name: "Fair kitchen assignment",
      does: "Spreads tiffins across kitchens",
      live_version: "λ 0.3",
      metric: "Gini 0.14",
      fallback: "Nearest kitchen with space",
      state: "healthy",
    },
  ],
};

export const intelHandlers = [
  http.get("*/v1/intel/health", ({ request }) => {
    const denied = adminOnly(request);
    if (denied) return denied;
    const source = new URL(request.url).searchParams.get("data_source");
    return HttpResponse.json(
      source === "pilot" ? { data_source: "pilot", available: false } : SIMULATOR_HEALTH,
    );
  }),

  // Runs and tests of one data source; the two sources are never returned together.
  http.get("*/v1/intel/experiments", ({ request }) => {
    const denied = adminOnly(request);
    if (denied) return denied;
    const source =
      new URL(request.url).searchParams.get("data_source") === "pilot" ? "pilot" : "synthetic";
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

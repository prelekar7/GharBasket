// Intelligence mocks. AI health is M12 section 3.8's GET /v1/analytics/ai-health (admin; built on
// feat/M10-forecast-v1): every model's newest metrics per data source, drift per hub over 7 days, and how often
// each kind of prediction came from the ML service or the fallback. Its data sources are the built API's
// `synthetic | pilot | live | mixed`. Experiments are cut (M12), so there are no experiment mocks. Numbers are the seeded story: the Kharghar hub on Tue 6 Oct 2026, simulator
// data. The pilot has no data yet (ADR-0008, around March 2027).
import { HttpResponse, http } from "msw";
import { KHARGHAR_HUB_ID, TALOJA_HUB_ID } from "./hubs.js";
import { problem, roleOf } from "./util.js";

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
];

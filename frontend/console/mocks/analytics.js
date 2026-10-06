// Analytics mocks (M12): KPIs and weekly cohorts. Both paths are in the spec (section 3):
// GET /v1/analytics/kpis?hub_id=&from=&to= and GET /v1/analytics/cohorts?data_source=. Leaving `hub_id` out asks
// for all hubs together (our proposal): admins only; everyone else gets analytics.forbidden.
// The numbers are simulator output; the pilot has no data yet, so the pilot source answers empty.
// Simulator and pilot are separate sources and the handlers never merge them.
import { HttpResponse, http } from "msw";
import { KHARGHAR_HUB_ID, TALOJA_HUB_ID, visibleHubs } from "./hubs.js";
import { problem, roleOf } from "./util.js";

/** The last 12 delivery weeks (Mondays) up to the week of 28 Sep 2026, oldest first. */
const WEEKS = [
  "13 Jul",
  "20 Jul",
  "27 Jul",
  "3 Aug",
  "10 Aug",
  "17 Aug",
  "24 Aug",
  "31 Aug",
  "7 Sep",
  "14 Sep",
  "21 Sep",
  "28 Sep",
];

/** One week per entry, oldest first; Kharghar's last four are the figures of the KPI design. */
const KHARGHAR = {
  active_households: [24, 28, 31, 35, 38, 41, 45, 49, 52, 55, 58, 64],
  weeks_accepted_bp: [8100, 8200, 8300, 8400, 8450, 8550, 8600, 8650, 8700, 8800, 8900, 9100],
  waste_g_per_100kg: [4600, 4400, 4100, 3900, 3600, 3400, 3100, 2900, 2800, 2600, 2300, 1900],
  on_time_bp: [9300, 9400, 9400, 9500, 9500, 9600, 9600, 9600, 9600, 9700, 9700, 9700],
  partner_earnings_paise: [
    310_000, 360_000, 420_000, 470_000, 510_000, 540_000, 580_000, 610_000, 620_000, 690_000,
    733_000, 845_000,
  ],
};

/** Taloja opened later and is smaller, so its rates are lower and still improving. */
const TALOJA = {
  active_households: [6, 8, 9, 11, 13, 15, 17, 18, 20, 22, 24, 27],
  weeks_accepted_bp: [7600, 7700, 7800, 7900, 8000, 8100, 8200, 8250, 8300, 8350, 8400, 8500],
  waste_g_per_100kg: [6100, 5800, 5500, 5200, 4900, 4600, 4300, 4000, 3700, 3400, 3100, 2800],
  on_time_bp: [9000, 9100, 9100, 9200, 9200, 9300, 9300, 9400, 9400, 9400, 9500, 9500],
  partner_earnings_paise: [
    90_000, 110_000, 125_000, 140_000, 160_000, 175_000, 190_000, 210_000, 230_000, 250_000,
    265_000, 300_000,
  ],
};

/** @typedef {typeof KHARGHAR} Series */
/** @type {Record<string, Series>} */
const BY_HUB = { [KHARGHAR_HUB_ID]: KHARGHAR, [TALOJA_HUB_ID]: TALOJA };

/**
 * All hubs together: households and earnings add up; the rates are averaged with the hubs' active
 * households as weights (a bigger hub counts for more), rounded to whole basis points and to 100 g.
 * @returns {Series}
 */
function allHubs() {
  const hubs = Object.values(BY_HUB);
  const sum = (/** @type {number[][]} */ rows) =>
    rows[0].map((_, i) => rows.reduce((n, r) => n + r[i], 0));
  /**
   * @param {(s: Series) => number[]} pick
   * @param {number} step what the result is rounded to
   */
  const weighted = (pick, step = 1) =>
    KHARGHAR.active_households.map((_, i) => {
      const w = hubs.reduce((n, h) => n + h.active_households[i], 0);
      return (
        step *
        Math.round(hubs.reduce((n, h) => n + pick(h)[i] * h.active_households[i], 0) / w / step)
      );
    });
  return {
    active_households: sum(hubs.map((h) => h.active_households)),
    weeks_accepted_bp: weighted((h) => h.weeks_accepted_bp),
    waste_g_per_100kg: weighted((h) => h.waste_g_per_100kg, 100),
    on_time_bp: weighted((h) => h.on_time_bp),
    partner_earnings_paise: sum(hubs.map((h) => h.partner_earnings_paise)),
  };
}

/** @type {import("../lib/cohorts.js").Cohort[]} */
const SIMULATOR_COHORTS = [
  { week: "2026-09-07", households: 200, retention_bp: [9400, 8800, 8400, 8100] },
  { week: "2026-09-14", households: 200, retention_bp: [9500, 8900, 8500, null] },
  { week: "2026-09-21", households: 200, retention_bp: [9300, 8700, null, null] },
  { week: "2026-09-28", households: 200, retention_bp: [9600, null, null, null] },
];

/** @param {Request} request */
const signedOut = (request) =>
  roleOf(request) ? null : problem(401, "auth.unauthenticated", "Sign in again");

export const analyticsHandlers = [
  http.get("*/v1/analytics/kpis", ({ request }) => {
    const denied = signedOut(request);
    if (denied) return denied;
    const q = new URL(request.url).searchParams;
    const hubId = q.get("hub_id");
    if (hubId === null) {
      if (roleOf(request) !== "admin")
        return problem(403, "analytics.forbidden", "Only admins can see all hubs together");
    } else if (!BY_HUB[hubId]) {
      return problem(404, "hubs.not_found", "No such hub");
    } else if (!visibleHubs(request).some((h) => h.id === hubId)) {
      return problem(403, "analytics.forbidden", "You do not have access to this hub");
    }
    const series = hubId === null ? allHubs() : BY_HUB[hubId];
    const from = q.get("from");
    const to = q.get("to");
    // The window is `from`..`to`; the mock only uses its length, in whole weeks, ending at the latest week.
    const days = from && to ? (Date.parse(to) - Date.parse(from)) / 86_400_000 : 28;
    const count = Math.min(WEEKS.length, Math.max(2, Math.round(days / 7)));
    const last = /** @type {<T>(a: T[]) => T[]} */ ((a) => a.slice(-count));
    return HttpResponse.json({
      hub_id: hubId,
      data_source: "simulator",
      weeks: last(WEEKS),
      active_households: last(series.active_households),
      weeks_accepted_bp: last(series.weeks_accepted_bp),
      waste_g_per_100kg: last(series.waste_g_per_100kg),
      on_time_bp: last(series.on_time_bp),
      partner_earnings_paise: last(series.partner_earnings_paise),
    });
  }),

  http.get("*/v1/analytics/cohorts", ({ request }) => {
    if (roleOf(request) !== "admin") {
      return problem(403, "analytics.forbidden", "Cohorts are for admins");
    }
    const source = new URL(request.url).searchParams.get("data_source") ?? "sim";
    if (source !== "sim" && source !== "pilot") {
      return problem(422, "request.invalid", "data_source is sim or pilot");
    }
    return HttpResponse.json({
      data_source: source,
      weeks: 4,
      cohorts: source === "sim" ? SIMULATOR_COHORTS : [],
    });
  }),
];

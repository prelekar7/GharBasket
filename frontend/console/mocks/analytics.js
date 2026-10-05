// Analytics mocks (M12): KPIs and weekly cohorts. Both paths are in the spec (section 3):
// GET /v1/analytics/kpis?hub_id=&from=&to= and GET /v1/analytics/cohorts?data_source=.
// The numbers are simulator output; the pilot has no data yet, so the pilot source answers empty.
// Simulator and pilot are separate sources and the handlers never merge them.
import { HttpResponse, http } from "msw";
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

/** One week per entry, oldest first; the last four are the figures of the KPI design. */
const SERIES = {
  active_households: [24, 28, 31, 35, 38, 41, 45, 49, 52, 55, 58, 64],
  weeks_accepted_bp: [8100, 8200, 8300, 8400, 8450, 8550, 8600, 8650, 8700, 8800, 8900, 9100],
  waste_g_per_100kg: [4600, 4400, 4100, 3900, 3600, 3400, 3100, 2900, 2800, 2600, 2300, 1900],
  on_time_bp: [9300, 9400, 9400, 9500, 9500, 9600, 9600, 9600, 9600, 9700, 9700, 9700],
  partner_earnings_paise: [
    310_000, 360_000, 420_000, 470_000, 510_000, 540_000, 580_000, 610_000, 620_000, 690_000,
    733_000, 845_000,
  ],
};

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
    const from = q.get("from");
    const to = q.get("to");
    // The window is `from`..`to`; the mock only uses its length, in whole weeks, ending at the latest week.
    const days = from && to ? (Date.parse(to) - Date.parse(from)) / 86_400_000 : 28;
    const count = Math.min(WEEKS.length, Math.max(2, Math.round(days / 7)));
    const last = /** @type {<T>(a: T[]) => T[]} */ ((a) => a.slice(-count));
    return HttpResponse.json({
      hub_id: q.get("hub_id") ?? "kharghar",
      data_source: "simulator",
      weeks: last(WEEKS),
      active_households: last(SERIES.active_households),
      weeks_accepted_bp: last(SERIES.weeks_accepted_bp),
      waste_g_per_100kg: last(SERIES.waste_g_per_100kg),
      on_time_bp: last(SERIES.on_time_bp),
      partner_earnings_paise: last(SERIES.partner_earnings_paise),
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

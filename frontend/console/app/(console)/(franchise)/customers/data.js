import { apiGet } from "@/lib/api.js";
import { apiSource, parseSource } from "@/lib/data-source.js";
import { currentHub } from "@/lib/hub.js";
import { mulDivRound } from "@/lib/money.js";

/**
 * GET /v1/analytics/societies?hub_id=&data_source= (M12 section 3.3): the hub's households as counts per
 * society. Aggregates only; a franchise owner sees how the families are doing, not who they are. Rates are
 * basis points; a society that is not live yet has no rates (null).
 * @typedef {{ society_id: string, name: string, status: "live" | "unlocking" | "prospect" | "paused",
 *   joined: number, threshold: number, active_households: number, paused_households: number,
 *   approved_on_time_bp: number | null, average_box_paise: number | null }} SocietyRow
 * @typedef {{ hub_id: string, as_of: string, items: SocietyRow[] }} Societies
 *
 * What the page shows: the hub's totals, worked out from `items` here (the API sends no totals), and the
 * week's change in active households, from the KPI series.
 * @typedef {{ active_households: number, active_households_change: number | null, paused_households: number,
 *   approved_on_time_bp: number | null, joining_societies: string[], societies: SocietyRow[] }} CustomerSummary
 */

/**
 * @param {SocietyRow[]} items
 * @param {(number | null)[]} activeSeries weekly active households, oldest first
 * @returns {CustomerSummary}
 */
export function summarise(items, activeSeries) {
  const active = items.reduce((n, s) => n + s.active_households, 0);
  const rated = items.filter((s) => s.approved_on_time_bp != null && s.active_households > 0);
  const weight = rated.reduce((n, s) => n + s.active_households, 0);
  const latest = activeSeries[activeSeries.length - 1];
  const before = activeSeries[activeSeries.length - 2];
  return {
    active_households: active,
    active_households_change: latest != null && before != null ? latest - before : null,
    paused_households: items.reduce((n, s) => n + s.paused_households, 0),
    approved_on_time_bp:
      weight > 0
        ? mulDivRound(
            rated.reduce((n, s) => n + s.active_households * (s.approved_on_time_bp ?? 0), 0),
            1,
            weight,
          )
        : null,
    joining_societies: items.filter((s) => s.status === "unlocking").map((s) => s.name),
    societies: items,
  };
}

/**
 * @param {string | undefined} source "simulator" (default) or "pilot" (from the URL)
 * @returns {Promise<CustomerSummary>}
 */
export async function loadCustomers(source) {
  const hub = await currentHub();
  const data_source = apiSource(parseSource(source));
  const weeks = 14 * 86_400_000; // two weeks back, to have this week and the one before
  const to = new Date(Date.now() + 5.5 * 3_600_000);
  const [societies, kpis] = await Promise.all([
    apiGet("/v1/analytics/societies", { searchParams: { hub_id: hub.id, data_source } }),
    apiGet("/v1/analytics/kpis", {
      searchParams: {
        hub_id: hub.id,
        data_source,
        grain: "week",
        from: new Date(to.getTime() - weeks).toISOString().slice(0, 10),
        to: to.toISOString().slice(0, 10),
      },
    }),
  ]);
  return summarise(societies.items, kpis.series.active_households);
}

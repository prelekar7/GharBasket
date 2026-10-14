import { shortDate } from "../../../../lib/dates.js";
import { formatRupees, mulDivRound, toDecimalText } from "../../../../lib/money.js";

/**
 * GET /v1/analytics/kpis?hub_id=&from=&to=&grain=week&data_source= (M12 section 3.3): one integer per period in
 * `series.<key>`, oldest first, or null where the KPI is undefined. `periods` are the first days of the
 * weeks (ISO dates). Rates are basis points, waste is grams per 100 kg packed, money is paise.
 * @typedef {"active_households" | "weeks_accepted_bp" | "waste_g_per_100kg" | "on_time_bp" | "partner_earnings_paise"} KpiKey
 * @typedef {{ hub_id: string | null, data_source: "real" | "synthetic", grain: "day" | "week",
 *   as_of: string, stale: boolean, periods: string[],
 *   series: Record<KpiKey, (number | null)[]>, totals: Record<KpiKey, number | null> }} Kpis
 * @typedef {{ label: string, value: string, hint?: string, spark: number[],
 *   delta?: { value: string, direction: "up" | "down" | "flat", good?: boolean } }} Tile
 */

/**
 * @param {number} diff change since the previous week
 * @param {(abs: number) => string} text the size of the change, without sign
 * @param {boolean} [upIsGood]
 * @returns {Tile["delta"]}
 */
function delta(diff, text, upIsGood = true) {
  if (diff === 0) return { value: "no change", direction: "flat" };
  const up = diff > 0;
  return {
    value: `${up ? "+" : "−"}${text(Math.abs(diff))}`,
    direction: up ? "up" : "down",
    good: up === upIsGood,
  };
}

/**
 * The latest value and the one before it. Null when the latest week is undefined (the pilot has no data).
 * @param {(number | null)[]} a
 * @returns {[number, number] | null}
 */
function lastTwo(a) {
  const latest = a[a.length - 1];
  if (latest == null) return null;
  return [latest, a[a.length - 2] ?? latest];
}

/** A series for a sparkline or chart: an undefined week is drawn as zero. @param {(number | null)[]} a */
export const plotted = (a) => a.map((v) => v ?? 0);

/** 9100 bp → 91. @param {number} bp */
const wholePercent = (bp) => mulDivRound(bp, 1, 100);

/**
 * @param {string} label
 * @param {(number | null)[]} series
 * @param {(latest: number, previous: number) => Pick<Tile, "value" | "delta">} make
 * @param {string} [hint]
 * @returns {Tile}
 */
function tile(label, series, make, hint) {
  const two = lastTwo(series);
  return {
    label,
    ...(two ? make(two[0], two[1]) : { value: "—" }),
    spark: plotted(series),
    hint,
  };
}

/**
 * The five KPI tiles: the latest week's value, its change on the week before, and the trend over the
 * window. All figures are formatted from integers.
 * @param {Kpis} k
 * @returns {Tile[]}
 */
export function buildTiles(k) {
  const s = k.series;
  const week = k.periods.length
    ? `Week of ${shortDate(k.periods[k.periods.length - 1])}`
    : undefined;
  return [
    tile("Active households", s.active_households, (hh, prev) => ({
      value: String(hh),
      delta: delta(hh - prev, String),
    })),
    tile(
      "Weeks accepted",
      s.weeks_accepted_bp,
      (acc, prev) => ({
        value: `${wholePercent(acc)}%`,
        delta: delta(wholePercent(acc) - wholePercent(prev), (n) => `${n} pts`),
      }),
      "Approved or auto-approved",
    ),
    tile("Waste per 100 kg sold", s.waste_g_per_100kg, (waste, prev) => ({
      value: `${toDecimalText(waste, 3)} kg`,
      delta: delta(waste - prev, (n) => `${toDecimalText(n, 3)} kg`, false),
    })),
    tile(
      "Delivered on time",
      s.on_time_bp,
      (onTime, prev) => ({
        value: `${wholePercent(onTime)}%`,
        delta: delta(wholePercent(onTime) - wholePercent(prev), (n) => `${n} pts`),
      }),
      "Inside the slot",
    ),
    tile(
      "Partner earnings",
      s.partner_earnings_paise,
      (earn, prev) => ({ value: formatRupees(earn), delta: delta(earn - prev, formatRupees) }),
      week,
    ),
  ];
}

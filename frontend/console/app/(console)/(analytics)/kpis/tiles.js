import { formatRupees, mulDivRound, toDecimalText } from "../../../../lib/money.js";

/**
 * GET /v1/analytics/kpis?hub_id=&from=&to= (M12 section 3): one value per delivery week, oldest first.
 * `hub_id` is null for all hubs added up. Rates are basis points, waste is grams per 100 kg sold, money is paise.
 * @typedef {{ hub_id: string | null, data_source: "simulator" | "pilot", weeks: string[],
 *   active_households: number[], weeks_accepted_bp: number[], waste_g_per_100kg: number[],
 *   on_time_bp: number[], partner_earnings_paise: number[] }} Kpis
 * @typedef {{ label: string, value: string, hint?: string, spark: number[],
 *   delta: { value: string, direction: "up" | "down" | "flat", good?: boolean } }} Tile
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

/** @param {number[]} a @returns {[number, number]} the latest value and the one before it */
const lastTwo = (a) => [a[a.length - 1], a[a.length - 2] ?? a[a.length - 1]];

/** 9100 bp → 91. @param {number} bp */
const wholePercent = (bp) => mulDivRound(bp, 1, 100);

/**
 * The five KPI tiles: the latest week's value, its change on the week before, and the trend over the
 * window. All figures are formatted from integers.
 * @param {Kpis} k
 * @returns {Tile[]}
 */
export function buildTiles(k) {
  const [hh, hhPrev] = lastTwo(k.active_households);
  const [acc, accPrev] = lastTwo(k.weeks_accepted_bp);
  const [waste, wastePrev] = lastTwo(k.waste_g_per_100kg);
  const [onTime, onTimePrev] = lastTwo(k.on_time_bp);
  const [earn, earnPrev] = lastTwo(k.partner_earnings_paise);
  return [
    {
      label: "Active households",
      value: String(hh),
      delta: delta(hh - hhPrev, String),
      spark: k.active_households,
    },
    {
      label: "Weeks accepted",
      value: `${wholePercent(acc)}%`,
      delta: delta(wholePercent(acc) - wholePercent(accPrev), (n) => `${n} pts`),
      spark: k.weeks_accepted_bp,
      hint: "Approved or auto-approved",
    },
    {
      label: "Waste per 100 kg sold",
      value: `${toDecimalText(waste, 3)} kg`,
      delta: delta(waste - wastePrev, (n) => `${toDecimalText(n, 3)} kg`, false),
      spark: k.waste_g_per_100kg,
    },
    {
      label: "Delivered on time",
      value: `${wholePercent(onTime)}%`,
      delta: delta(wholePercent(onTime) - wholePercent(onTimePrev), (n) => `${n} pts`),
      spark: k.on_time_bp,
      hint: "Inside the slot",
    },
    {
      label: "Partner earnings",
      value: formatRupees(earn),
      delta: delta(earn - earnPrev, formatRupees),
      spark: k.partner_earnings_paise,
      hint: `Week of ${k.weeks[k.weeks.length - 1]}`,
    },
  ];
}

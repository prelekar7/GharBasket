/**
 * GET /v1/analytics/ai-health (M12 section 3.8, admin): every model's newest metrics, drift per hub over the
 * window, and how often each kind of prediction came from the ML service or the fallback. The API labels
 * data as `synthetic | pilot | live | mixed`; the console's "Simulator" is `synthetic` and "Pilot" is `pilot`
 * and `live`, never merged with the simulator (ADR-0008). WAPE is a fraction (0.142 is 14.2%).
 * @typedef {"synthetic" | "pilot" | "live" | "mixed"} Label
 * @typedef {{ metric: string, value: number, split: "train" | "holdout" | "backtest" | "live", data_source: Label,
 *   version: string, computed_at: string, hub_id: string | null }} Metric
 * @typedef {{ hub_id: string, data_source: Label, drift: boolean, drifted_days: number, scored_days: number,
 *   for_date: string | null, wape: number | null, backtest_wape: number | null }} HubDrift
 * @typedef {{ model_name: string, metrics: Metric[], drift: boolean, hubs: HubDrift[] }} Model
 * @typedef {{ generated_at: string, window_days: number, models: Model[],
 *   predictions: { kind: string, source: "ml" | "fallback", count: number }[] }} AiHealth
 */

/** @param {"simulator" | "pilot"} source @param {Label} label */
export const inSource = (source, label) =>
  source === "pilot" ? label === "pilot" || label === "live" : label === "synthetic";

/** What each family of models does and what answers when it is down. The API names models, not their jobs. */
const WORDS = /** @type {[string, { does: string, fallback: string }][]} */ ([
  [
    "forecast",
    { does: "Quantities with uncertainty per item", fallback: "Last 4 weeks’ average + buffer" },
  ],
  ["ranker", { does: "What a family is likely to need", fallback: "Last box repeated" }],
  ["basket", { does: "What a family is likely to need", fallback: "Last box repeated" }],
  [
    "planner",
    { does: "Builds the week within Budget Lock and food rules", fallback: "Rule-based box" },
  ],
  ["pantry", { does: "What is left in the family’s pantry", fallback: "Assume nothing is left" }],
  ["route", { does: "Routes with time windows and pickups", fallback: "Nearest-neighbour route" }],
  ["assign", { does: "Spreads tiffins across kitchens", fallback: "Nearest kitchen with space" }],
]);

/** "forecast_lightgbm" → "Forecast lightgbm". @param {string} name */
export const modelLabel = (name) => {
  const text = name.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
};

/** @param {string} name */
const wordsOf = (name) => WORDS.find(([prefix]) => name.startsWith(prefix))?.[1];

/** "0.142" → "14.2%" without a float: the value has at most four decimals. @param {number} fraction */
export const percentText = (fraction) => {
  const tenths = Math.round(fraction * 1000);
  return `${Math.floor(tenths / 10)}${tenths % 10 === 0 ? "" : `.${tenths % 10}`}%`;
};

/** A metric's number as the table shows it: rates (WAPE, acceptance, share) as percent, scores as they are. @param {Metric} m */
const metricText = (m) =>
  `${m.metric.replaceAll("_", " ")} ${["wape", "acceptance"].includes(m.metric) ? percentText(m.value) : String(Math.round(m.value * 1000) / 1000)}`;

/** The newest numbers of one source, live first, then holdout, then backtest. @param {Model} model @param {"simulator" | "pilot"} source */
function headline(model, source) {
  const order = ["live", "holdout", "backtest", "train"];
  const mine = model.metrics
    .filter((m) => inSource(source, m.data_source) && m.metric !== "drift")
    .sort((a, b) => order.indexOf(a.split) - order.indexOf(b.split));
  return mine[0]
    ? { text: `${metricText(mine[0])} (${mine[0].split})`, version: mine[0].version }
    : null;
}

/**
 * The rows of the models table for one source and hub.
 * @param {AiHealth} health @param {"simulator" | "pilot"} source @param {string} hubId
 */
export function modelRows(health, source, hubId) {
  return health.models.flatMap((m) => {
    const top = headline(m, source);
    if (!top) return [];
    const hub = m.hubs.find((h) => h.hub_id === hubId && inSource(source, h.data_source));
    const words = wordsOf(m.model_name);
    return [
      {
        key: m.model_name,
        name: modelLabel(m.model_name),
        does: words?.does ?? "—",
        fallback: words?.fallback ?? "—",
        version: top.version,
        metric: top.text,
        state: /** @type {"degraded" | "healthy"} */ (hub?.drift ? "degraded" : "healthy"),
      },
    ];
  });
}

/**
 * The four tiles for one source and hub, from the forecast model's drift row and the prediction counts.
 * @param {AiHealth} health @param {"simulator" | "pilot"} source @param {string} hubId
 */
export function tiles(health, source, hubId) {
  const forecast = health.models.find((m) => m.model_name.startsWith("forecast"));
  const row = forecast?.hubs.find((h) => h.hub_id === hubId && inSource(source, h.data_source));
  const accepted = health.models
    .find((m) => m.model_name === "planner_ml")
    ?.metrics.find(
      (m) => m.metric === "acceptance" && m.split === "live" && inSource(source, m.data_source),
    );
  const total = health.predictions.reduce((n, p) => n + p.count, 0);
  const fallback = health.predictions
    .filter((p) => p.source === "fallback")
    .reduce((n, p) => n + p.count, 0);
  return {
    wape: row?.wape ?? null,
    backtest_wape: row?.backtest_wape ?? null,
    wape_change_pts:
      row?.wape != null && row.backtest_wape != null
        ? Math.round((row.wape - row.backtest_wape) * 1000) / 10
        : null,
    drifted_days: row?.drifted_days ?? null,
    scored_days: row?.scored_days ?? null,
    acceptance: accepted?.value ?? null,
    fallback_share: total > 0 ? fallback / total : null,
    predictions: total,
  };
}

/**
 * ML against fallback answers per kind of prediction, for the bar chart. The API does not split these by data
 * source, so they cover every source.
 * @param {AiHealth} health
 */
export function predictionSeries(health) {
  const kinds = [...new Set(health.predictions.map((p) => p.kind))];
  const count = (/** @type {string} */ kind, /** @type {"ml" | "fallback"} */ source) =>
    health.predictions.find((p) => p.kind === kind && p.source === source)?.count ?? 0;
  return {
    labels: kinds.map((k) => modelLabel(k)),
    ml: kinds.map((k) => count(k, "ml")),
    fallback: kinds.map((k) => count(k, "fallback")),
  };
}

/**
 * Realised and backtest WAPE per hub of one source, in percent for the chart (the numbers in the text come
 * from `percentText`; only the plotted point is a float).
 * @param {AiHealth} health @param {"simulator" | "pilot"} source @param {Record<string, string>} names hub id → name
 */
export function hubWape(health, source, names) {
  const forecast = health.models.find((m) => m.model_name.startsWith("forecast"));
  const rows = (forecast?.hubs ?? []).filter(
    (h) => inSource(source, h.data_source) && h.wape != null,
  );
  return {
    labels: rows.map((h) => names[h.hub_id] ?? `Hub ${h.hub_id.slice(-4)}`),
    realised: rows.map((h) => Math.round((h.wape ?? 0) * 1000) / 10),
    backtest: rows.map((h) => Math.round((h.backtest_wape ?? 0) * 1000) / 10),
  };
}

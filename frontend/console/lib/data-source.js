/**
 * Simulator versus pilot data (ADR-0008). Every household and order carries a `data_source`, and the
 * console never mixes the two: a page shows one source at a time, chosen with the "Data source"
 * control (`?source=` in the URL, default the simulator). The API calls them `synthetic` and `pilot`.
 *
 * @typedef {"simulator" | "pilot"} DataSource
 */

/** @type {{ value: DataSource, label: string }[]} */
export const DATA_SOURCES = [
  { value: "simulator", label: "Household simulator" },
  { value: "pilot", label: "Pilot" },
];

/**
 * The source from a raw `?source=` value; anything unknown means the simulator.
 * @param {string | string[] | undefined} raw
 * @returns {DataSource}
 */
export function parseSource(raw) {
  return raw === "pilot" ? "pilot" : "simulator";
}

/**
 * The `data_source` value the API expects.
 * @param {DataSource} source
 */
export function apiSource(source) {
  return source === "pilot" ? "pilot" : "synthetic";
}

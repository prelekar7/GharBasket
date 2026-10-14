/**
 * Simulator versus pilot data (ADR-0008). Every household and order carries a `data_source`, and the
 * console never mixes the two: a page shows one source at a time, chosen with the "Data source"
 * control (`?source=` in the URL, default the simulator). The API calls them `synthetic` and `real` (M12 section 3.3; the console labels `real` "Pilot" until launch).
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
 * The `data_source` value the API expects: `synthetic` or `real`.
 * @param {DataSource} source
 * @returns {"synthetic" | "real"}
 */
export function apiSource(source) {
  return source === "pilot" ? "real" : "synthetic";
}

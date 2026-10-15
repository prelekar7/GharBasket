// Reading packing lines and bag codes the way the M6 API writes them (docs/modules/M6-hub-ops.md §3.2).

/** @typedef {import("./queries.js").PackLine} PackLine */
/** @typedef {import("./queries.js").PackingTask} PackingTask */

/** A line is done when the steps picked plus the steps refunded make the steps ordered. @param {PackLine} line */
export const isDone = (line) => line.picked_qty + line.short_qty >= line.qty;

/** todo, picked, or short (some steps refunded). @param {PackLine} line @returns {"todo" | "picked" | "short"} */
export const lineState = (line) =>
  !isDone(line) ? "todo" : line.short_qty > 0 ? "short" : "picked";

/** The product that goes in the bag: the substitute once the line is swapped. @param {PackLine} line */
export const inBag = (line) => line.substitute ?? line;

/** "250 g" for one step, "2 × 1 bunch" for more. @param {PackLine} line */
export const lineQty = (line) => {
  const { label } = inBag(line).step;
  return line.qty === 1 ? label : `${line.qty} × ${label}`;
};

/** Does a scanned or typed code name this line's product (or its substitute)? @param {PackLine} line @param {string} raw */
export const lineMatches = (line, raw) => {
  const code = raw.trim().toLowerCase();
  return [line, line.substitute].some((p) => p && (p.slug === code || p.product_id === code));
};

/** What is printed under the bag's QR: the end of "GB1:<order id>". @param {{ bag_qr: string }} task */
export const bagCode = (task) => task.bag_qr.slice(-6).toUpperCase();

/**
 * The bag a scanned code names: the whole "GB1:…" code, or the last characters printed under it
 * when they pick out one bag. @param {PackingTask[]} tasks @param {string} raw
 */
export function findBag(tasks, raw) {
  const code = raw.replace(/\s+/g, "").toLowerCase();
  const exact = tasks.find((t) => t.bag_qr.toLowerCase() === code);
  if (exact || code.length < 4) return exact;
  const ends = tasks.filter((t) => t.bag_qr.toLowerCase().endsWith(code));
  return ends.length === 1 ? ends[0] : undefined;
}

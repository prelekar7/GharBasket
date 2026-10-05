// Reading a price-list CSV and comparing it with the list in use (M2 US-3). Plain arithmetic on
// integer paise: a person publishes the list, nothing here picks or adjusts a price. The API checks
// everything again when the list is published; this is so people see what will change first.

/**
 * @typedef {object} Known a product the hub can price, with the price it has now
 * @property {string} id @property {string} name @property {string} step
 * @property {number | null} nowPaise @property {number | null} mrpPaise
 */
/**
 * @typedef {object} Line one row of the file
 * @property {number} row line number in the file, counting the header as 1
 * @property {string} id @property {number | null} pricePaise
 * @property {number | null | undefined} mrpPaise undefined when the file has no MRP column
 * @property {string} [problem] what is wrong with this row on its own
 */
/**
 * @typedef {object} Compared one row of the preview table
 * @property {string} id @property {string} name @property {string} step
 * @property {number | null} nowPaise @property {number | null} nextPaise
 * @property {number | null} mrpPaise the MRP the list would have
 * @property {number | null} pct whole-number change, null for a new product
 * @property {"changed" | "new" | "same" | "error"} state
 * @property {string} note
 */

/** A rise of at least this many percent gets the "Autopilot may swap it" note (proposal, not in the spec). */
export const BIG_JUMP_PCT = 25;

/**
 * Rupees typed as "110", "110.5" or "₹110.50" to integer paise, without floats. Null when it is not money.
 * @param {string} text
 */
export function rupeesToPaise(text) {
  const m = text.trim().match(/^₹?\s*(\d{1,7})(?:\.(\d{1,2}))?$/);
  return m ? Number(m[1]) * 100 + Number((m[2] ?? "").padEnd(2, "0")) : null;
}

/**
 * Splits CSV text into rows of cells (quotes and doubled quotes honoured; blank lines skipped).
 * @param {string} text
 */
function splitCsv(text) {
  /** @type {string[][]} */
  const rows = [];
  let row = /** @type {string[]} */ ([]);
  let cell = "";
  let quoted = false;
  const body = text.replace(/^﻿/, "");
  for (let i = 0; i < body.length; i++) {
    const c = body[i];
    if (quoted) {
      if (c === '"' && body[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (c === '"') quoted = false;
      else cell += c;
    } else if (c === '"') quoted = true;
    else if (c === ",") {
      row.push(cell);
      cell = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && body[i + 1] === "\n") i++;
      row.push(cell);
      cell = "";
      if (row.some((x) => x.trim())) rows.push(row);
      row = [];
    } else cell += c;
  }
  row.push(cell);
  if (row.some((x) => x.trim())) rows.push(row);
  return rows;
}

/**
 * Reads the file the hub manager uploads. Columns by header name: `product_id` (or product, slug, id),
 * `price` in rupees per step, and optionally `mrp` in rupees (blank means no MRP). Other columns, such
 * as the name and step of Download CSV, are ignored.
 * @param {string} text
 * @returns {{ lines: Line[], hasMrp: boolean, fileProblem?: string }}
 */
export function parseCsv(text) {
  const rows = splitCsv(text);
  if (rows.length < 2)
    return {
      lines: [],
      hasMrp: false,
      fileProblem: "The file has no price rows. Use a CSV with a header row.",
    };
  const head = rows[0].map((h) => h.trim().toLowerCase());
  const col = (/** @type {string[]} */ names) => head.findIndex((h) => names.includes(h));
  const idCol = col(["product_id", "product", "slug", "id"]);
  const priceCol = col(["price", "price_rupees", "price_inr"]);
  const mrpCol = col(["mrp", "mrp_rupees", "mrp_inr"]);
  if (idCol < 0 || priceCol < 0)
    return {
      lines: [],
      hasMrp: false,
      fileProblem: "The file needs a product_id column and a price column.",
    };
  const lines = rows.slice(1).map((cells, i) => {
    const id = (cells[idCol] ?? "").trim();
    const priceText = (cells[priceCol] ?? "").trim();
    const pricePaise = rupeesToPaise(priceText);
    const mrpText = mrpCol < 0 ? "" : (cells[mrpCol] ?? "").trim();
    const mrpPaise = mrpCol < 0 ? undefined : mrpText === "" ? null : rupeesToPaise(mrpText);
    /** @type {Line} */
    const line = { row: i + 2, id, pricePaise, mrpPaise };
    if (!id) line.problem = `Row ${i + 2} has no product.`;
    else if (pricePaise === null) line.problem = `${id}: “${priceText}” is not a price in rupees.`;
    else if (pricePaise < 1) line.problem = `${id}: the price must be above ₹0.`;
    else if (mrpPaise === null && mrpText !== "")
      line.problem = `${id}: “${mrpText}” is not an MRP in rupees.`;
    return line;
  });
  return { lines, hasMrp: mrpCol >= 0 };
}

/**
 * Compares the file with the products and what they cost now. `money` formats paise as rupees for
 * the messages. Rows are ordered errors first, then the biggest changes.
 * @param {Line[]} lines @param {Known[]} known @param {(paise: number) => string} money
 * @returns {{ rows: Compared[], same: number, untouched: number }}
 */
export function compare(lines, known, money) {
  const byId = new Map(known.map((k) => [k.id, k]));
  const seen = new Set();
  /** @type {Compared[]} */
  const rows = [];
  let same = 0;
  for (const l of lines) {
    const k = byId.get(l.id);
    const base = {
      id: l.id,
      name: k?.name ?? (l.id || "—"),
      step: k?.step ?? "—",
      nowPaise: k?.nowPaise ?? null,
    };
    /** @param {string} note */
    const fail = (note) =>
      rows.push({
        ...base,
        nextPaise: l.pricePaise,
        mrpPaise: null,
        pct: null,
        state: "error",
        note,
      });
    if (l.problem) fail(l.problem);
    else if (!k) fail(`${l.id} is not in the catalog.`);
    else if (seen.has(l.id)) fail(`${k.name} appears more than once.`);
    else {
      const next = /** @type {number} */ (l.pricePaise);
      const mrp = l.mrpPaise === undefined ? k.mrpPaise : l.mrpPaise;
      if (mrp != null && next > mrp)
        rows.push({
          ...base,
          nextPaise: next,
          mrpPaise: mrp,
          pct: k.nowPaise ? pct(k.nowPaise, next) : null,
          state: "error",
          note: `Above MRP ${money(mrp)}: fix to publish`,
        });
      else if (k.nowPaise === null)
        rows.push({
          ...base,
          nextPaise: next,
          mrpPaise: mrp,
          pct: null,
          state: "new",
          note: "New in the list",
        });
      else if (next === k.nowPaise && mrp === k.mrpPaise) same++;
      else {
        const change = pct(k.nowPaise, next);
        rows.push({
          ...base,
          nextPaise: next,
          mrpPaise: mrp,
          pct: change,
          state: "changed",
          note:
            change >= BIG_JUMP_PCT
              ? "Big jump: Autopilot may swap it"
              : mrp != null
                ? `MRP ${money(mrp)}`
                : "",
        });
      }
    }
    seen.add(l.id);
  }
  const rank = { error: 0, new: 1, changed: 1, same: 2 };
  rows.sort(
    (a, b) =>
      rank[a.state] - rank[b.state] ||
      Math.abs(b.pct ?? 0) - Math.abs(a.pct ?? 0) ||
      a.name.localeCompare(b.name),
  );
  const untouched = known.filter((k) => k.nowPaise !== null && !seen.has(k.id)).length;
  return { rows, same, untouched };
}

/** Whole-number percent change from `now` to `next`. @param {number} now @param {number} next */
function pct(now, next) {
  return Math.round(((next - now) / now) * 100);
}

/**
 * "+22%", "−5%", "0%": a minus sign, not a hyphen, and a sign you can read without colour.
 * @param {number | null} p
 */
export function formatPct(p) {
  if (p === null) return "New";
  return p > 0 ? `+${p}%` : p < 0 ? `−${Math.abs(p)}%` : "0%";
}

/**
 * The list in use as CSV text (the file Download CSV gives, and the one to edit and upload again).
 * @param {{ product_id: string, name: string, step: { qty: number, unit: string }, price_paise: number, mrp_paise: number | null }[]} items
 */
export function toCsv(items) {
  /** @param {number} paise */
  const rupees = (paise) =>
    paise % 100 === 0
      ? String(paise / 100)
      : `${Math.floor(paise / 100)}.${String(paise % 100).padStart(2, "0")}`;
  /** @param {string} s */
  const quote = (s) => (/[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
  const head = "product_id,name,step,price,mrp";
  const body = items.map((i) =>
    [
      i.product_id,
      quote(i.name),
      `${i.step.qty} ${i.step.unit}`,
      rupees(i.price_paise),
      i.mrp_paise == null ? "" : rupees(i.mrp_paise),
    ].join(","),
  );
  return `${[head, ...body].join("\n")}\n`;
}

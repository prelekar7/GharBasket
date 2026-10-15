/**
 * Saves rows as a CSV file in the browser (client only). Cells that start with an equals, plus, minus or at sign get a leading
 * quote so a spreadsheet never runs them as a formula.
 * @param {string} filename
 * @param {(string | number)[][]} rows the first row is the header
 */
export function downloadCsv(filename, rows) {
  const cell = (/** @type {string | number} */ v) => {
    const text = String(v);
    const safe = /^[=+\-@]/.test(text) && Number.isNaN(Number(text)) ? `'${text}` : text;
    return /[",\n]/.test(safe) ? `"${safe.replaceAll('"', '""')}"` : safe;
  };
  const blob = new Blob([`${rows.map((r) => r.map(cell).join(",")).join("\n")}\n`], {
    type: "text/csv;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

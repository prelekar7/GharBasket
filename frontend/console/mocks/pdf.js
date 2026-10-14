// A tiny valid PDF for mocked downloads, built the way frontend/mobile/src/mocks/pdf.js builds its one-line
// certificate (that file is an app of its own, so the console keeps its own copy, extended to several lines).

/**
 * Text safe for a PDF string literal in the built-in Helvetica font: backslashes and parentheses
 * escaped, anything outside printable ASCII replaced ("₹" becomes "Rs").
 * @param {string} text
 */
function pdfText(text) {
  return text
    .replaceAll("₹", "Rs ")
    .replace(/[^\x20-\x7e]/g, "-")
    .replace(/[\\()]/g, (c) => `\\${c}`);
}

/**
 * A one-page A4 PDF with a heading and lines of text, as bytes. The mocks of the report endpoints
 * answer with it so the download works without a server; the real API returns its own rendered report.
 * @param {string} title
 * @param {string[]} lines one per line, plain text
 * @returns {Uint8Array<ArrayBuffer>}
 */
export function mockPdf(title, lines) {
  const body = lines.map((l) => `T* (${pdfText(l)}) Tj`).join("\n");
  const stream = `BT /F1 20 Tf 48 780 Td 28 TL (${pdfText(title)}) Tj /F1 12 Tf 20 TL\n${body}\nET`;
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
  ];
  let pdf = "%PDF-1.4\n";
  const offsets = objects.map((text, i) => {
    const at = pdf.length;
    pdf += `${i + 1} 0 obj\n${text}\nendobj\n`;
    return at;
  });
  const xref = pdf.length;
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  pdf += offsets.map((o) => `${String(o).padStart(10, "0")} 00000 n \n`).join("");
  pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
  // Every character is ASCII by now, so one byte each.
  return Uint8Array.from(pdf, (c) => c.charCodeAt(0));
}

/**
 * The response of a report endpoint: the PDF when the caller accepts it, else 406.
 * @param {Request} request
 * @param {string} filename
 * @param {string} title
 * @param {string[]} lines
 */
export function pdfResponse(request, filename, title, lines) {
  const accept = request.headers.get("accept") ?? "";
  if (!accept.includes("application/pdf") && !accept.includes("*/*"))
    return new Response(null, { status: 406 });
  return new Response(mockPdf(title, lines), {
    headers: {
      "content-type": "application/pdf",
      "content-disposition": `attachment; filename="${filename}"`,
    },
  });
}
